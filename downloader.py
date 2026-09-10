#!/usr/bin/env python3
"""
=============================================================================
Advanced Google Drive Multi-Link Downloader
=============================================================================
Author: Antigravity Assistant
Description: High-performance, multi-threaded Google Drive file downloader
             supporting large file confirmation bypass, stream resumption,
             YAML batch configuration, and real-time progress visualization.
=============================================================================
"""

import os
import re
import sys
import time
import argparse
import urllib.parse
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import yaml

# Optional rich UI integration with graceful fallback
try:
    from rich.console import Console
    from rich.table import Table
    from rich.progress import (
        Progress,
        TextColumn,
        BarColumn,
        DownloadColumn,
        TransferSpeedColumn,
        TimeRemainingColumn,
        SpinnerColumn,
    )
    RICH_AVAILABLE = True
    console = Console()
except ImportError:
    RICH_AVAILABLE = False
    console = None


class GoogleDriveURLParser:
    """Extracts Google Drive file IDs from various URL formats."""

    PATTERNS = [
        # Standard sharing URL: https://drive.google.com/file/d/<ID>/view
        r"/file/d/([a-zA-Z0-9_-]+)",
        # Export / uc URL: https://drive.google.com/uc?id=<ID>
        r"[?&]id=([a-zA-Z0-9_-]+)",
        # Open URL: https://drive.google.com/open?id=<ID>
        r"/open\?id=([a-zA-Z0-9_-]+)",
        # Drive usercontent: https://drive.usercontent.google.com/download?id=<ID>
        r"drive\.usercontent\.google\.com/download\?id=([a-zA-Z0-9_-]+)",
        # Folders URL: https://drive.google.com/drive/folders/<ID>
        r"/folders/([a-zA-Z0-9_-]+)",
    ]

    @classmethod
    def extract_file_id(cls, url_or_id: str) -> Optional[str]:
        """Extract valid file ID from URL or raw ID string."""
        if not url_or_id:
            return None

        url_or_id = url_or_id.strip()

        # Check if the string is already a raw ID (alphanumeric, -, _, length 25-45)
        if re.fullmatch(r"[a-zA-Z0-9_-]{25,50}", url_or_id):
            return url_or_id

        for pattern in cls.PATTERNS:
            match = re.search(pattern, url_or_id)
            if match:
                return match.group(1)

        return None


class GoogleDriveDownloader:
    """
    Robust Google Drive file downloader supporting large files,
    stream resumption, virus scan confirmation bypass, and retry logic.
    """

    BASE_URL = "https://drive.google.com/uc?export=download"
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        output_dir: str = "./downloads",
        chunk_size_kb: int = 1024,
        timeout: int = 45,
        retry_attempts: int = 3,
        retry_delay: int = 2,
        overwrite: bool = False,
    ):
        self.output_dir = Path(output_dir)
        self.chunk_size = chunk_size_kb * 1024
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.overwrite = overwrite
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_session(self) -> requests.Session:
        """Create a configured HTTP session."""
        session = requests.Session()
        session.headers.update({"User-Agent": self.USER_AGENT})
        return session

    def _extract_filename_from_headers(self, headers: Dict[str, str]) -> Optional[str]:
        """Extract filename from HTTP Content-Disposition header."""
        cd = headers.get("content-disposition", "")
        if not cd:
            return None

        # Try RFC 5987 / UTF-8 filename*=UTF-8''...
        utf8_match = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE)
        if utf8_match:
            try:
                return urllib.parse.unquote(utf8_match.group(1))
            except Exception:
                pass

        # Try standard filename="..."
        name_match = re.search(r'filename="?([^";]+)"?', cd)
        if name_match:
            filename = name_match.group(1).strip()
            # Clean possible trailing quotes
            return filename.strip('"\'')

        return None

    def _parse_html_confirmation(self, html_text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse Google Drive large file warning HTML page.
        Returns (confirm_token, download_url, detected_title).
        """
        confirm_token = None
        download_url = None
        detected_title = None

        # Extract title if present: <title>filename - Google Drive</title>
        title_match = re.search(r"<title>(.*?)(?: - Google Drive)?</title>", html_text, re.IGNORECASE)
        if title_match:
            t = title_match.group(1).strip()
            if t and "Google Drive - Virus scan warning" not in t:
                detected_title = t

        # Pattern 1: Link href containing confirm=
        link_match = re.search(r'href="(/uc\?[^"]*confirm=[^"]*)"', html_text)
        if link_match:
            download_url = urllib.parse.urljoin("https://drive.google.com", link_match.group(1).replace("&amp;", "&"))
            token_match = re.search(r"confirm=([0-9A-Za-z_-]+)", download_url)
            if token_match:
                confirm_token = token_match.group(1)

        # Pattern 2: Form action for direct download
        if not download_url:
            form_match = re.search(r'<form[^>]+action="([^"]+)"[^>]*>', html_text)
            if form_match:
                download_url = form_match.group(1).replace("&amp;", "&")

        # Pattern 3: Direct confirmation token in query or input tag
        if not confirm_token:
            token_match = re.search(r'name="confirm" value="([^"]+)"', html_text)
            if token_match:
                confirm_token = token_match.group(1)

        if not confirm_token:
            token_match = re.search(r"confirm=([0-9A-Za-z_-]+)", html_text)
            if token_match:
                confirm_token = token_match.group(1)

        # Pattern 4: Usercontent download link
        if not download_url:
            user_content_match = re.search(r'href="(https://drive\.usercontent\.google\.com/download[^"]+)"', html_text)
            if user_content_match:
                download_url = user_content_match.group(1).replace("&amp;", "&")

        return confirm_token, download_url, detected_title

    def _prepare_download_stream(
        self, session: requests.Session, file_id: str, resume_offset: int = 0
    ) -> Tuple[requests.Response, Optional[str], int]:
        """
        Negotiate file download stream, resolving Google virus warnings and resume headers.
        Returns: (response_stream, resolved_filename, total_bytes)
        """
        headers = {}
        if resume_offset > 0:
            headers["Range"] = f"bytes={resume_offset}-"

        # Step 1: Initial request
        params = {"id": file_id, "export": "download"}
        resp = session.get(self.BASE_URL, params=params, headers=headers, stream=True, timeout=self.timeout)

        # Check if Google Drive returned virus confirmation HTML page
        content_type = resp.headers.get("content-type", "").lower()
        if "text/html" in content_type and resp.status_code == 200:
            html_text = resp.text
            confirm_token, download_url, html_title = self._parse_html_confirmation(html_text)

            # Look for confirmation cookies
            if not confirm_token:
                for k, v in session.cookies.items():
                    if k.startswith("download_warning"):
                        confirm_token = v
                        break

            # Step 2: Retry with confirmation
            if download_url:
                resp = session.get(download_url, headers=headers, stream=True, timeout=self.timeout)
            elif confirm_token:
                params["confirm"] = confirm_token
                resp = session.get(self.BASE_URL, params=params, headers=headers, stream=True, timeout=self.timeout)
            else:
                # Direct usercontent fallback
                usercontent_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
                resp = session.get(usercontent_url, headers=headers, stream=True, timeout=self.timeout)

        # Check for error responses
        if resp.status_code not in (200, 206):
            if resp.status_code == 404:
                raise FileNotFoundError("File not found or access denied (check permissions / link sharing).")
            elif resp.status_code == 403:
                raise PermissionError("Access forbidden. File download quota may have been exceeded or requires sign-in.")
            elif resp.status_code == 416:
                # Requested Range Not Satisfiable (file is already fully downloaded)
                return resp, None, 0
            else:
                raise RuntimeError(f"Server returned HTTP status code {resp.status_code}")

        # Determine filename
        filename = self._extract_filename_from_headers(resp.headers)

        # Determine total size
        total_size = 0
        if resp.status_code == 206:
            # Format: Content-Range: bytes 1024-2047/2048
            cr = resp.headers.get("content-range", "")
            if cr and "/" in cr:
                try:
                    total_size = int(cr.split("/")[-1])
                except ValueError:
                    pass
        if not total_size:
            content_length = resp.headers.get("content-length")
            if content_length:
                try:
                    total_size = int(content_length) + (resume_offset if resp.status_code == 206 else 0)
                except ValueError:
                    total_size = 0

        return resp, filename, total_size

    def download_file(
        self,
        url_or_id: str,
        custom_filename: Optional[str] = None,
        subdir: Optional[str] = None,
        progress_tracker: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Download a single Google Drive file with retry, resume, and progress tracking.
        """
        file_id = GoogleDriveURLParser.extract_file_id(url_or_id)
        if not file_id:
            return {
                "file_id": url_or_id,
                "filename": custom_filename or "Unknown",
                "status": "Failed",
                "error": "Invalid Google Drive link or ID format",
                "bytes_downloaded": 0,
                "duration": 0,
            }

        dest_dir = self.output_dir / (subdir or "")
        dest_dir.mkdir(parents=True, exist_ok=True)

        last_error = None
        start_time = time.time()

        for attempt in range(1, self.retry_attempts + 1):
            session = self._get_session()
            task_id = None

            try:
                # Temporary file probe for resume
                tentative_filename = custom_filename or f"gdrive_{file_id}.bin"
                temp_dest = dest_dir / tentative_filename
                part_file = dest_dir / f"{tentative_filename}.part"

                # Check if already fully downloaded
                if temp_dest.exists() and not self.overwrite:
                    # Let's verify with HEAD or quick probe
                    file_size = temp_dest.stat().st_size
                    if file_size > 0:
                        return {
                            "file_id": file_id,
                            "filename": temp_dest.name,
                            "path": str(temp_dest),
                            "status": "Skipped (Exists)",
                            "bytes_downloaded": file_size,
                            "duration": 0,
                        }

                resume_offset = 0
                if part_file.exists() and not self.overwrite:
                    resume_offset = part_file.stat().st_size

                # Negotiate connection & stream
                resp, remote_filename, total_size = self._prepare_download_stream(
                    session, file_id, resume_offset=resume_offset
                )

                # If server returns 416, part_file is already complete
                if resp.status_code == 416 and part_file.exists():
                    part_file.rename(temp_dest)
                    return {
                        "file_id": file_id,
                        "filename": temp_dest.name,
                        "path": str(temp_dest),
                        "status": "Completed (Resumed)",
                        "bytes_downloaded": temp_dest.stat().st_size,
                        "duration": time.time() - start_time,
                    }

                # Finalize filename
                final_name = custom_filename or remote_filename or tentative_filename
                # Sanitize filename for local OS
                final_name = re.sub(r'[\\/*?:"<>|]', "_", final_name)
                final_dest = dest_dir / final_name
                part_file = dest_dir / f"{final_name}.part"

                if resp.status_code == 206:
                    write_mode = "ab"
                    downloaded_bytes = resume_offset
                else:
                    write_mode = "wb"
                    downloaded_bytes = 0

                # Setup progress bar if tracker provided
                if progress_tracker:
                    task_id = progress_tracker.add_task(
                        description=f"[cyan]{final_name[:24]:<24}",
                        total=total_size if total_size > 0 else None,
                        completed=downloaded_bytes,
                    )

                # Write chunks to disk
                with open(part_file, write_mode) as f:
                    for chunk in resp.iter_content(chunk_size=self.chunk_size):
                        if chunk:
                            f.write(chunk)
                            chunk_len = len(chunk)
                            downloaded_bytes += chunk_len
                            if progress_tracker and task_id is not None:
                                progress_tracker.update(task_id, advance=chunk_len)

                # Complete download: rename .part to destination
                if part_file.exists():
                    if final_dest.exists():
                        final_dest.unlink()
                    part_file.rename(final_dest)

                if progress_tracker and task_id is not None:
                    progress_tracker.update(task_id, completed=downloaded_bytes)

                duration = time.time() - start_time
                status_msg = "Resumed" if resp.status_code == 206 else "Downloaded"

                return {
                    "file_id": file_id,
                    "filename": final_name,
                    "path": str(final_dest),
                    "status": status_msg,
                    "bytes_downloaded": downloaded_bytes,
                    "duration": duration,
                }

            except Exception as e:
                last_error = str(e)
                if progress_tracker and task_id is not None:
                    progress_tracker.remove_task(task_id)

                if attempt < self.retry_attempts:
                    time.sleep(self.retry_delay * attempt)
            finally:
                session.close()

        return {
            "file_id": file_id,
            "filename": custom_filename or "Unknown",
            "status": "Failed",
            "error": last_error or "Download failed after retries",
            "bytes_downloaded": 0,
            "duration": time.time() - start_time,
        }


def format_size(size_bytes: int) -> str:
    """Format bytes into human-readable string."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    size = float(size_bytes)
    while size >= 1024 and unit_idx < len(units) - 1:
        size /= 1024
        unit_idx += 1
    return f"{size:.2f} {units[unit_idx]}"


def print_summary_table(results: List[Dict[str, Any]]) -> None:
    """Render a clean summary table of download results."""
    total_bytes = sum(r.get("bytes_downloaded", 0) for r in results)
    total_time = sum(r.get("duration", 0) for r in results)
    success_count = sum(1 for r in results if "downloaded" in r.get("status", "").lower() or "completed" in r.get("status", "").lower() or "resumed" in r.get("status", "").lower())
    skip_count = sum(1 for r in results if "skipped" in r.get("status", "").lower())
    fail_count = sum(1 for r in results if r.get("status") == "Failed")

    if RICH_AVAILABLE and console:
        table = Table(title="Google Drive Download Summary", show_lines=True)
        table.add_column("File Name", style="bold white", overflow="ellipsis")
        table.add_column("File ID", style="dim cyan")
        table.add_column("Status", justify="center")
        table.add_column("Size", justify="right", style="green")
        table.add_column("Time", justify="right", style="yellow")
        table.add_column("Notes / Path", style="dim")

        for r in results:
            status = r.get("status", "Unknown")
            if "downloaded" in status.lower() or "resumed" in status.lower():
                status_formatted = f"[green]{status}[/green]"
            elif "skipped" in status.lower():
                status_formatted = f"[blue]{status}[/blue]"
            else:
                status_formatted = f"[red]{status}[/red]"

            notes = r.get("error") or r.get("path", "")
            table.add_row(
                str(r.get("filename", "N/A")),
                str(r.get("file_id", "N/A")),
                status_formatted,
                format_size(r.get("bytes_downloaded", 0)),
                f"{r.get('duration', 0):.1f}s",
                str(notes),
            )

        console.print()
        console.print(table)
        console.print(
            f"[bold]Total Downloads:[/bold] {len(results)} | "
            f"[bold green]Success:[/bold green] {success_count} | "
            f"[bold blue]Skipped:[/bold blue] {skip_count} | "
            f"[bold red]Failed:[/bold red] {fail_count} | "
            f"[bold magenta]Total Data:[/bold magenta] {format_size(total_bytes)}"
        )
    else:
        print("\n" + "=" * 70)
        print("GOOGLE DRIVE DOWNLOAD SUMMARY")
        print("=" * 70)
        for r in results:
            status = r.get("status")
            name = r.get("filename", "N/A")
            size = format_size(r.get("bytes_downloaded", 0))
            duration = f"{r.get('duration', 0):.1f}s"
            notes = r.get("error") or r.get("path", "")
            print(f"[{status}] {name} ({size}, {duration}) - {notes}")
        print("-" * 70)
        print(f"Total: {len(results)} | Success: {success_count} | Skipped: {skip_count} | Failed: {fail_count} | Total Data: {format_size(total_bytes)}")
        print("=" * 70)


def load_config_file(config_path: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Parse YAML configuration file."""
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    settings = data.get("settings", {})
    files = data.get("files", [])
    return settings, files


def main():
    parser = argparse.ArgumentParser(
        description="Advanced Google Drive Multi-Link Downloader",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to YAML configuration file (e.g. config.yml)",
    )
    parser.add_argument(
        "--urls", "-u",
        nargs="+",
        help="One or more Google Drive URLs or file IDs to download directly",
    )
    parser.add_argument(
        "--output", "-o",
        type=str,
        default=None,
        help="Output directory for downloaded files",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=None,
        help="Maximum concurrent worker threads",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=None,
        help="Stream chunk size in KB (default: 1024 KB)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite files if they already exist instead of skipping/resuming",
    )
    parser.add_argument(
        "--no-progress",
        action="store_true",
        help="Disable interactive rich progress bars (useful for CI/GitHub Actions)",
    )

    args = parser.parse_args()

    # Determine files and settings
    config_settings = {}
    config_files = []

    # Default to config.yml if it exists and no explicit arguments were supplied
    config_to_load = args.config
    if not config_to_load and not args.urls and Path("config.yml").exists():
        config_to_load = "config.yml"

    if config_to_load:
        config_settings, config_files = load_config_file(config_to_load)

    # CLI arguments override YAML settings
    output_dir = args.output or config_settings.get("output_dir", "./downloads")
    max_workers = args.workers or config_settings.get("max_workers", 3)
    chunk_size_kb = args.chunk_size or config_settings.get("chunk_size_kb", 1024)
    timeout = config_settings.get("timeout_seconds", 45)
    retry_attempts = config_settings.get("retry_attempts", 3)
    retry_delay = config_settings.get("retry_delay_seconds", 2)
    overwrite = args.overwrite or config_settings.get("overwrite", False)

    # Prepare file targets
    targets: List[Dict[str, Any]] = []

    if args.urls:
        for u in args.urls:
            targets.append({"url": u, "filename": None, "subdir": None})

    for item in config_files:
        if isinstance(item, str):
            targets.append({"url": item, "filename": None, "subdir": None})
        elif isinstance(item, dict) and "url" in item:
            targets.append({
                "url": item["url"],
                "filename": item.get("filename"),
                "subdir": item.get("subdir"),
            })

    if not targets:
        print("No Google Drive URLs or files specified!")
        print("Provide URLs via `--urls <link1> <link2>` or in a `config.yml` file.")
        parser.print_help()
        sys.exit(1)

    print(f"Starting Google Drive Downloader...")
    print(f"Target count: {len(targets)} | Workers: {max_workers} | Output: {output_dir}")

    downloader = GoogleDriveDownloader(
        output_dir=output_dir,
        chunk_size_kb=chunk_size_kb,
        timeout=timeout,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        overwrite=overwrite,
    )

    results: List[Dict[str, Any]] = []

    # Check if rich progress should be used
    use_rich_ui = RICH_AVAILABLE and not args.no_progress and sys.stdout.isatty()

    if use_rich_ui:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=None),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            transient=False,
        ) as progress:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                future_map = {
                    executor.submit(
                        downloader.download_file,
                        target["url"],
                        target["filename"],
                        target["subdir"],
                        progress,
                    ): target
                    for target in targets
                }

                for future in as_completed(future_map):
                    try:
                        res = future.result()
                        results.append(res)
                    except Exception as exc:
                        target = future_map[future]
                        results.append({
                            "file_id": target["url"],
                            "filename": target.get("filename", "Unknown"),
                            "status": "Failed",
                            "error": str(exc),
                            "bytes_downloaded": 0,
                            "duration": 0,
                        })
    else:
        # Non-interactive / headless fallback
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    downloader.download_file,
                    target["url"],
                    target["filename"],
                    target["subdir"],
                    None,
                ): target
                for target in targets
            }

            for future in as_completed(future_map):
                try:
                    res = future.result()
                    results.append(res)
                    print(f"[{res['status']}] {res.get('filename')} ({format_size(res.get('bytes_downloaded', 0))})")
                except Exception as exc:
                    target = future_map[future]
                    results.append({
                        "file_id": target["url"],
                        "filename": target.get("filename", "Unknown"),
                        "status": "Failed",
                        "error": str(exc),
                        "bytes_downloaded": 0,
                        "duration": 0,
                    })

    # Summary table
    print_summary_table(results)

    # Return exit code 1 if all downloads failed
    if results and all(r.get("status") == "Failed" for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
