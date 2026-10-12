"""
اعلان جایگزین وقتی خود تلگرام در دسترس نیست (مثلاً خطای شبکه مداوم
یا قطعی تلگرام). از ntfy.sh (یا هر سرور ntfy سلف‌هاست‌شده) استفاده
می‌کنه — یه سرویس push notification ساده و بدون نیاز به اکانت.
"""

from __future__ import annotations

import httpx


async def send_ntfy(topic_url: str, message: str, title: str | None = None, timeout: float = 10.0) -> bool:
    """
    topic_url نمونه: https://ntfy.sh/my-leechdar-alerts
    خروجی: True اگه موفق بود، False اگه نه (هیچ‌وقت exception نمی‌ده
    تا یه مسیر اعلان ناموفق، بقیه‌ی ربات رو خراب نکنه).
    """
    headers = {}
    if title:
        headers["Title"] = title.encode("utf-8")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(topic_url, content=message.encode("utf-8"), headers=headers)
            return resp.status_code < 300
    except Exception:
        return False
