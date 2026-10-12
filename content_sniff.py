"""
برای لینک‌هایی که پسوند فایل مشخصی ندارن (مثل
example.com/download?id=123)، یه HEAD request می‌زنیم و از روی
Content-Type واقعی تشخیص می‌دیم که این یه فایل قابل‌دانلود مستقیمه
(aria2) یا یه صفحه‌ی وب معمولیه (که باید با yt-dlp امتحان بشه).
"""

from __future__ import annotations

import httpx

# این content-type ها یعنی «صفحه‌ی وب»، نه فایل مستقیم
_WEBPAGE_CONTENT_TYPES = ("text/html", "application/xhtml+xml")


async def classify_url(url: str, timeout: float = 6.0) -> str:
    """
    خروجی: "aria2" (فایل مستقیم)، "ytdlp" (صفحه‌ی وب/رسانه)، یا
    "unknown" اگه نشد تشخیص داد (HEAD جواب نداد، تایم‌اوت و...).
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.head(url)
            content_type = resp.headers.get("content-type", "").lower().split(";")[0].strip()
    except Exception:
        return "unknown"

    if not content_type:
        return "unknown"
    if any(content_type.startswith(w) for w in _WEBPAGE_CONTENT_TYPES):
        return "ytdlp"
    return "aria2"
