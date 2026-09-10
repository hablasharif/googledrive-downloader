import os
import re
import sys
import json
import mimetypes
import argparse
import requests
from pathlib import Path

from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload


# ============================================================
# GOOGLE OAUTH CONFIGURATION — HARDCODED
# ============================================================

CLIENT_ID = (
    "8250288792-7v43qpmkf0gecfgcjkl40q3s5llccuib.apps.googleusercontent.com"
)

CLIENT_SECRET = (
    "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"
)

# Hardcode your refresh token here for 100% headless GitHub Actions execution
# You can also set the GDRIVE_REFRESH_TOKEN environment variable / secret.
REFRESH_TOKEN = os.environ.get("GDRIVE_REFRESH_TOKEN", "")

# OAuth scopes
SCOPES = [
    "https://www.googleapis.com/auth/drive"
]

TOKEN_FILE = "token.json"


# ============================================================
# PUBLIC GOOGLE DRIVE LINKS TO DOWNLOAD FIRST (OPTIONAL)
# ============================================================
# If you add URLs here, the script downloads them into DOWNLOAD_DIR first.
# If left empty, it will directly upload all files already inside DOWNLOAD_DIR.

PUBLIC_DRIVE_URLS = [
    # "https://drive.google.com/file/d/FILE_ID/view",
    # "https://drive.google.com/open?id=FILE_ID",
]


# ============================================================
# DESTINATION FOLDER ON GOOGLE DRIVE
# ============================================================
# Put a Google Drive folder ID here if you want uploads inside a specific folder.
# Leave as None to upload to your Google Drive root / My Drive.

DESTINATION_FOLDER_ID = os.environ.get("DESTINATION_FOLDER_ID", None)


# ============================================================
# LOCAL DOWNLOADS DIRECTORY
# ============================================================

DOWNLOAD_DIR = "downloads"

os.makedirs(DOWNLOAD_DIR, exist_ok=True)


# ============================================================
# GOOGLE AUTHENTICATION (HEADLESS / GITHUB ACTIONS COMPATIBLE)
# ============================================================

def get_credentials():
    """
    Authenticate with Google OAuth.
    Supports:
    1. REFRESH_TOKEN (hardcoded or environment variable for GitHub Actions)
    2. GDRIVE_TOKEN_JSON environment variable (full JSON secret)
    3. Existing local token.json
    4. Local browser authorization server (for generating initial token on desktop)
    """
    creds = None

    # Priority 1: Check if GDRIVE_TOKEN_JSON environment variable is set
    token_json_env = os.environ.get("GDRIVE_TOKEN_JSON", "").strip()
    if token_json_env:
        try:
            token_data = json.loads(token_json_env)
            creds = Credentials.from_authorized_user_info(token_data, SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            print("✅ [AUTH] Authenticated successfully via GDRIVE_TOKEN_JSON environment secret.")
            return creds
        except Exception as e:
            print(f"⚠️ [AUTH] Failed loading GDRIVE_TOKEN_JSON: {e}")

    # Priority 2: Use hardcoded or environment REFRESH_TOKEN (Best for GitHub Actions)
    refresh_token = REFRESH_TOKEN.strip()
    if refresh_token:
        try:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                scopes=SCOPES
            )
            creds.refresh(Request())
            print("✅ [AUTH] Authenticated successfully via REFRESH_TOKEN.")
            return creds
        except Exception as e:
            print(f"⚠️ [AUTH] REFRESH_TOKEN refresh failed: {e}")

    # Priority 3: Local token.json file
    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            print("[AUTH] Refreshing expired token from token.json...")
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
            return creds
        except Exception as e:
            print(f"⚠️ [AUTH] Token refresh failed: {e}")
            creds = None

    # Priority 4: If running in GitHub Actions / CI without a token, raise clear error
    is_ci = os.environ.get("CI") == "true" or os.environ.get("GITHUB_ACTIONS") == "true"
    if is_ci:
        raise RuntimeError(
            "\n" + "=" * 65 + "\n"
            "ERROR: Running in GitHub Actions without OAuth Token!\n"
            "GitHub Actions is a headless cloud environment without a web browser.\n\n"
            "To fix this, do ONE of the following:\n"
            "1. Run 'python upload_drive.py --auth-only' locally once on your computer\n"
            "   to generate your REFRESH_TOKEN.\n"
            "2. Paste the refresh token into the hardcoded REFRESH_TOKEN variable\n"
            "   in upload_drive.py, OR add it to GitHub Secrets as 'GDRIVE_REFRESH_TOKEN'.\n"
            "=" * 65
        )

    # Priority 5: Interactive browser authorization (Local computer only)
    print("\n[AUTH] Opening web browser for one-time Google authorization...")
    print("[AUTH] Please log in and allow Google Drive permissions.\n")

    client_config = {
        "installed": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"]
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    # Save token.json
    with open(TOKEN_FILE, "w") as token:
        token.write(creds.to_json())

    print("✅ Authorization completed and saved to token.json!")
    if creds.refresh_token:
        print("\n" + "=" * 65)
        print("⭐ YOUR GOOGLE DRIVE REFRESH TOKEN (FOR GITHUB ACTIONS):")
        print(creds.refresh_token)
        print("=" * 65)
        print("Tip: Paste this token into REFRESH_TOKEN in upload_drive.py or")
        print("add it as a GitHub Secret named GDRIVE_REFRESH_TOKEN!\n")

    return creds


# ============================================================
# GOOGLE DRIVE URL EXTRACTION & OPTIONAL DOWNLOAD
# ============================================================

def extract_drive_file_id(url: str):
    """Extract Google Drive file ID from link."""
    patterns = [
        r"/file/d/([a-zA-Z0-9_-]+)",
        r"[?&]id=([a-zA-Z0-9_-]+)",
        r"/uc\?id=([a-zA-Z0-9_-]+)",
        r"/open\?id=([a-zA-Z0-9_-]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def download_public_drive_file(url: str, output_path: str):
    """Download a publicly shared Google Drive file."""
    file_id = extract_drive_file_id(url)
    if not file_id:
        raise ValueError(f"Could not extract Google Drive file ID from: {url}")

    session = requests.Session()
    download_url = "https://drive.usercontent.google.com/download"
    params = {"id": file_id, "export": "download", "confirm": "t"}

    response = session.get(download_url, params=params, stream=True, timeout=120)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type:
        text = response.text
        confirm_match = re.search(r'name="confirm"\s+value="([^"]+)"', text) or re.search(r"confirm=([0-9A-Za-z_-]+)", text)
        if confirm_match:
            params["confirm"] = confirm_match.group(1)
            response = session.get(download_url, params=params, stream=True, timeout=120)
            response.raise_for_status()
        else:
            raise RuntimeError("Google Drive confirmation token not found. File may require direct login.")

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)

    if os.path.getsize(output_path) == 0:
        raise RuntimeError("Downloaded file is empty.")

    return output_path


# ============================================================
# REMOTE FOLDER CREATION & RESOLUTION
# ============================================================

_folder_cache = {}

def get_or_create_remote_folder(service, folder_name: str, parent_id: str = None) -> str:
    """Find or create a folder on Google Drive and cache its ID."""
    cache_key = (folder_name, parent_id)
    if cache_key in _folder_cache:
        return _folder_cache[cache_key]

    query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    else:
        query += " and 'root' in parents"

    try:
        results = service.files().list(q=query, spaces="drive", fields="files(id, name)").execute()
        items = results.get("files", [])
        if items:
            folder_id = items[0]["id"]
            _folder_cache[cache_key] = folder_id
            return folder_id
    except Exception:
        pass

    folder_metadata = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder"
    }
    if parent_id:
        folder_metadata["parents"] = [parent_id]

    folder = service.files().create(body=folder_metadata, fields="id").execute()
    folder_id = folder.get("id")
    _folder_cache[cache_key] = folder_id
    return folder_id


def resolve_drive_folder_for_relpath(service, rel_dir: str, base_parent_id: str = None) -> str:
    """Resolve or create nested folder structure on Google Drive matching local path."""
    if not rel_dir or rel_dir == ".":
        return base_parent_id

    parts = Path(rel_dir).parts
    current_parent = base_parent_id

    for part in parts:
        current_parent = get_or_create_remote_folder(service, part, current_parent)

    return current_parent


# ============================================================
# RESUMABLE UPLOAD TO GOOGLE DRIVE
# ============================================================

def upload_file_to_drive(service, local_file_path: str, filename: str, parent_folder_id: str = None):
    """Upload a local file to Google Drive using resumable chunked streaming."""
    file_size = os.path.getsize(local_file_path)
    mime_type, _ = mimetypes.guess_type(local_file_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    file_metadata = {"name": filename}
    if parent_folder_id:
        file_metadata["parents"] = [parent_folder_id]

    chunk_size = 5 * 1024 * 1024
    media = MediaFileUpload(
        local_file_path,
        mimetype=mime_type,
        chunksize=chunk_size,
        resumable=True
    )

    request = service.files().create(
        body=file_metadata,
        media_body=media,
        fields="id, name, webViewLink, size"
    )

    response = None
    last_reported = -1

    while response is None:
        status, response = request.next_chunk()
        if status:
            percent = int(status.progress() * 100)
            if percent != last_reported:
                uploaded_mb = status.resumable_progress / (1024 * 1024)
                total_mb = status.total_size / (1024 * 1024)
                sys.stdout.write(f"\r  Uploading: {percent}% [{uploaded_mb:.1f} MB / {total_mb:.1f} MB] ")
                sys.stdout.flush()
                last_reported = percent

    sys.stdout.write(f"\r  Uploading: 100% completed!                                  \n")
    sys.stdout.flush()
    return response


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="Google Drive Downloader & Uploader")
    parser.add_argument("--auth-only", action="store_true", help="Authorize and print REFRESH_TOKEN for GitHub Actions")
    parser.add_argument("--dir", default=DOWNLOAD_DIR, help="Local directory of files to upload")
    args = parser.parse_args()

    print("=" * 65)
    print("      GOOGLE DRIVE DOWNLOAD & AUTO-UPLOAD TO YOUR DRIVE")
    print("=" * 65)

    # 1. Authenticate with Google Drive
    print("\n[1/3] Authenticating Google Drive API...")
    try:
        creds = get_credentials()
        service = build("drive", "v3", credentials=creds)
        print("✅ Authentication successful!\n")
    except Exception as error:
        print(f"❌ Authentication failed: {error}")
        sys.exit(1)

    if args.auth_only:
        print("Auth check passed successfully. Ready for GitHub Actions.")
        return

    # Check for URLs passed via environment variable (e.g. from GitHub Actions workflow)
    urls_to_download = list(PUBLIC_DRIVE_URLS)
    env_urls = os.environ.get("INPUT_URLS", "").strip()
    if env_urls:
        for u in env_urls.splitlines():
            clean_u = u.strip()
            if clean_u and not clean_u.startswith("#"):
                urls_to_download.append(clean_u)

    # 2. Download any URLs specified
    upload_target_dir = args.dir
    if urls_to_download:
        print(f"[2/3] Processing {len(urls_to_download)} URL(s)...")
        for idx, url in enumerate(urls_to_download, start=1):
            try:
                file_id = extract_drive_file_id(url)
                filename = f"drive_file_{file_id}" if file_id else f"drive_file_{idx}"

                if file_id:
                    try:
                        meta = service.files().get(fileId=file_id, fields="name").execute()
                        if meta.get("name"):
                            filename = meta.get("name")
                    except Exception:
                        pass

                out_path = os.path.join(upload_target_dir, filename)
                print(f"  Downloading [{idx}/{len(urls_to_download)}]: {filename}")
                download_public_drive_file(url, out_path)
                print(f"  ✅ Downloaded: {os.path.getsize(out_path):,} bytes")
            except Exception as e:
                print(f"  ❌ Download failed for {url}: {e}")
    else:
        print(f"[2/3] No URLs specified. Scanning '{upload_target_dir}' directory...")

    # 3. Discover all files in upload_target_dir (including subfolders)
    print(f"\n[3/3] Scanning '{upload_target_dir}' for files to upload...")
    files_to_upload = []

    for root, dirs, files in os.walk(upload_target_dir):
        for f in files:
            if f.startswith(".") or f.endswith(".tmp"):
                continue
            full_path = os.path.join(root, f)
            rel_path = os.path.relpath(full_path, upload_target_dir)
            rel_dir = os.path.dirname(rel_path)
            files_to_upload.append((full_path, f, rel_dir))

    if not files_to_upload:
        print(f"⚠️  No files found in '{upload_target_dir}' to upload.")
        return

    print(f"Found {len(files_to_upload)} file(s) ready to upload to Google Drive.\n")

    success_count = 0
    fail_count = 0

    for idx, (full_path, filename, rel_dir) in enumerate(files_to_upload, start=1):
        file_size = os.path.getsize(full_path)
        size_str = f"{file_size / (1024 * 1024):.2f} MB" if file_size >= 1024 * 1024 else f"{file_size / 1024:.1f} KB"

        target_folder_name = rel_dir if rel_dir else "Drive Root"
        print("-" * 65)
        print(f"[{idx}/{len(files_to_upload)}] File: {filename} ({size_str})")
        print(f"  Local Path:   {full_path}")
        print(f"  Drive Folder: {target_folder_name}")

        try:
            target_parent_id = resolve_drive_folder_for_relpath(
                service, rel_dir, base_parent_id=DESTINATION_FOLDER_ID
            )

            uploaded = upload_file_to_drive(
                service,
                local_file_path=full_path,
                filename=filename,
                parent_folder_id=target_parent_id
            )

            print(f"  ✅ Uploaded successfully!")
            print(f"  File ID: {uploaded.get('id')}")
            if uploaded.get("webViewLink"):
                print(f"  Link:    {uploaded.get('webViewLink')}")

            success_count += 1

        except Exception as error:
            print(f"  ❌ Upload failed: {error}")
            fail_count += 1

    print("\n" + "=" * 65)
    print("                     SUMMARY")
    print("=" * 65)
    print(f"Total files processed: {len(files_to_upload)}")
    print(f"Successfully uploaded: {success_count}")
    print(f"Failed uploads:        {fail_count}")
    print("=" * 65)


if __name__ == "__main__":
    main()
