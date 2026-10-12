"""
آپلود به GoFile.io — بدون نیاز به اکانت (لینک موقت/عمومی)، یا با
توکن اختیاری برای اکانت‌دار بودن (فایل‌ها تو حساب خودت بمونن).

مستندات: https://gofile.io/api
"""

from __future__ import annotations

from pathlib import Path

import httpx


async def upload_to_gofile(
    file_path: Path,
    api_token: str | None = None,
    timeout: float = 600.0,
    servers_url: str = "https://api.gofile.io/servers",
    upload_url_template: str = "https://{server}.gofile.io/uploadFile",
) -> str:
    async with httpx.AsyncClient(timeout=timeout) as client:
        servers_resp = await client.get(servers_url)
        servers_resp.raise_for_status()
        servers_data = servers_resp.json()
        if servers_data.get("status") != "ok" or not servers_data["data"]["servers"]:
            raise RuntimeError(f"GoFile: سرور آپلود پیدا نشد: {servers_data}")
        server = servers_data["data"]["servers"][0]["name"]

        upload_url = upload_url_template.format(server=server)
        with open(file_path, "rb") as f:
            files = {"file": (file_path.name, f)}
            data = {"token": api_token} if api_token else {}
            resp = await client.post(upload_url, files=files, data=data)
        resp.raise_for_status()
        result = resp.json()
        if result.get("status") != "ok":
            raise RuntimeError(f"GoFile خطا داد: {result}")
        return result["data"]["downloadPage"]
