"""
ابزارهای عمومی فایل: ساخت زیپ، استخراج آرشیو، تقسیم به پارت‌های
دلخواه، و بازسازی از روی پارت‌ها.

اسپیلت اینجا یه "تقسیم باینری خام" هست (مثل روش سنتی split/join):
هر فایلی (زیپ یا غیرزیپ) رو به تیکه‌های هم‌سایز می‌بره. این روش با
هر نوع فایلی کار می‌کنه و وابسته به فرمت آرشیو خاصی نیست.
"""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

try:
    import py7zr
    HAS_7Z = True
except ImportError:
    HAS_7Z = False

try:
    import rarfile
    HAS_RAR = True
except ImportError:
    HAS_RAR = False


CHUNK_READ_SIZE = 8 * 1024 * 1024  # 8MB بلاک خوندن/نوشتن


def create_zip(file_paths: list[Path], output_path: Path, password: str | None = None) -> Path:
    """
    چند فایل (یا یه فایل تنها) رو در یه زیپ واحد قرار میده. اگه
    password داده بشه، با رمزنگاری AES (از pyzipper) ساخته می‌شه —
    زیپ‌های رمزدار معمولی ZipCrypto امنیت ضعیفی دارن، AES درسته.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if password:
        import pyzipper

        with pyzipper.AESZipFile(
            output_path, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES
        ) as zf:
            zf.setpassword(password.encode())
            for p in file_paths:
                if p.is_dir():
                    for sub in p.rglob("*"):
                        if sub.is_file():
                            zf.write(sub, arcname=str(sub.relative_to(p.parent)))
                else:
                    zf.write(p, arcname=p.name)
        return output_path

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
        for p in file_paths:
            if p.is_dir():
                for sub in p.rglob("*"):
                    if sub.is_file():
                        zf.write(sub, arcname=str(sub.relative_to(p.parent)))
            else:
                zf.write(p, arcname=p.name)
    return output_path


def split_file(path: Path, chunk_size_bytes: int, out_dir: Path | None = None) -> list[Path]:
    """
    فایل رو به پارت‌های هم‌سایز تقسیم می‌کنه: name.ext.001, .002, ...
    اگه فایل از chunk_size_bytes کوچیک‌تر باشه، همون فایل اصلی برگردونده میشه.
    """
    if path.stat().st_size <= chunk_size_bytes:
        return [path]

    out_dir = out_dir or path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    with open(path, "rb") as src:
        index = 1
        while True:
            chunk = src.read(chunk_size_bytes)
            if not chunk:
                break
            part_path = out_dir / f"{path.name}.{index:03d}"
            with open(part_path, "wb") as dst:
                dst.write(chunk)
            parts.append(part_path)
            index += 1
    return parts


def split_file_into_n_parts(path: Path, n: int, out_dir: Path | None = None) -> list[Path]:
    """
    برخلاف split_file (که سایز پارت رو می‌گیره)، اینجا تعداد پارت رو
    می‌گیری و سایز خودش محاسبه می‌شه — برای وقتی می‌خوای دقیقاً N تکه
    مساوی داشته باشی، نه هر چقدر که بشه.
    """
    if n < 1:
        raise ValueError("تعداد پارت باید حداقل ۱ باشه")
    size = path.stat().st_size
    chunk_size = max(1, -(-size // n))  # سقف تقسیم، تا پارت آخر خالی نمونه
    return split_file(path, chunk_size, out_dir=out_dir)


def join_parts(parts: list[Path], output_path: Path) -> Path:
    """
    برعکسِ split_file: پارت‌ها رو به ترتیب به هم می‌چسبونه.
    (این تابع رو معمولاً کسی اجرا می‌کنه که پارت‌ها رو از تلگرام/درایو
    دریافت کرده و می‌خواد فایل اصلی رو بازسازی کنه — به همراه اسکریپت
    مستقل join_parts.py هم ارائه شده.)
    """
    sorted_parts = sorted(parts, key=lambda p: p.name)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as dst:
        for part in sorted_parts:
            with open(part, "rb") as src:
                shutil.copyfileobj(src, dst, length=CHUNK_READ_SIZE)
    return output_path


def is_archive(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in (".zip", ".7z", ".rar")


def extract_archive(path: Path, out_dir: Path) -> list[Path]:
    """آرشیو zip / 7z / rar رو استخراج می‌کنه و لیست فایل‌های خروجی رو برمی‌گردونه."""
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()

    if suffix == ".zip":
        with zipfile.ZipFile(path) as zf:
            zf.extractall(out_dir)
    elif suffix == ".7z":
        if not HAS_7Z:
            raise RuntimeError("py7zr نصب نیست: pip install py7zr")
        with py7zr.SevenZipFile(path, mode="r") as zf:
            zf.extractall(path=out_dir)
    elif suffix == ".rar":
        if not HAS_RAR:
            raise RuntimeError(
                "rarfile نصب نیست یا باینری unrar در سیستم موجود نیست: "
                "pip install rarfile و نصب unrar/unrar-free"
            )
        with rarfile.RarFile(path) as rf:
            rf.extractall(out_dir)
    else:
        raise ValueError(f"فرمت آرشیو پشتیبانی‌نشده: {suffix}")

    return [p for p in out_dir.rglob("*") if p.is_file()]


def rename_file(path: Path, new_name: str) -> Path:
    """فایل رو تغییر نام میده و مسیر جدید رو برمی‌گردونه (پسوند اصلی حفظ میشه اگه ننوشته باشی)."""
    if "." not in new_name and path.suffix:
        new_name = new_name + path.suffix
    new_path = path.with_name(new_name)
    path.rename(new_path)
    return new_path
