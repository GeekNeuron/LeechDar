"""
آپلود فایل به تلگرام با دو حالت:

1) bot_api  — از طریق توکن ربات (python-telegram-bot). ساده، ولی سقفش
   ۵۰ مگابایته (محدودیت خود Bot API کلاد تلگرام).

2) pyrogram_user — از طریق حساب کاربری خودت (نه ربات)، با کتابخونه
   Pyrogram. سقفش ۲ گیگابایته و با تلگرام پریمیوم به ۴ گیگ می‌رسه.
   برای این حالت باید یه بار generate_session.py رو اجرا کنی تا
   SESSION_STRING ساخته بشه (نیاز به شماره تلفن و کد تایید داره).
"""

from __future__ import annotations

from pathlib import Path

from telegram import Bot


async def upload_via_bot_api(bot: Bot, chat_id: int, file_path: Path) -> None:
    with open(file_path, "rb") as f:
        await bot.send_document(chat_id, document=f, filename=file_path.name, read_timeout=120, write_timeout=120)


async def upload_via_pyrogram_user(
    api_id: int,
    api_hash: str,
    session_string: str,
    chat_id: int,
    file_path: Path,
    progress_callback=None,
) -> None:
    # ایمپورت لوکال تا کسایی که از این حالت استفاده نمی‌کنن مجبور به
    # نصب pyrogram نباشن
    from pyrogram import Client

    async with Client(
        "uploader_session",
        api_id=api_id,
        api_hash=api_hash,
        session_string=session_string,
        in_memory=True,
    ) as app:
        await app.send_document(
            chat_id,
            document=str(file_path),
            file_name=file_path.name,
            progress=progress_callback,
        )
