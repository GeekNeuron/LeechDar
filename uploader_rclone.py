"""
آپلود فایل با rclone — به هر کلاودی که rclone پشتیبانی می‌کنه (گوگل‌درایو،
Dropbox، S3، Mega، OneDrive و...)، فارغ از اینکه چه چیزی باشه.

پیش‌نیاز: خود باینری rclone باید نصب و remote موردنظر از قبل با
`rclone config` تنظیم شده باشه (این بیرون از کد ما انجام میشه، چون
نیاز به لاگین تعاملی داره).
"""

from __future__ import annotations

import asyncio
from pathlib import Path


async def upload_via_rclone(remote_path: str, file_path: Path, rclone_binary: str = "rclone") -> str:
    """
    فایل رو با `rclone copy` به remote_path (مثل "gdrive:LeechDar/") می‌فرسته.
    خروجی: پیام موفقیت rclone (برای نمایش/لاگ).
    """
    proc = await asyncio.create_subprocess_exec(
        rclone_binary,
        "copy",
        str(file_path),
        remote_path,
        "--progress",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        err = stderr.decode(errors="ignore").strip()[-800:]
        raise RuntimeError(f"rclone با کد {proc.returncode} خطا داد: {err}")
    return stdout.decode(errors="ignore").strip()[-500:]


async def check_rclone_available(rclone_binary: str = "rclone") -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            rclone_binary, "version", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        await proc.communicate()
        return proc.returncode == 0
    except FileNotFoundError:
        return False
