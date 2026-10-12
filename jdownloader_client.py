"""
فوروارد لینک به JDownloader از طریق سرویس MyJDownloader — برای
هاست‌های پریمیوم یا سایت‌هایی که کپچا دارن و این ربات خودش
نمی‌تونه ازشون دانلود کنه. خود JDownloader (رو کامپیوتر خودت یا هر
جایی که بازش کردی) کار دانلود رو انجام می‌ده، نه این ربات — این
فقط لینک رو به صف JDownloader اضافه می‌کنه.

پیش‌نیاز: یه اکانت رایگان تو https://my.jdownloader.org بساز، همون
ایمیل/پسورد رو تو config.py بذار، و روی خود برنامه‌ی JDownloader که
باز داری وصلش کن به همون اکانت.
"""

from __future__ import annotations

import asyncio

import myjdapi


def _connect_sync(email: str, password: str, device_name: str, app_key: str = "LeechDar"):
    jd = myjdapi.Myjdapi()
    jd.set_app_key(app_key)
    jd.connect(email, password)
    device = jd.get_device(device_name)
    return jd, device


def _add_link_sync(email: str, password: str, device_name: str, url: str, package_name: str) -> None:
    jd, device = _connect_sync(email, password, device_name)
    try:
        device.linkgrabber.add_links(
            [{"autostart": True, "links": url, "packageName": package_name, "overwritePackagizerRules": False}]
        )
    finally:
        jd.disconnect()


async def add_link(email: str, password: str, device_name: str, url: str, package_name: str = "LeechDar") -> None:
    """لینک رو به صف JDownloader اضافه می‌کنه؛ خود دانلود تو JDownloader انجام می‌شه، نه اینجا."""
    await asyncio.to_thread(_add_link_sync, email, password, device_name, url, package_name)


async def list_devices(email: str, password: str, app_key: str = "LeechDar") -> list[str]:
    def _sync():
        jd = myjdapi.Myjdapi()
        jd.set_app_key(app_key)
        jd.connect(email, password)
        try:
            jd.update_devices()
            return [d["name"] for d in jd.list_devices()]
        finally:
            jd.disconnect()

    return await asyncio.to_thread(_sync)
