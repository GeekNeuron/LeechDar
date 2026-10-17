"""
چندتا عملیات ساده‌ی رایج روی ویدیو/صدا با ffmpeg — قبل از آپلود،
رو فایل آماده‌شده اجرا می‌شن. چیز پیچیده‌ای نیست؛ فقط دور زدن
subprocess با چکِ خطای درست.
"""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path


def is_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def _run_ffmpeg(args: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg", "-y", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg با کد {proc.returncode} خطا داد: {stderr.decode(errors='ignore')[-800:]}")


async def convert_format(input_path: Path, output_path: Path) -> Path:
    """تبدیل فرمت بر اساس پسوند output_path (مثلاً .mp4 -> .mkv)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_ffmpeg(["-i", str(input_path), str(output_path)])
    return output_path


async def trim(input_path: Path, output_path: Path, start_seconds: float, duration_seconds: float) -> Path:
    """یه تکه از ویدیو/صدا رو می‌بره، بدون encode دوباره (سریع و بدون افت کیفیت)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_ffmpeg(
        ["-ss", str(start_seconds), "-i", str(input_path), "-t", str(duration_seconds), "-c", "copy", str(output_path)]
    )
    return output_path


async def extract_audio(input_path: Path, output_path: Path) -> Path:
    """فقط صدا رو جدا می‌کنه (مثلاً از یه ویدیو، mp3 بساز)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_ffmpeg(["-i", str(input_path), "-vn", "-acodec", "libmp3lame", "-q:a", "2", str(output_path)])
    return output_path


async def add_text_watermark(
    input_path: Path, output_path: Path, text: str, position: str = "bottom-right"
) -> Path:
    """یه متن ساده رو گوشه‌ی ویدیو می‌نویسه (نیازی به فونت خاص نداره، از فونت پیش‌فرض ffmpeg استفاده می‌کنه)."""
    positions = {
        "bottom-right": "x=w-tw-10:y=h-th-10",
        "bottom-left": "x=10:y=h-th-10",
        "top-right": "x=w-tw-10:y=10",
        "top-left": "x=10:y=10",
    }
    pos = positions.get(position, positions["bottom-right"])
    escaped = text.replace("'", r"\'").replace(":", r"\:")
    filt = f"drawtext=text='{escaped}':fontcolor=white:fontsize=24:box=1:boxcolor=black@0.5:boxborderw=5:{pos}"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_ffmpeg(["-i", str(input_path), "-vf", filt, "-codec:a", "copy", str(output_path)])
    return output_path


async def generate_thumbnail(input_path: Path, output_path: Path, timestamp_seconds: float = 3.0) -> Path:
    """یه فریم از ویدیو رو به‌عنوان عکس می‌گیره."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    await _run_ffmpeg(["-ss", str(timestamp_seconds), "-i", str(input_path), "-frames:v", "1", str(output_path)])
    return output_path


async def get_duration_seconds(input_path: Path) -> float:
    """طول ویدیو/صدا رو بر می‌گردونه (برای چک‌کردن اینکه trim معتبره یا نه)."""
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(input_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe خطا داد: {stderr.decode(errors='ignore')[-500:]}")
    return float(stdout.decode().strip())
