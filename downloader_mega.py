"""
دانلود مستقیم از لینک‌های Mega.nz — چون Mega رمزنگاری سمت کلاینت
مخصوص خودش رو داره (کلید رمزگشایی تو خود لینک، بعد از #)، نمی‌شه
مثل لینک معمولی با aria2 دانلودش کرد؛ از کتابخونه‌ی غیررسمی
`mega.py` استفاده می‌کنیم که این پروتکل رو پیاده کرده.

⚠️ mega.py نگهداری فعالی نداره و ممکنه با تغییرات آینده‌ی
   مگا قطع بشه — اگه خطا داد، اول با نسخه‌ی جدیدتر `pip install -U mega.py` امتحان کن.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from mega import Mega


def is_mega_url(url: str) -> bool:
    return "mega.nz" in url or "mega.co.nz" in url


def _download_sync(url: str, out_dir: Path, email: str | None, password: str | None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    m = Mega()
    if email and password:
        api = m.login(email, password)
    else:
        api = m.login_anonymous()
    downloaded_path = api.download_url(url, dest_path=str(out_dir))
    return Path(downloaded_path)


async def download(
    url: str, out_dir: Path, email: str | None = None, password: str | None = None
) -> list[Path]:
    """
    دانلود یه فایل از مگا. اگه فایل تو یه فولدر عمومی مگا باشه
    (نه یه فایل تکی)، فعلاً پشتیبانی نمی‌شه — فقط لینک فایل تکی.
    """
    path = await asyncio.to_thread(_download_sync, url, out_dir, email, password)
    return [path]
