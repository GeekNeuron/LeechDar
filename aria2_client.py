"""
لایه‌ی واسط با aria2 — برای دانلود لینک مستقیم (HTTP/HTTPS/FTP)،
برخلاف yt-dlp که برای سایت‌های ویدیویی/رسانه‌ایه.

برخلاف qBittorrent، اینجا خودمون دیمون aria2c رو (اگه از قبل بالا
نباشه) به‌عنوان زیرفرآیند بغل ربات اجرا می‌کنیم — نیازی به سرویس
جدا یا کانتینر جدا نیست؛ فقط باینری aria2c باید نصب باشه.
"""

from __future__ import annotations

import atexit
import shutil
import subprocess
import time
from pathlib import Path

import aria2p

_daemon_proc: subprocess.Popen | None = None


def ensure_daemon_running(host: str, port: int, secret: str, download_dir: str, binary: str = "aria2c") -> None:
    """اگه دیمون از قبل رو این پورت جواب نده، یکی رو خودمون بالا میاریم."""
    global _daemon_proc

    if _is_daemon_alive(host, port, secret):
        return

    if shutil.which(binary) is None:
        raise RuntimeError(
            f"باینری «{binary}» پیدا نشد. نصبش کن (مثلاً: apt install aria2 / brew install aria2)."
        )

    Path(download_dir).mkdir(parents=True, exist_ok=True)
    args = [
        binary,
        "--enable-rpc",
        f"--rpc-listen-port={port}",
        f"--rpc-secret={secret}",
        f"--dir={download_dir}",
        "--continue=true",
        "--max-concurrent-downloads=5",
    ]
    _daemon_proc = subprocess.Popen(
        args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL
    )
    atexit.register(_shutdown_daemon)

    for _ in range(20):
        if _is_daemon_alive(host, port, secret):
            return
        time.sleep(0.25)
    raise RuntimeError("دیمون aria2c بالا نیومد (تایم‌اوت).")


def _is_daemon_alive(host: str, port: int, secret: str) -> bool:
    try:
        api = get_api(host, port, secret)
        api.client.get_version()
        return True
    except Exception:
        return False


def _shutdown_daemon() -> None:
    if _daemon_proc is not None:
        _daemon_proc.terminate()


def get_api(host: str, port: int, secret: str) -> aria2p.API:
    return aria2p.API(aria2p.Client(host=host, port=port, secret=secret))


def add_uri(api: aria2p.API, url: str, out_dir: str | None = None) -> str:
    """لینک رو به صف aria2 اضافه می‌کنه و gid برمی‌گردونه."""
    options = {"dir": out_dir} if out_dir else {}
    dl = api.add_uris([url], options=options)
    return dl.gid


def get_download(api: aria2p.API, gid: str):
    return api.get_download(gid)


def pause(api: aria2p.API, gid: str) -> None:
    api.get_download(gid).pause()


def resume(api: aria2p.API, gid: str) -> None:
    api.get_download(gid).resume()


def remove(api: aria2p.API, gid: str, delete_files: bool = True) -> None:
    d = api.get_download(gid)
    d.remove(files=delete_files)
