"""
آپلود به PixelDrain — بدون نیاز به اکانت هم کار می‌کنه؛ با API key
(اختیاری) فایل‌ها تو حساب خودت ثبت می‌شن.

مستندات: https://pixeldrain.com/api
"""

from __future__ import annotations

from pathlib import Path

import httpx


async def upload_to_pixeldrain(
    file_path: Path,
    api_key: str | None = None,
    timeout: float = 600.0,
    base_url: str = "https://pixeldrain.com/api/file",
) -> str:
    auth = ("", api_key) if api_key else None
    async with httpx.AsyncClient(timeout=timeout) as client:
        with open(file_path, "rb") as f:
            resp = await client.put(f"{base_url}/{file_path.name}", content=f.read(), auth=auth)
    resp.raise_for_status()
    data = resp.json()
    if "id" not in data:
        raise RuntimeError(f"PixelDrain خطا داد: {data}")
    return f"https://pixeldrain.com/u/{data['id']}"
