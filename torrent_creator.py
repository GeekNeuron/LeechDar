"""
ساخت یه فایل .torrent از یه فایل/پوشه‌ی محلی — برای «آپلود به‌عنوان
تورنت»: فایلی که از تلگرام می‌گیری رو تبدیل به تورنت کن، به
qBittorrent بده تا سیدش کنه، و مگنت‌لینک رو برگردون تا بقیه بتونن
ازت دانلود کنن.

بدون tracker هم کار می‌کنه (از DHT/PEX خود BitTorrent استفاده
می‌کنه)، پس نیازی به سرور tracker جدا نیست.
"""

from __future__ import annotations

from pathlib import Path

import torf

# چندتا tracker عمومی و رایگان، برای بهتر پیدا شدن سیدرها (اختیاری؛
# DHT به‌تنهایی هم کار می‌کنه ولی با tracker سریع‌تر وصل می‌شن)
DEFAULT_TRACKERS = [
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://tracker.openbittorrent.com:6969/announce",
]


def create_torrent(
    source_path: Path,
    output_torrent_path: Path,
    trackers: list[str] | None = None,
    private: bool = False,
) -> tuple[Path, str]:
    """
    source_path: فایل یا پوشه‌ای که می‌خوای تورنتش کنی
    خروجی: (مسیر فایل .torrent ساخته‌شده، لینک مگنت)
    """
    t = torf.Torrent(
        path=str(source_path),
        trackers=trackers if trackers is not None else DEFAULT_TRACKERS,
        private=private,
    )
    t.generate()
    output_torrent_path.parent.mkdir(parents=True, exist_ok=True)
    t.write(str(output_torrent_path), overwrite=True)
    return output_torrent_path, str(t.magnet())
