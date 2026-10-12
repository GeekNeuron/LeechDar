"""ابزارهای قابلیت‌اطمینان: retry با backoff، مدیریت FloodWait تلگرام، چک دیسک."""

from __future__ import annotations

import asyncio
import logging
import shutil

log = logging.getLogger("leechdar.reliability")


async def retry_async(func, *args, retries: int = 3, base_delay: float = 5.0, **kwargs):
    """
    func رو با آرگومان‌های داده‌شده اجرا می‌کنه. اگه با خطای موقتی شبکه
    مواجه بشه، با تاخیر نمایی (exponential backoff) دوباره امتحان
    می‌کنه. اگه تلگرام FloodWait بده (محدودیت نرخ)، دقیقاً به‌اندازه‌ی
    زمانی که خودش می‌گه صبر می‌کنه (نه backoff تصادفی).
    """
    from telegram.error import NetworkError, RetryAfter, TimedOut

    attempt = 0
    while True:
        try:
            return await func(*args, **kwargs)
        except RetryAfter as e:
            wait = getattr(e, "retry_after", base_delay) + 1
            log.warning("Telegram FloodWait: صبر %s ثانیه", wait)
            await asyncio.sleep(wait)
            # FloodWait جزو تلاش‌های شکست‌خورده حساب نمی‌شه، دوباره امتحان می‌کنیم
        except (TimedOut, NetworkError) as e:
            attempt += 1
            if attempt > retries:
                log.error("عملیات بعد از %s تلاش شکست خورد: %s", retries, e)
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.warning("خطای شبکه (%s)، تلاش %s/%s بعد از %.0f ثانیه", e, attempt, retries, delay)
            await asyncio.sleep(delay)
        except Exception as e:
            attempt += 1
            if attempt > retries:
                log.error("عملیات بعد از %s تلاش شکست خورد: %s", retries, e)
                raise
            delay = base_delay * (2 ** (attempt - 1))
            log.warning("خطا (%s)، تلاش %s/%s بعد از %.0f ثانیه", e, attempt, retries, delay)
            await asyncio.sleep(delay)


def free_disk_gb(path: str = ".") -> float:
    _, _, free = shutil.disk_usage(path)
    return free / (1024**3)
