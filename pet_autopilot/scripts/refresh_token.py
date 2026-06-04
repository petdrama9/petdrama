"""
Run this locally to regenerate token.json after it expires.

Steps:
  1. python space_autopilot/scripts/refresh_token.py
  2. Copy contents of space_autopilot/credentials/token.json
  3. Go to GitHub repo → Settings → Secrets → YOUTUBE_TOKEN_JSON → Update secret
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config import CREDENTIALS_DIR, YOUTUBE_SCOPES
from google_auth_oauthlib.flow import InstalledAppFlow

SECRETS_PATH = Path(CREDENTIALS_DIR) / "client_secrets.json"
TOKEN_PATH = Path(CREDENTIALS_DIR) / "token.json"

if not SECRETS_PATH.exists():
    print(f"ERROR: {SECRETS_PATH} not found.")
    print("Download from Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client IDs")
    sys.exit(1)

flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS_PATH), YOUTUBE_SCOPES)
creds = flow.run_local_server(port=0)
TOKEN_PATH.write_text(creds.to_json())

print(f"\nToken saved to: {TOKEN_PATH}")
print("\n--- Copy the content below into the YOUTUBE_TOKEN_JSON GitHub secret ---\n")
print(TOKEN_PATH.read_text())
