# 🚀 Advanced Google Drive Downloader

An advanced, multi-threaded Google Drive batch downloader designed for high-speed file transfers, automated virus scan confirmation bypass (large files > 100MB), stream resumption, and GitHub Actions cloud automation.

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

- **Multi-URL Parsing**: Supports standard sharing URLs (`/file/d/...`), direct export links (`uc?id=...`), open links (`/open?id=...`), folder links, and raw file IDs.
- **Large File Bypass**: Automatically detects and extracts Google Drive's virus scan confirmation tokens (`confirm=...`) and session cookies, bypassing the manual *"Google Drive can't scan this file for viruses"* prompt.
- **Multi-Threaded Concurrency**: Downloads multiple files simultaneously with configurable thread pools (`max_workers`).
- **Resumable Downloads**: Supports HTTP Range headers (`Range: bytes=...`) to resume interrupted or paused downloads without re-downloading existing chunks.
- **Interactive UI & Headless Mode**: Multi-line progress bars with transfer speed, ETA, and downloaded size using `rich`, with automatic fallback for CI/headless logs.
- **Cloud Automation via GitHub Actions**: Run the downloader on GitHub's gigabit runners, then download the files as GitHub workflow artifacts or release attachments.

---

## 🛠️ Local Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure YAML (`config.yml`)
Add your Google Drive links into `config.yml`:

```yaml
settings:
  output_dir: "./downloads"
  max_workers: 4
  chunk_size_kb: 1024
  timeout_seconds: 45
  retry_attempts: 3
  overwrite: false

files:
  - url: "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs/view?usp=sharing"
    filename: "my_data.zip"
    subdir: "datasets"

  - url: "https://drive.google.com/open?id=1AbCdEfGhIjKlMnOpQrStUvWxYz"
    filename: "document.pdf"
```

### 3. Run the Downloader
Execute using the YAML configuration:
```bash
python downloader.py --config config.yml
```

Or download directly via command line arguments:
```bash
python downloader.py --urls "https://drive.google.com/file/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs/view" --workers 2 --output ./my_files
```

---

## ☁️ Running on GitHub ("Using GitHub")

You can run this downloader directly on **GitHub Actions** to leverage GitHub's high-speed cloud network and avoid using local bandwidth:

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

### Step 2: Trigger the Workflow
1. In your GitHub repository, navigate to the **Actions** tab.
2. Select **"Google Drive Batch Downloader"** on the left menu.
3. Click **"Run workflow"**:
   - Paste any Google Drive links directly into the URL box (or leave empty to download everything defined in `config.yml`).
   - Select your worker count and artifact retention duration.
   - Click **"Run workflow"**.

### Step 3: Download Your Files
Once the workflow run completes:
1. Click on the completed run in GitHub Actions.
2. Under the **Artifacts** section at the bottom, click `google-drive-downloads-<run_id>` to download your zip file.

---

## ⚙️ CLI Options Reference

| Option | Flag | Description | Default |
| :--- | :--- | :--- | :--- |
| `--config` | `-c` | Path to YAML config file | `config.yml` |
| `--urls` | `-u` | Space-separated list of Google Drive URLs or IDs | None |
| `--output` | `-o` | Target download directory | `./downloads` |
| `--workers` | `-w` | Number of concurrent worker threads | `3` |
| `--chunk-size` | | Stream chunk size in KB | `1024` (1 MB) |
| `--overwrite` | | Force re-download even if file already exists | `False` |
| `--no-progress`| | Disable rich animated progress bars (for CI) | `False` |
