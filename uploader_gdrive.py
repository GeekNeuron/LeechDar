"""
آپلود فایل به گوگل درایو.

راه‌اندازی اولیه (فقط یه بار):
1. برو به https://console.cloud.google.com و یه پروژه بساز
2. تو "APIs & Services > Library" فعال کن: Google Drive API
3. تو "APIs & Services > Credentials" یه "OAuth client ID" از نوع
   "Desktop app" بساز و فایل JSON رو دانلود کن، اسمش رو بذار
   credentials.json و کنار bot.py بذارش
4. بار اول که ربات آپلود به درایو انجام بده، یه لینک تو ترمینال چاپ
   میشه — بازش کن، با اکانت گوگلت اجازه بده، و توکن خودکار ذخیره
   میشه (gdrive_token.json) برای دفعات بعد.
"""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def get_drive_service(credentials_file: str, token_file: str):
    creds = None
    token_path = Path(token_file)
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


def upload_to_gdrive(
    credentials_file: str,
    token_file: str,
    file_path: Path,
    folder_id: str = "",
) -> str:
    """فایل رو آپلود می‌کنه و لینک قابل‌اشتراک‌گذاری رو برمی‌گردونه."""
    service = get_drive_service(credentials_file, token_file)

    metadata = {"name": file_path.name}
    if folder_id:
        metadata["parents"] = [folder_id]

    media = MediaFileUpload(str(file_path), resumable=True)
    uploaded = service.files().create(
        body=metadata, media_body=media, fields="id, webViewLink"
    ).execute()

    file_id = uploaded["id"]
    # عمومی‌کردن لینک برای دسترسی راحت (read-only برای هرکسی که لینک رو داشته باشه)
    service.permissions().create(
        fileId=file_id, body={"role": "reader", "type": "anyone"}
    ).execute()

    return uploaded.get("webViewLink", f"https://drive.google.com/file/d/{file_id}/view")
