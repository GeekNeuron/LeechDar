"""
اجرای مستقل: بعد از اینکه همه‌ی پارت‌های یه فایل (مثلاً movie.mkv.zip.001,
.002, ...) رو از تلگرام یا گوگل‌درایو دانلود کردی، همه رو تو یه پوشه بذار
و این اسکریپت رو بزن تا فایل اصلی بازسازی بشه.

استفاده:
    python join_parts.py "movie.mkv.zip" /path/to/folder/with/parts
"""

import sys
from pathlib import Path

from file_tools import join_parts


def main() -> None:
    if len(sys.argv) < 3:
        print("استفاده: python join_parts.py <نام_فایل_خروجی> <پوشه_پارت‌ها>")
        sys.exit(1)

    output_name = sys.argv[1]
    folder = Path(sys.argv[2])
    parts = sorted(folder.glob(f"{output_name}.*"))

    if not parts:
        print(f"هیچ پارتی با الگوی «{output_name}.*» تو پوشه {folder} پیدا نشد.")
        sys.exit(1)

    output_path = folder / output_name
    print(f"در حال چسبوندن {len(parts)} پارت به {output_path} ...")
    join_parts(parts, output_path)
    print("تموم شد ✅")


if __name__ == "__main__":
    main()
