"""
دانلود از طریق yt-dlp — پشتیبانی از یوتیوب و صدها سایت دیگه
(اینستاگرام، ساندکلاود، توییتر/X و...) — فقط لینک بده، خودش تشخیص میده.

برخلاف تورنت که پیشرفتش رو از qBittorrent می‌خونیم، اینجا خود
yt-dlp با progress_hook گزارش میده؛ درصدش رو تو دیکشنری سراسری
_progress نگه می‌داریم تا bot.py بتونه تو /status نشونش بده.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yt_dlp

# key -> (0.0 تا 1.0)
_progress: dict[str, float] = {}


def is_supported_url(url: str) -> bool:
    return url.startswith("http://") or url.startswith("https://")


def get_progress(key: str) -> float:
    return _progress.get(key, 0.0)


def clear_progress(key: str) -> None:
    _progress.pop(key, None)


def _make_hook(key: str):
    def hook(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes", 0)
            if total:
                _progress[key] = downloaded / total
        elif d.get("status") == "finished":
            _progress[key] = 1.0

    return hook


def _download_sync(
    url: str,
    out_dir: Path,
    key: str,
    format_selector: str,
    subtitles: bool = False,
    sub_langs: str = "fa,en",
) -> tuple[list[Path], str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(out_dir / "%(title).150B [%(id)s].%(ext)s"),
        "format": format_selector,
        "noplaylist": False,
        "quiet": True,
        "no_warnings": True,
        "progress_hooks": [_make_hook(key)],
        "merge_output_format": "mp4",
    }
    if subtitles:
        opts.update(
            {
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": [s.strip() for s in sub_langs.split(",") if s.strip()],
                "subtitlesformat": "srt/best",
            }
        )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)

    entries = info["entries"] if info.get("_type") == "playlist" else [info]
    title = info.get("title", url)
    files: list[Path] = []
    for e in entries:
        if not e:
            continue
        downloads = e.get("requested_downloads") or []
        for d in downloads:
            fp = d.get("filepath")
            if fp:
                files.append(Path(fp))
        if not downloads and e.get("_filename"):
            p = Path(e["_filename"])
            if p.exists():
                files.append(p)

    if subtitles:
        # فایل‌های زیرنویس (srt/vtt) جدا از ویدیو نوشته می‌شن؛ همه‌ی
        # فایل‌های تازه‌ساخته‌شده تو پوشه رو هم اضافه کن (بدون تکراری)
        known = {f.resolve() for f in files}
        for sub_file in out_dir.glob("*.srt"):
            if sub_file.resolve() not in known:
                files.append(sub_file)

    return files, title


async def download(
    url: str,
    out_dir: Path,
    key: str,
    format_selector: str = "bestvideo+bestaudio/best",
    subtitles: bool = False,
    sub_langs: str = "fa,en",
) -> tuple[list[Path], str]:
    """
    لینک رو دانلود می‌کنه (تو ترد جدا، چون yt-dlp خودش async نیست).
    خروجی: (لیست فایل‌های دانلودشده، عنوان محتوا)
    """
    try:
        return await asyncio.to_thread(_download_sync, url, out_dir, key, format_selector, subtitles, sub_langs)
    finally:
        clear_progress(key)
