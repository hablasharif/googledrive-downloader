"""
One-click helper to authenticate Google Drive once locally and display
your REFRESH TOKEN for use with GitHub Actions.
"""
from upload_drive import get_credentials

if __name__ == "__main__":
    print("Authenticating Google Drive to generate Refresh Token...")
    creds = get_credentials()
    if creds and creds.refresh_token:
        print("\n" + "=" * 65)
        print("YOUR REFRESH TOKEN FOR GITHUB ACTIONS:")
        print(creds.refresh_token)
        print("=" * 65)
        print("\nYou can paste this token into:")
        print("1. 'upload_drive.py' -> REFRESH_TOKEN = '...' (Hardcoded)")
        print("   OR")
        print("2. GitHub Repository -> Settings -> Secrets and variables -> Actions")
        print("   New repository secret -> Name: GDRIVE_REFRESH_TOKEN")
    else:
        print("Done. Credentials saved to token.json.")
