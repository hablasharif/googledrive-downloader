#!/usr/bin/env python3
"""
=============================================================================
Advanced Google Drive Multi-Link & Folder Downloader
=============================================================================
Author: Antigravity Assistant
Description: High-performance, multi-threaded Google Drive file and folder
             downloader supporting large file confirmation bypass, stream
             resumption, folder tree extraction, batch URL lists, automatic
             redownload of failed URLs, persistent failure logging, YAML
             configuration, and real-time progress visualization.
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

# Try importing YAML parsers
try:
    import yaml
except ImportError:
    try:
        from ruamel import yaml
    except ImportError:
        yaml = None

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

# Optional gdown integration for folder extraction and robust download fallback
try:
    import gdown
    try:
        from gdown.download import get_url_from_gdrive_confirmation
    except ImportError:
        get_url_from_gdrive_confirmation = None
    GDOWN_AVAILABLE = True
except ImportError:
    GDOWN_AVAILABLE = False
    get_url_from_gdrive_confirmation = None

# Optional BeautifulSoup for robust HTML form parsing
try:
    import bs4
    BS4_AVAILABLE = True
except ImportError:
    BS4_AVAILABLE = False


class GoogleDriveURLParser:
    """Extracts Google Drive file or folder IDs and types from URLs."""

    FILE_PATTERNS = [
        # Standard sharing URL: https://drive.google.com/file/d/<ID>/view
        r"/file/d/([a-zA-Z0-9_-]+)",
        # Export / uc URL: https://drive.google.com/uc?id=<ID>
        r"[?&]id=([a-zA-Z0-9_-]+)",
        # Open URL: https://drive.google.com/open?id=<ID>
        r"/open\?id=([a-zA-Z0-9_-]+)",
        # Drive usercontent: https://drive.usercontent.google.com/download?id=<ID>
        r"drive\.usercontent\.google\.com/download\?id=([a-zA-Z0-9_-]+)",
    ]

    FOLDER_PATTERNS = [
        r"/drive/(?:u/\d+/)?folders/([a-zA-Z0-9_-]+)",
        r"/folders/([a-zA-Z0-9_-]+)",
    ]

    @classmethod
    def is_folder_url(cls, url_or_id: str) -> bool:
        """Determine whether URL points to a Google Drive folder."""
        if not url_or_id:
            return False
        for pattern in cls.FOLDER_PATTERNS:
            if re.search(pattern, url_or_id):
                return True
        return False

    @classmethod
    def extract_file_id(cls, url_or_id: str) -> Optional[str]:
        """Extract valid file ID from URL or raw ID string."""
        if not url_or_id:
            return None

        url_or_id = url_or_id.strip()

        # Check if the string is already a raw ID
        if re.fullmatch(r"[a-zA-Z0-9_-]{25,50}", url_or_id):
            return url_or_id

        for pattern in cls.FILE_PATTERNS:
            match = re.search(pattern, url_or_id)
            if match:
                return match.group(1)

        # Fallback to folder patterns if needed
        for pattern in cls.FOLDER_PATTERNS:
            match = re.search(pattern, url_or_id)
            if match:
                return match.group(1)

        return None

    @classmethod
    def extract_folder_items(cls, folder_url: str) -> List[Dict[str, Any]]:
        """
        Recursively extract all files inside a Google Drive folder,
        preserving the relative directory tree.
        """
        if not GDOWN_AVAILABLE:
            print("[Warning] `gdown` is required to crawl Google Drive folders.")
            print("Please install it: pip install gdown")
            return []

        try:
            items = gdown.download_folder(url=folder_url, skip_download=True, quiet=True)
            targets: List[Dict[str, Any]] = []
            seen_paths = set()

            for item in items:
                p = Path(item.path)
                subdir = str(p.parent) if p.parent != Path(".") else None
                filename = p.name

                target_key = f"{subdir}/{filename}"
                if target_key in seen_paths:
                    stem = p.stem
                    suffix = p.suffix
                    filename = f"{stem}_{item.id[:6]}{suffix}"
                    target_key = f"{subdir}/{filename}"
                seen_paths.add(target_key)

                targets.append({
                    "url": item.id,
                    "filename": filename,
                    "subdir": subdir,
                })
            return targets
        except Exception as e:
            print(f"[Error] Failed to crawl folder {folder_url}: {e}")
            return []


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
        engine: str = "auto",
    ):
        self.output_dir = Path(output_dir)
        self.chunk_size = chunk_size_kb * 1024
        self.timeout = timeout
        self.retry_attempts = retry_attempts
        self.retry_delay = retry_delay
        self.overwrite = overwrite
        self.engine = (engine or "auto").lower()
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _get_session(self) -> requests.Session:
        """Create a configured HTTP session with browser headers."""
        session = requests.Session()
        session.headers.update({
            "User-Agent": self.USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
        })
        return session

    def _extract_filename_from_headers(self, headers: Dict[str, str]) -> Optional[str]:
        """Extract filename from HTTP Content-Disposition header."""
        cd = headers.get("content-disposition", "")
        if not cd:
            return None

        utf8_match = re.search(r"filename\*=UTF-8''([^;]+)", cd, re.IGNORECASE)
        if utf8_match:
            try:
                return urllib.parse.unquote(utf8_match.group(1))
            except Exception:
                pass

        name_match = re.search(r'filename="?([^";]+)"?', cd)
        if name_match:
            filename = name_match.group(1).strip()
            return filename.strip('"\'')

        return None

    def _parse_html_confirmation(
        self, html_text: str, file_id: Optional[str] = None
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse Google Drive large file warning HTML page.
        Returns (confirm_token, download_url, detected_title).
        """
        confirm_token = None
        download_url = None
        detected_title = None

        # Check for title
        title_match = re.search(r"<title>(.*?)(?: - Google Drive)?</title>", html_text, re.IGNORECASE)
        if title_match:
            t = title_match.group(1).strip()
            if t and "Google Drive - Virus scan warning" not in t:
                detected_title = t

        # Also check for title/filename in warning text: e.g. <span class="uc-name-size"><a ...>filename</a>
        if not detected_title:
            name_match = re.search(r'<span[^>]*class=["\']uc-name-size["\'][^>]*><a[^>]*>([^<]+)</a>', html_text, re.IGNORECASE)
            if name_match:
                detected_title = name_match.group(1).strip()

        # Method 1: Use gdown's specialized confirmation resolver if available
        if GDOWN_AVAILABLE and get_url_from_gdrive_confirmation is not None:
            try:
                gdown_url = get_url_from_gdrive_confirmation(html_text)
                if gdown_url:
                    download_url = gdown_url
                    m_confirm = re.search(r"[?&]confirm=([0-9A-Za-z_-]+)", download_url)
                    if m_confirm:
                        confirm_token = m_confirm.group(1)
            except Exception:
                pass

        # Method 2: Parse #download-form using BeautifulSoup
        if not download_url and BS4_AVAILABLE:
            try:
                soup = bs4.BeautifulSoup(html_text, features="html.parser")
                form = soup.select_one("#download-form") or soup.find("form")
                if form and form.get("action"):
                    action = form["action"].replace("&amp;", "&")
                    url_components = urllib.parse.urlsplit(action)
                    query_params = urllib.parse.parse_qs(url_components.query)
                    for inp in form.find_all("input"):
                        name = inp.get("name")
                        val = inp.get("value", "")
                        if name:
                            query_params[name] = [val]
                            if name == "confirm":
                                confirm_token = val
                    query = urllib.parse.urlencode(query_params, doseq=True)
                    download_url = urllib.parse.urlunsplit(url_components._replace(query=query))
            except Exception:
                pass

        # Method 3: Pure-Python regex parser for form action and input tags
        if not download_url:
            form_match = re.search(
                r'<form[^>]*id=["\']download-form["\'][^>]*action=["\']([^"\']+)["\'][^>]*>(.*?)</form>',
                html_text,
                re.DOTALL | re.IGNORECASE,
            )
            if not form_match:
                form_match = re.search(
                    r'<form[^>]*action=["\']([^"\']+)["\'][^>]*>(.*?)</form>',
                    html_text,
                    re.DOTALL | re.IGNORECASE,
                )
            if form_match:
                action = form_match.group(1).replace("&amp;", "&")
                form_body = form_match.group(2)
                inputs = []
                for inp_tag in re.findall(r'<input[^>]+>', form_body, re.IGNORECASE):
                    name_m = re.search(r'name=["\']([^"\']+)["\']', inp_tag, re.IGNORECASE)
                    val_m = re.search(r'value=["\']([^"\']*)["\']', inp_tag, re.IGNORECASE)
                    if name_m:
                        n = name_m.group(1)
                        v = val_m.group(1) if val_m else ""
                        inputs.append((n, v))
                        if n == "confirm":
                            confirm_token = v

                url_components = urllib.parse.urlsplit(action)
                query_params = urllib.parse.parse_qs(url_components.query)
                for k, v in inputs:
                    query_params[k] = [v]
                query = urllib.parse.urlencode(query_params, doseq=True)
                download_url = urllib.parse.urlunsplit(url_components._replace(query=query))

        # Method 4: Standard href containing confirm=
        if not download_url:
            link_match = re.search(r'href="(/uc\?[^"]*confirm=[^"]*)"', html_text)
            if link_match:
                download_url = urllib.parse.urljoin("https://drive.google.com", link_match.group(1).replace("&amp;", "&"))
                token_match = re.search(r"confirm=([0-9A-Za-z_-]+)", download_url)
                if token_match:
                    confirm_token = token_match.group(1)

        # Method 5: Embedded JSON downloadUrl
        if not download_url:
            json_match = re.search(r'"downloadUrl":"([^"]+)"', html_text)
            if json_match:
                download_url = json_match.group(1).replace("\\u003d", "=").replace("\\u0026", "&")

        # Extract confirm token if not yet found
        if not confirm_token:
            token_match = re.search(r'name="confirm" value="([^"]+)"', html_text) or re.search(r"confirm=([0-9A-Za-z_-]+)", html_text)
            if token_match:
                confirm_token = token_match.group(1)

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
        remote_filename = None

        if "text/html" in content_type and resp.status_code == 200:
            html_text = resp.text
            confirm_token, download_url, detected_title = self._parse_html_confirmation(html_text, file_id=file_id)
            if detected_title:
                remote_filename = detected_title

            if not confirm_token:
                for k, v in session.cookies.items():
                    if k.startswith("download_warning"):
                        confirm_token = v
                        break

            # Step 2: Request with confirmation
            if download_url:
                resp = session.get(download_url, headers=headers, stream=True, timeout=self.timeout)
            elif confirm_token:
                params["confirm"] = confirm_token
                resp = session.get(self.BASE_URL, params=params, headers=headers, stream=True, timeout=self.timeout)
            else:
                usercontent_url = f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"
                resp = session.get(usercontent_url, headers=headers, stream=True, timeout=self.timeout)

        # Check for error responses
        if resp.status_code not in (200, 206):
            err_details = ""
            if "text/html" in resp.headers.get("content-type", "").lower():
                try:
                    err_m = re.search(r'<p class="uc-error-subcaption">(.*?)</p>', resp.text, re.IGNORECASE)
                    if err_m:
                        err_details = f": {err_m.group(1).strip()}"
                except Exception:
                    pass

            if resp.status_code == 404:
                raise FileNotFoundError(f"File not found or access denied (check permissions / link sharing){err_details}")
            elif resp.status_code == 403:
                raise PermissionError(f"Access forbidden. Download quota exceeded or sign-in required{err_details}")
            elif resp.status_code == 416:
                return resp, remote_filename, 0
            else:
                raise RuntimeError(f"Server returned HTTP status code {resp.status_code}{err_details}")

        # Determine filename
        extracted_name = self._extract_filename_from_headers(resp.headers)
        if extracted_name:
            remote_filename = extracted_name

        # Determine total size
        total_size = 0
        if resp.status_code == 206:
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

        return resp, remote_filename, total_size

    def _download_via_gdown(
        self,
        file_id: str,
        dest_dir: Path,
        custom_filename: Optional[str] = None,
        progress_tracker: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Download file using gdown with resume and progress bar integration."""
        if not GDOWN_AVAILABLE:
            raise RuntimeError("gdown is not installed. Install with `pip install gdown`.")

        start_time = time.time()
        dest_dir.mkdir(parents=True, exist_ok=True)
        task_id = None

        target_file = dest_dir / custom_filename if custom_filename else None
        output_arg = str(target_file) if target_file else (str(dest_dir) + os.sep)

        def gdown_progress(downloaded: int, total: Optional[int]):
            nonlocal task_id
            if progress_tracker:
                if task_id is None:
                    desc_name = custom_filename or f"gdrive_{file_id[:8]}"
                    task_id = progress_tracker.add_task(
                        description=f"[cyan]{desc_name[:24]:<24}",
                        total=total if (total and total > 0) else None,
                        completed=downloaded,
                    )
                else:
                    progress_tracker.update(task_id, completed=downloaded, total=total if (total and total > 0) else None)

        try:
            downloaded_path_str = gdown.download(
                id=file_id,
                output=output_arg,
                quiet=True,
                resume=not self.overwrite,
                progress=gdown_progress if progress_tracker else None,
            )

            if not downloaded_path_str or not Path(downloaded_path_str).exists():
                raise RuntimeError("gdown did not produce the expected downloaded file")

            final_path = Path(downloaded_path_str)
            file_size = final_path.stat().st_size

            if progress_tracker and task_id is not None:
                progress_tracker.update(task_id, completed=file_size)

            return {
                "file_id": file_id,
                "filename": final_path.name,
                "path": str(final_path),
                "status": "Downloaded (gdown)",
                "bytes_downloaded": file_size,
                "duration": time.time() - start_time,
            }
        except Exception as e:
            if progress_tracker and task_id is not None:
                progress_tracker.remove_task(task_id)
            raise e

    def download_file(
        self,
        url_or_id: str,
        custom_filename: Optional[str] = None,
        subdir: Optional[str] = None,
        progress_tracker: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Download a single Google Drive file with retry, resume, and progress tracking.
        Supports direct chunked streaming and gdown engine / fallback.
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

        tentative_filename = custom_filename or f"gdrive_{file_id}.bin"
        temp_dest = dest_dir / tentative_filename

        # Check if already fully downloaded
        if temp_dest.exists() and not self.overwrite:
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

        # If gdown engine is explicitly selected
        if self.engine == "gdown" and GDOWN_AVAILABLE:
            try:
                return self._download_via_gdown(
                    file_id=file_id,
                    dest_dir=dest_dir,
                    custom_filename=custom_filename,
                    progress_tracker=progress_tracker,
                )
            except Exception as exc:
                return {
                    "file_id": file_id,
                    "filename": custom_filename or "Unknown",
                    "status": "Failed",
                    "error": f"gdown error: {exc}",
                    "bytes_downloaded": 0,
                    "duration": 0,
                }

        last_error = None
        start_time = time.time()

        for attempt in range(1, self.retry_attempts + 1):
            session = self._get_session()
            task_id = None

            try:
                part_file = dest_dir / f"{tentative_filename}.part"

                resume_offset = 0
                if part_file.exists() and not self.overwrite:
                    resume_offset = part_file.stat().st_size

                # Negotiate connection & stream
                resp, remote_filename, total_size = self._prepare_download_stream(
                    session, file_id, resume_offset=resume_offset
                )

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

                final_name = custom_filename or remote_filename or tentative_filename
                final_name = re.sub(r'[\\/*?:"<>|]', "_", final_name)
                final_dest = dest_dir / final_name
                part_file = dest_dir / f"{final_name}.part"

                if final_dest.exists() and not self.overwrite:
                    file_size = final_dest.stat().st_size
                    if file_size > 0:
                        return {
                            "file_id": file_id,
                            "filename": final_dest.name,
                            "path": str(final_dest),
                            "status": "Skipped (Exists)",
                            "bytes_downloaded": file_size,
                            "duration": time.time() - start_time,
                        }

                if resp.status_code == 206:
                    write_mode = "ab"
                    downloaded_bytes = resume_offset
                else:
                    write_mode = "wb"
                    downloaded_bytes = 0

                if progress_tracker:
                    task_id = progress_tracker.add_task(
                        description=f"[cyan]{final_name[:24]:<24}",
                        total=total_size if total_size > 0 else None,
                        completed=downloaded_bytes,
                    )

                with open(part_file, write_mode) as f:
                    for chunk in resp.iter_content(chunk_size=self.chunk_size):
                        if chunk:
                            f.write(chunk)
                            chunk_len = len(chunk)
                            downloaded_bytes += chunk_len
                            if progress_tracker and task_id is not None:
                                progress_tracker.update(task_id, advance=chunk_len)

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

        # If direct stream failed after retries, try gdown fallback if engine is 'auto' and gdown is available
        if self.engine == "auto" and GDOWN_AVAILABLE:
            try:
                return self._download_via_gdown(
                    file_id=file_id,
                    dest_dir=dest_dir,
                    custom_filename=custom_filename,
                    progress_tracker=progress_tracker,
                )
            except Exception as gdown_err:
                last_error = f"{last_error} | gdown fallback error: {gdown_err}"

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
            f"[bold]Total Files:[/bold] {len(results)} | "
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

    if yaml is None:
        raise ImportError("No YAML parser found. Install PyYAML with `pip install pyyaml`.")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    settings = data.get("settings", {})
    files = data.get("files", [])
    return settings, files


def load_file_list(file_list_path: str) -> List[Dict[str, Any]]:
    """Parse links from a plain text file (one URL/ID per line)."""
    path = Path(file_list_path)
    if not path.exists():
        raise FileNotFoundError(f"URL list file not found: {file_list_path}")

    targets = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            targets.append({"url": line, "filename": None, "subdir": None})
    return targets


def write_failed_log(log_path: Path, failed_items: List[Dict[str, Any]]) -> None:
    """Record persistently failed URLs and filenames into a log file."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w", encoding="utf-8") as f:
        f.write("# ====================================================================\n")
        f.write("# Google Drive Downloader - Persistent Failed Downloads Log\n")
        f.write(f"# Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n")
        f.write(f"# Total Failed Items: {len(failed_items)}\n")
        f.write("# ====================================================================\n\n")
        for idx, item in enumerate(failed_items, 1):
            file_id = item.get("file_id", "Unknown")
            filename = item.get("filename", "Unknown")
            error = item.get("error", "Unknown Error")
            # Build full link
            if file_id and len(file_id) >= 20:
                url = f"https://drive.google.com/file/d/{file_id}/view"
            else:
                url = item.get("target_info", {}).get("url", file_id)

            f.write(f"[{idx}] File Name: {filename}\n")
            f.write(f"    Google Drive URL: {url}\n")
            f.write(f"    File ID:          {file_id}\n")
            f.write(f"    Error Reason:     {error}\n\n")


def run_download_pass(
    downloader: GoogleDriveDownloader,
    targets: List[Dict[str, Any]],
    max_workers: int,
    use_rich_ui: bool,
    pass_label: str = "Initial Pass",
) -> List[Dict[str, Any]]:
    """Execute a concurrent download pass for given targets."""
    print(f"\n>>> Running Download: {pass_label} ({len(targets)} item(s)) <<<")
    results: List[Dict[str, Any]] = []

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
                        t["url"],
                        t["filename"],
                        t["subdir"],
                        progress,
                    ): t
                    for t in targets
                }

                for future in as_completed(future_map):
                    t = future_map[future]
                    try:
                        res = future.result()
                        res["target_info"] = t
                        results.append(res)
                    except Exception as exc:
                        results.append({
                            "file_id": t["url"],
                            "filename": t.get("filename", "Unknown"),
                            "status": "Failed",
                            "error": str(exc),
                            "bytes_downloaded": 0,
                            "duration": 0,
                            "target_info": t,
                        })
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {
                executor.submit(
                    downloader.download_file,
                    t["url"],
                    t["filename"],
                    t["subdir"],
                    None,
                ): t
                for t in targets
            }

            for future in as_completed(future_map):
                t = future_map[future]
                try:
                    res = future.result()
                    res["target_info"] = t
                    results.append(res)
                    print(f"[{res['status']}] {res.get('filename')} ({format_size(res.get('bytes_downloaded', 0))})")
                except Exception as exc:
                    results.append({
                        "file_id": t["url"],
                        "filename": t.get("filename", "Unknown"),
                        "status": "Failed",
                        "error": str(exc),
                        "bytes_downloaded": 0,
                        "duration": 0,
                        "target_info": t,
                    })
    return results


def main():
    parser = argparse.ArgumentParser(
        description="Advanced Google Drive Multi-Link & Folder Downloader",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to YAML configuration file (e.g. config.yml)",
    )
    parser.add_argument(
        "--file-list", "-f",
        type=str,
        default=None,
        help="Path to a text file containing Google Drive links (one per line)",
    )
    parser.add_argument(
        "positional_urls",
        nargs="*",
        help="Optional Google Drive URLs (file or folder) or file IDs passed as positional arguments",
    )
    parser.add_argument(
        "--urls", "-u",
        nargs="+",
        help="One or more Google Drive URLs (file or folder) or file IDs",
    )
    parser.add_argument(
        "--engine",
        type=str,
        choices=["auto", "direct", "gdown"],
        default=None,
        help="Download engine: 'auto' (direct stream with gdown fallback), 'direct', or 'gdown'",
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
    parser.add_argument(
        "--failed-log",
        type=str,
        default="download_failed.txt",
        help="Output text file path to record any persistently failed file names and URLs",
    )
    parser.add_argument(
        "--no-retry-failed",
        action="store_true",
        help="Disable automatic second-pass redownload of failed URLs",
    )

    args = parser.parse_args()

    config_settings = {}
    config_files = []

    config_to_load = args.config
    if not config_to_load and not args.urls and not args.positional_urls and not args.file_list and Path("config.yml").exists():
        config_to_load = "config.yml"

    if config_to_load:
        config_settings, config_files = load_config_file(config_to_load)

    output_dir = args.output or config_settings.get("output_dir", "./downloads")
    max_workers = args.workers or config_settings.get("max_workers", 3)
    chunk_size_kb = args.chunk_size or config_settings.get("chunk_size_kb", 1024)
    timeout = config_settings.get("timeout_seconds", 45)
    retry_attempts = config_settings.get("retry_attempts", 3)
    retry_delay = config_settings.get("retry_delay_seconds", 2)
    overwrite = args.overwrite or config_settings.get("overwrite", False)
    engine = args.engine or config_settings.get("engine", "auto")

    initial_targets: List[Dict[str, Any]] = []

    if args.file_list:
        initial_targets.extend(load_file_list(args.file_list))

    all_cli_urls = (args.urls or []) + (args.positional_urls or [])
    if all_cli_urls:
        for u in all_cli_urls:
            initial_targets.append({"url": u, "filename": None, "subdir": None})

    for item in config_files:
        if isinstance(item, str):
            initial_targets.append({"url": item, "filename": None, "subdir": None})
        elif isinstance(item, dict) and "url" in item:
            initial_targets.append({
                "url": item["url"],
                "filename": item.get("filename"),
                "subdir": item.get("subdir"),
            })

    if not initial_targets:
        print("No Google Drive URLs or files specified!")
        print("Provide URLs via `<link1> <link2>`, `--urls <link>`, `--file-list links.txt`, or in `config.yml`.")
        parser.print_help()
        sys.exit(1)

    # Expand folder URLs into individual file targets
    expanded_targets: List[Dict[str, Any]] = []
    for target in initial_targets:
        url_candidate = target["url"]
        if GoogleDriveURLParser.is_folder_url(url_candidate):
            print(f"[Folder Detected] Crawling Google Drive folder: {url_candidate}")
            folder_items = GoogleDriveURLParser.extract_folder_items(url_candidate)
            print(f"Found {len(folder_items)} file(s) inside folder.")

            for fi in folder_items:
                item_subdir = fi["subdir"]
                if target.get("subdir"):
                    item_subdir = str(Path(target["subdir"]) / (item_subdir or ""))

                expanded_targets.append({
                    "url": fi["url"],
                    "filename": fi["filename"],
                    "subdir": item_subdir,
                })
        else:
            expanded_targets.append(target)

    targets = expanded_targets

    print(f"Starting Google Drive Downloader...")
    print(f"Total targets: {len(targets)} | Workers: {max_workers} | Engine: {engine} | Output: {output_dir}")

    downloader = GoogleDriveDownloader(
        output_dir=output_dir,
        chunk_size_kb=chunk_size_kb,
        timeout=timeout,
        retry_attempts=retry_attempts,
        retry_delay=retry_delay,
        overwrite=overwrite,
        engine=engine,
    )

    use_rich_ui = RICH_AVAILABLE and not args.no_progress and sys.stdout.isatty()

    # Pass 1: Initial Download
    results_pass1 = run_download_pass(
        downloader=downloader,
        targets=targets,
        max_workers=max_workers,
        use_rich_ui=use_rich_ui,
        pass_label="Initial Pass",
    )

    # Map results by target url
    results_dict: Dict[str, Dict[str, Any]] = {}
    for r in results_pass1:
        key = f"{r.get('file_id')}_{r.get('filename')}"
        results_dict[key] = r

    # Identify failures
    failed_pass1 = [r for r in results_pass1 if r.get("status") == "Failed"]

    # Pass 2: Automatic Redownload of Failed URLs
    if failed_pass1 and not args.no_retry_failed:
        print(f"\n[Warning] {len(failed_pass1)} download(s) failed during the initial pass.")
        print("Initiating automatic second-pass redownload for failed URLs in 3 seconds...")
        time.sleep(3)

        retry_targets = [r["target_info"] for r in failed_pass1]
        retry_workers = max(1, min(max_workers, 2))  # Reduce concurrency to avoid rate limiting

        results_pass2 = run_download_pass(
            downloader=downloader,
            targets=retry_targets,
            max_workers=retry_workers,
            use_rich_ui=use_rich_ui,
            pass_label="Second-Pass Redownload (Failed URLs)",
        )

        # Update results mapping with redownload outcomes
        for r in results_pass2:
            key = f"{r.get('file_id')}_{r.get('filename')}"
            results_dict[key] = r

    final_results = list(results_dict.values())
    print_summary_table(final_results)

    # Check for persistent failures after redownload
    persistently_failed = [r for r in final_results if r.get("status") == "Failed"]
    failed_log_path = Path(args.failed_log)

    if persistently_failed:
        write_failed_log(failed_log_path, persistently_failed)
        print(f"\n[ALERT] {len(persistently_failed)} file(s) persistently failed after redownload retry.")
        print(f"Saved failed file names and links into: {failed_log_path.resolve()}")
    else:
        # If everything succeeded, clean up or remove stale download_failed.txt
        if failed_log_path.exists():
            try:
                failed_log_path.unlink()
            except Exception:
                pass
        print("\n[SUCCESS] All files downloaded successfully without failures.")

    if persistently_failed and len(persistently_failed) == len(final_results):
        sys.exit(1)


if __name__ == "__main__":
    main()
