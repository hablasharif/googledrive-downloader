# 🚀 Advanced Google Drive Downloader (Files & Folders)

An advanced, multi-threaded Google Drive batch downloader designed for high-speed file transfers, **full folder tree downloading**, automated virus scan confirmation bypass (large files > 100MB), stream resumption, and GitHub Actions cloud automation.

---

## 📁 Project Structure

```
google drive downloader/
├── .github/
│   └── workflows/
│       └── download.yml     # GitHub Actions workflow for running downloads in GitHub's cloud
├── config.yml               # YAML configuration file for batch links & settings
├── downloader.py            # Core advanced Python downloader engine
├── requirements.txt         # Required Python packages
├── .gitignore               # Git ignore rules (ignores partial files & downloaded content)
└── README.md                # Documentation & usage guide
```

---

## ✨ Features

- **ANY Google Drive Link Supported**:
  - **Full Folders**: `https://drive.google.com/drive/folders/<FOLDER_ID>` (automatically crawls and downloads all files preserving folder trees).
  - **File View Links**: `https://drive.google.com/file/d/<FILE_ID>/view`
  - **Direct Export Links**: `https://drive.google.com/uc?id=<FILE_ID>&export=download`
  - **Open Links**: `https://drive.google.com/open?id=<FILE_ID>`
  - **Raw IDs**: `<FILE_ID>`
- **Large File Confirmation Bypass**: Automatically detects and extracts Google Drive's virus scan confirmation tokens (`confirm=...`) and session cookies, downloading files of any size without prompts.
- **Multi-Threaded Concurrency**: Downloads multiple files simultaneously with configurable worker threads (`max_workers`).
- **Resumable Downloads**: Uses HTTP Range headers to resume interrupted partial downloads.
- **Interactive UI & Headless Mode**: Animated progress bars with transfer speed, ETA, and downloaded size using `rich`, with automatic fallback for CI/headless logs.
- **Cloud Automation via GitHub Actions**: Run the downloader on GitHub's gigabit runners, and download the files as GitHub workflow artifacts.

---

## 🛠️ Local Usage

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure YAML (`config.yml`)
Add your links (files or folders) into `config.yml`:

```yaml
settings:
  output_dir: "./downloads"
  max_workers: 4
  chunk_size_kb: 1024
  timeout_seconds: 45
  retry_attempts: 3
  overwrite: false

files:
  # Folder link (recursively downloads all files inside):
  - url: "https://drive.google.com/drive/folders/1S4TlJa0_K78atNJBmgpXkI4T0Xz-Fw0r?usp=sharing"
    subdir: "my_folder"

  # Single file link:
  # - url: "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs/view"
  #   filename: "my_data.zip"
```

### 3. Run the Downloader
Execute using the YAML configuration:
```bash
python downloader.py --config config.yml
```

Or download directly via URL (positional argument or `--urls`):
```bash
python downloader.py "https://drive.google.com/file/d/1tLg9ftBUTT-Ku3x1JM8zpOsyQrkSn4jh/view"
python downloader.py "https://drive.google.com/drive/folders/1S4TlJa0_K78atNJBmgpXkI4T0Xz-Fw0r?usp=sharing" --workers 4 --output ./downloads
```

Choose a download engine (`auto`, `direct`, or `gdown`):
```bash
# 'auto' uses direct streaming with automatic gdown fallback (recommended)
python downloader.py "<URL>" --engine auto

# Or force gdown engine directly:
python downloader.py "<URL>" --engine gdown
```

Or pass a text file containing links:
```bash
python downloader.py --file-list links.txt --workers 4 --output ./downloads
```

---

## ☁️ Running on GitHub ("Using GitHub")

You can run this downloader directly on **GitHub Actions** to download Google Drive links (files or entire folders) using GitHub's high-speed cloud network:

### Step 1: Push to GitHub
1. Initialize git in this folder:
   ```bash
   git init
   git add .
   git commit -m "Initial commit of Google Drive downloader"
   ```
2. Create a repository on GitHub and push your code:
   ```bash
   git remote add origin https://github.com/<YOUR_USERNAME>/<REPO_NAME>.git
   git branch -M main
   git push -u origin main
   ```

### Step 2: Set Up Google Drive Upload on GitHub (One-Time)
Because GitHub Actions is a headless cloud environment, Google OAuth needs a **Refresh Token**:
1. Run this command locally once on your computer:
   ```bash
   python get_refresh_token.py
   ```
   A browser window will open. Log into your Google account and click **Allow**.
2. Copy the printed token and either:
   - **Hardcode it** directly into `upload_drive.py`:
     ```python
     REFRESH_TOKEN = "your_printed_refresh_token_here"
     ```
   - **Or add it to GitHub Secrets** (Recommended):
     Go to **GitHub Repo -> Settings -> Secrets and variables -> Actions -> New repository secret**:
     - Name: `GDRIVE_REFRESH_TOKEN`
     - Value: `your_printed_refresh_token_here`

### Step 3: Trigger the Workflow on GitHub
1. In your GitHub repository, navigate to the **Actions** tab.
2. Select **"Google Drive Batch Downloader & Auto-Uploader"** on the left menu.
3. Click **"Run workflow"**:
   - Paste any Google Drive links into the **Google Drive Links** box (or leave blank to use `config.yml`).
   - (Optional) Enter a **Destination Folder ID** on Google Drive.
   - Leave **Upload downloaded files directly to your Google Drive** checked (`true`).
   - Click **"Run workflow"**.

GitHub Actions will download the files using GitHub's gigabit network and automatically upload them straight to your Google Drive account!

