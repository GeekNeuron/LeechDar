"""
اعتبارسنجی initData که تلگرام WebApp به فرانت‌اند می‌ده — طبق الگوریتم
رسمی: https://core.telegram.org/bots/webapps#validating-data-received-via-the-web-app

این جایگزین رمز عبوره: چون initData با هش HMAC توسط خود تلگرام امضا
شده، اگه معتبر باشه یعنی درخواست واقعاً از همون کاربر تلگرامی اومده
که ربات رو باز کرده — نیازی به لاگین دستی نیست.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl


def validate_init_data(init_data: str, bot_token: str, max_age_seconds: int = 86400) -> dict | None:
    """
    اگه initData معتبر و تازه باشه، دیکشنری کاربر (از فیلد user) رو
    برمی‌گردونه؛ وگرنه None.
    """
    try:
        pairs = dict(parse_qsl(init_data, strict_parsing=True))
    except ValueError:
        return None

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed_hash, received_hash):
        return None

    auth_date = pairs.get("auth_date")
    if auth_date and (time.time() - int(auth_date)) > max_age_seconds:
        return None  # initData قدیمیه، احتمال replay attack

    user_raw = pairs.get("user")
    if not user_raw:
        return None
    try:
        return json.loads(user_raw)
    except json.JSONDecodeError:
        return None
