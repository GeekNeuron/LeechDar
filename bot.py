"""
LeechDar — ربات شخصی لیچ تورنت (فاز ۱: پایداری + انتخاب فایل + قابلیت‌اطمینان)
------------------------------------------------------------------------------
اضافه‌شده نسبت به نسخه‌ی قبل:
  • پایداری کامل با SQLite (db.py) — با ری‌استارت ربات صف از دست نمی‌ره
  • انتخاب فایل قبل/حین دانلود برای تورنت‌های چندفایلی (/files, /selectfiles)
  • صف و محدودیت تعداد دانلود هم‌زمان (MAX_CONCURRENT_DOWNLOADS)
  • جلوگیری از دانلود تکراری بر اساس هش مگنت (/forceadd برای دور زدن)
  • چک فضای آزاد دیسک قبل از هر دانلود جدید
  • تلاش مجدد خودکار + مدیریت FloodWait تلگرام روی آپلودها
  • لاگ فایل چرخشی (rotating) + دستور /log
  • health-check استارتاپ (اتصال qBittorrent قبل از بالا اومدن ربات)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaDocument, Update, WebAppInfo
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

import aria2_client
import config
import content_sniff
import db
import downloader_mega
import downloader_ytdlp
import ffmpeg_tools
import jdownloader_client
import uploader_gofile
import uploader_pixeldrain
import file_tools
import qbit_client
import notifier
import reliability
import torrent_creator
import uploader_rclone
from uploader_telegram import upload_via_bot_api, upload_via_pyrogram_user

try:
    from uploader_gdrive import upload_to_gdrive
except ImportError:
    upload_to_gdrive = None

# ------------------------------------------------------------------
# راه‌اندازی لاگ (کنسول + فایل چرخشی)
# ------------------------------------------------------------------
Path(config.LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
_file_handler = RotatingFileHandler(config.LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
_console_handler = logging.StreamHandler()
_fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
_file_handler.setFormatter(_fmt)
_console_handler.setFormatter(_fmt)
logging.basicConfig(level=getattr(logging, config.LOG_LEVEL, logging.INFO), handlers=[_console_handler, _file_handler])
log = logging.getLogger("leechdar.bot")

QB_CATEGORY = "tg-leech"
WORK_DIR = Path("work")
WORK_DIR.mkdir(exist_ok=True)

db.init_db()

# --- وضعیت زمان‌اجرا: حالا per-user — هر آیدی تلگرام تنظیمات خودش رو داره ---
# (چت‌آیدی خصوصی == کاربر، چون این ربات برای چت خصوصی با هر کاربر مجازه؛
# تو یه گروه مشترک معنی نداره چون همه یه chat_id مشترک می‌بینن)
VALID_DESTS = {"telegram", "gdrive", "rclone", "gofile", "pixeldrain"}
_DEFAULT_RUNTIME_FACTORY = lambda: {  # noqa: E731
    "destinations": {"telegram"},
    "upload_mode": config.TELEGRAM_UPLOAD_MODE,
    "is_premium": config.TELEGRAM_IS_PREMIUM,
    "split_size_mb": config.SPLIT_SIZE_MB,
    "force_zip": False,
    "zip_password": None,
    "split_parts_count": None,
}

_user_runtime_cache: dict[int, dict] = {}


def get_runtime(user_id: int) -> dict:
    """تنظیمات زمان‌اجرای همون کاربر رو برمی‌گردونه (اولین بار از دیتابیس لود می‌شه)."""
    if user_id not in _user_runtime_cache:
        defaults = _DEFAULT_RUNTIME_FACTORY()
        dest_saved = db.get_setting("destinations", user_id=user_id)
        _user_runtime_cache[user_id] = {
            "destinations": set(dest_saved.split(",")) if dest_saved else defaults["destinations"],
            "upload_mode": db.get_setting("upload_mode", defaults["upload_mode"], user_id=user_id),
            "is_premium": db.get_setting("is_premium", str(defaults["is_premium"]), user_id=user_id) == "True",
            "split_size_mb": (lambda v: int(v) if v not in (None, "None") else None)(
                db.get_setting("split_size_mb", str(defaults["split_size_mb"]), user_id=user_id)
            ),
            "force_zip": db.get_setting("force_zip", "False", user_id=user_id) == "True",
            "zip_password": db.get_setting("zip_password", user_id=user_id) or None,
            "split_parts_count": (lambda v: int(v) if v not in (None, "None") else None)(
                db.get_setting("split_parts_count", user_id=user_id)
            ),
        }
    return _user_runtime_cache[user_id]


def _save_runtime(user_id: int, key: str) -> None:
    r = get_runtime(user_id)
    if key == "destinations":
        db.set_setting(key, ",".join(sorted(r[key])), user_id=user_id)
    else:
        db.set_setting(key, str(r[key]), user_id=user_id)


# hash -> {"chat_id", "torrent_name", "files": [Path,...], "status": queued|downloading|ready,
#          "magnet_hash", "source": torrent|ytdlp, "source_url"}
pending: dict[str, dict] = {}


def load_pending_from_db() -> None:
    for t in db.all_tasks():
        pending[t["hash"]] = {
            "chat_id": t["chat_id"],
            "torrent_name": t["torrent_name"],
            "files": [Path(p) for p in t["files"]],
            "status": t["status"],
            "magnet_hash": t["magnet_hash"],
            "source": t["source"],
            "source_url": t["source_url"],
            "aria2_gid": t["aria2_gid"],
        }
    if pending:
        log.info("بازیابی %s کار ناتموم از دیتابیس", len(pending))


def _persist(h: str, item: dict) -> None:
    db.upsert_task(
        h,
        item["chat_id"],
        item["torrent_name"],
        item["status"],
        files=[str(p) for p in item["files"]],
        magnet_hash=item.get("magnet_hash"),
        source=item.get("source", "torrent"),
        source_url=item.get("source_url"),
        aria2_gid=item.get("aria2_gid"),
    )


def get_qb_settings() -> dict:
    """تنظیمات qBittorrent — اول دیتابیس (از /setup) رو چک می‌کنه، بعد config.py/env رو."""
    return {
        "host": db.get_setting("qb_host") or config.QB_HOST,
        "port": int(db.get_setting("qb_port") or config.QB_PORT),
        "username": db.get_setting("qb_username") or config.QB_USERNAME,
        "password": db.get_setting("qb_password") or config.QB_PASSWORD,
    }


def qb() -> object:
    s = get_qb_settings()
    return qbit_client.get_client(s["host"], s["port"], s["username"], s["password"])


_aria2_api = None


def get_aria2_api():
    global _aria2_api
    if _aria2_api is None:
        aria2_client.ensure_daemon_running(
            config.ARIA2_HOST, config.ARIA2_PORT, config.ARIA2_SECRET, config.ARIA2_DOWNLOAD_DIR, config.ARIA2_BINARY
        )
        _aria2_api = aria2_client.get_api(config.ARIA2_HOST, config.ARIA2_PORT, config.ARIA2_SECRET)
    return _aria2_api


_DIRECT_FILE_EXTENSIONS = (
    ".zip", ".rar", ".7z", ".tar", ".gz", ".tar.gz", ".iso", ".exe", ".msi",
    ".pdf", ".apk", ".deb", ".rpm", ".mp3", ".flac", ".mp4", ".mkv", ".avi",
    ".mov", ".bin", ".dmg", ".img",
)


def looks_like_direct_file(url: str) -> bool:
    from urllib.parse import urlparse

    path = urlparse(url).path.lower()
    return path.endswith(_DIRECT_FILE_EXTENSIONS)


def is_allowed(user_id: int) -> bool:
    if config.ALLOWED_USER_IDS and user_id in config.ALLOWED_USER_IDS:
        return True
    return db.is_user_allowed(user_id)


def is_owner(user_id: int) -> bool:
    if config.ALLOWED_USER_IDS and db.count_allowed_users() == 0:
        return user_id in config.ALLOWED_USER_IDS  # تنظیم‌شده با config، همه معادل مالک
    return db.is_user_owner(user_id)


def try_auto_claim(user_id: int) -> bool:
    """
    اگه هیچ‌کس هنوز صاحب ربات نشده (نه تو config، نه تو دیتابیس)،
    اولین کسی که پیام بده خودکار مالک می‌شه — برای کلون‌وران بدون
    نیاز به دونستن آیدی تلگرام از قبل.
    """
    if config.ALLOWED_USER_IDS:
        return False  # صاحب از قبل تو config مشخص شده
    if db.count_allowed_users() > 0:
        return False  # یکی دیگه قبلاً claim کرده
    db.add_allowed_user(user_id, is_owner=True)
    log.info("کاربر %s به‌عنوان مالک (اولین claim) ثبت شد", user_id)
    return True


def current_cap_mb(user_id: int) -> int:
    r = get_runtime(user_id)
    if r["upload_mode"] == "bot_api":
        return 50
    return 4000 if r["is_premium"] else 2000


def current_split_size_bytes(user_id: int) -> int:
    mb = get_runtime(user_id)["split_size_mb"] or current_cap_mb(user_id)
    return mb * 1024 * 1024


_BTIH_RE = re.compile(r"btih:([A-Za-z0-9]{32,40})", re.IGNORECASE)


def extract_btih(magnet: str) -> str | None:
    m = _BTIH_RE.search(magnet)
    return m.group(1).lower() if m else None


def is_within_schedule() -> bool:
    """اگه زمان‌بندی خاموش باشه همیشه True؛ وگرنه فقط تو بازه‌ی ساعتی مجازه."""
    if not config.SCHEDULE_ENABLED:
        return True
    hour = time.localtime().tm_hour
    start, end = config.SCHEDULE_START_HOUR, config.SCHEDULE_END_HOUR
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end  # بازه‌ای که از نیمه‌شب رد می‌شه (مثلاً ۲۳ تا ۷)


def active_downloading_count() -> int:
    return sum(1 for t in pending.values() if t["status"] == "downloading")


def promote_queued(client, app: Application) -> None:
    """اگه جای خالی تو صف دانلود هست و تو بازه‌ی زمان‌بندی مجازیم، اولین کار queued رو شروع می‌کنه."""
    if active_downloading_count() >= config.MAX_CONCURRENT_DOWNLOADS or not is_within_schedule():
        return
    for h, item in pending.items():
        if item["status"] == "queued":
            item["status"] = "downloading"
            if item.get("source") == "ytdlp":
                _persist(h, item)
                asyncio.create_task(_run_ytdlp(app, h, item))
            elif item.get("source") == "mega":
                _persist(h, item)
                asyncio.create_task(_run_mega(app, h, item))
            elif item.get("source") == "aria2":
                gid = aria2_client.add_uri(get_aria2_api(), item["source_url"], config.ARIA2_DOWNLOAD_DIR)
                item["aria2_gid"] = gid
                _persist(h, item)
            else:
                qbit_client.resume_torrent(client, h)
                _persist(h, item)
            log.info("کار %s از صف وارد دانلود شد", h[:8])
            return


# ------------------------------------------------------------------
# دستورات پایه
# ------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    setup_notice = ""
    try:
        await asyncio.to_thread(qb)
    except Exception:
        setup_notice = "⚠️ qBittorrent هنوز وصل نیست — اول /setup رو بزن تا اتصالش رو تنظیم کنی.\n\n"

    await update.message.reply_text(
        setup_notice + "سلام! لینک مگنت، فایل .torrent، لینک مستقیم/یوتیوب/هر سایت دیگه بفرست "
        "— خودم تشخیص می‌دم (حتی بدون پسوند مشخص). یه فایل معمولی (غیر-تورنت) "
        "بفرستی، ازش برات تورنت می‌سازم و سیدش می‌کنم.\n\n"
        "راه‌اندازی اولیه: /setup (تنظیم اتصال qBittorrent)\n"
        "مدیریت: /status /pause /resume /cancel /cancelconfirm <hash8>\n"
        "تنظیمات: /destination /uploadmode /premium /splitsize /splitparts /tozip "
        "/zippassword /seedlimit\n"
        "فایل: /files <hash8> — /selectfiles <hash8> <1,2,3>\n"
        "ویرایش ویدیو/صدا: /convert /trim /watermark /thumbnail /extractaudio\n"
        "RSS: /rssadd /rsslist /rssremove /rssrule /rssrules /rssruledel\n"
        "داشبورد تو خود تلگرام: /webapp\n"
        "جستجو: /search <عبارت> — /searchadd <شماره>\n"
        "بک‌آپ: /backup — /restore (با ریپلای رو فایل بک‌آپ)\n"
        "مدیریت کاربران (فقط مالک): /adduser /removeuser /listusers\n"
        "ابزار: /extract /rename /mergezip /upload /forceadd /ytdl /leech /mega /jdadd /log /update\n"
    )


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update.effective_user.id):
        return
    client = qb()
    torrents = client.torrents_info(category=QB_CATEGORY)
    lines = []
    for t in torrents:
        st = pending.get(t.hash, {}).get("status", t.state)
        lines.append(f"• {t.hash[:8]} — {t.name[:40]} — {round(t.progress*100,1)}% — {st}")
    for h, item in pending.items():
        if item.get("source") == "ytdlp":
            pct = round(downloader_ytdlp.get_progress(h) * 100, 1)
            lines.append(f"• {h[:8]} — {item['torrent_name'][:40]} — {pct}% — {item['status']} (yt-dlp)")
        elif item.get("source") == "aria2":
            pct = 0.0
            if item.get("aria2_gid"):
                try:
                    d = aria2_client.get_download(get_aria2_api(), item["aria2_gid"])
                    pct = round(d.progress, 1)
                except Exception:
                    pass
            lines.append(f"• {h[:8]} — {item['torrent_name'][:40]} — {pct}% — {item['status']} (aria2)")
    if not lines:
        await update.message.reply_text("هیچ دانلودی در جریان نیست.")
        return
    r = get_runtime(update.effective_user.id)
    free = reliability.free_disk_gb(config.DISK_CHECK_PATH)
    lines.append(
        f"\nمقصد: {','.join(sorted(r['destinations']))} | حالت: {r['upload_mode']} | "
        f"سقف: {current_cap_mb(update.effective_user.id)}MB | فضای آزاد: {free:.1f}GB | "
        f"هم‌زمان: {active_downloading_count()}/{config.MAX_CONCURRENT_DOWNLOADS}"
        + (" | ⏰ خارج از بازه‌ی زمان‌بندی" if not is_within_schedule() else "")
    )
    await update.message.reply_text("\n".join(lines))


async def cmd_pause(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /pause <hash8>")
    qbit_client.pause_torrent(qb(), context.args[0])
    await update.message.reply_text("⏸ متوقف شد.")


async def cmd_resume(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /resume <hash8>")
    qbit_client.resume_torrent(qb(), context.args[0])
    await update.message.reply_text("▶️ ازسرگیری شد.")


async def cmd_cancel(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /cancel <hash8>")
    short_hash = context.args[0]
    h = _full_hash(short_hash)
    if h not in pending and not qbit_client.find_by_short_hash(qb(), QB_CATEGORY, short_hash):
        return await update.message.reply_text("پیدا نشد.")

    if config.REQUIRE_CANCEL_CONFIRM:
        name = pending.get(h, {}).get("torrent_name", short_hash)
        return await update.message.reply_text(
            f"⚠️ مطمئنی می‌خوای «{name}» رو لغو و فایل‌هاش رو کامل پاک کنی؟ "
            f"این برگشت‌پذیر نیست.\nبرای تایید نهایی:\n/cancelconfirm {short_hash}"
        )

    await _do_cancel(update, context, short_hash)


async def cmd_cancelconfirm(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /cancelconfirm <hash8>")
    await _do_cancel(update, context, context.args[0])


async def _do_cancel(update, context, short_hash: str) -> None:
    h = _full_hash(short_hash)
    item = pending.get(h)

    if item and item.get("source") == "ytdlp":
        if item["status"] == "downloading":
            return await update.message.reply_text(
                "⚠️ دانلود yt-dlp در حال اجراست و نمی‌شه وسط راه لغوش کرد؛ صبر کن تموم بشه."
            )
        pending.pop(h, None)
        db.delete_task(h)
        promote_queued(qb(), context.application)
        return await update.message.reply_text("🗑 از صف حذف شد.")

    if item and item.get("source") == "aria2":
        if item["status"] == "downloading" and item.get("aria2_gid"):
            try:
                aria2_client.remove(get_aria2_api(), item["aria2_gid"], delete_files=True)
            except Exception as e:
                log.warning("aria2 remove failed: %s", e)
        pending.pop(h, None)
        db.delete_task(h)
        promote_queued(qb(), context.application)
        return await update.message.reply_text("🗑 لغو و فایل حذف شد.")

    client = qb()
    t = qbit_client.find_by_short_hash(client, QB_CATEGORY, short_hash)
    if not t:
        return await update.message.reply_text("پیدا نشد.")
    qbit_client.cancel_torrent(client, t.hash, delete_files=True)
    pending.pop(t.hash, None)
    db.delete_task(t.hash)
    promote_queued(client, context.application)
    await update.message.reply_text("🗑 لغو و فایل‌ها حذف شدن.")


async def cmd_destination(update, context):
    if not context.args:
        return await update.message.reply_text(
            "استفاده: /destination telegram|gdrive|rclone|gofile|pixeldrain (چندتایی با کاما)"
        )
    requested = {d.strip() for d in context.args[0].split(",") if d.strip()}
    if not requested or not requested.issubset(VALID_DESTS):
        return await update.message.reply_text(f"مقادیر مجاز: {', '.join(sorted(VALID_DESTS))}")
    if "gdrive" in requested and not config.GDRIVE_ENABLED:
        return await update.message.reply_text("GDRIVE_ENABLED خاموشه؛ README رو ببین.")
    if "rclone" in requested and not (config.RCLONE_ENABLED and config.RCLONE_REMOTE):
        return await update.message.reply_text("RCLONE_ENABLED خاموشه یا RCLONE_REMOTE تنظیم نشده.")
    if "gofile" in requested and not config.GOFILE_ENABLED:
        return await update.message.reply_text("GOFILE_ENABLED خاموشه.")
    if "pixeldrain" in requested and not config.PIXELDRAIN_ENABLED:
        return await update.message.reply_text("PIXELDRAIN_ENABLED خاموشه.")
    uid = update.effective_user.id
    get_runtime(uid)["destinations"] = requested
    _save_runtime(uid, "destinations")
    await update.message.reply_text(f"مقصد آپلود: {', '.join(sorted(requested))}")


async def cmd_uploadmode(update, context):
    if not context.args or context.args[0] not in ("bot", "user"):
        return await update.message.reply_text("استفاده: /uploadmode bot|user")
    mode = "bot_api" if context.args[0] == "bot" else "pyrogram_user"
    if mode == "pyrogram_user" and not config.PYROGRAM_SESSION_STRING:
        return await update.message.reply_text("اول generate_session.py رو اجرا کن.")
    uid = update.effective_user.id
    get_runtime(uid)["upload_mode"] = mode
    _save_runtime(uid, "upload_mode")
    await update.message.reply_text(f"حالت آپلود: {mode} (سقف {current_cap_mb(uid)}MB)")


async def cmd_premium(update, context):
    if not context.args or context.args[0] not in ("on", "off"):
        return await update.message.reply_text("استفاده: /premium on|off")
    uid = update.effective_user.id
    get_runtime(uid)["is_premium"] = context.args[0] == "on"
    _save_runtime(uid, "is_premium")
    await update.message.reply_text(f"سقف جدید: {current_cap_mb(uid)}MB")


async def cmd_splitsize(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /splitsize <MB> یا /splitsize auto")
    uid = update.effective_user.id
    if context.args[0] == "auto":
        get_runtime(uid)["split_size_mb"] = None
    else:
        try:
            get_runtime(uid)["split_size_mb"] = int(context.args[0])
        except ValueError:
            return await update.message.reply_text("عدد معتبر بده.")
    _save_runtime(uid, "split_size_mb")
    await update.message.reply_text(f"سایز پارت: {current_split_size_bytes(uid) // (1024*1024)}MB")


async def cmd_tozip(update, context):
    if not context.args or context.args[0] not in ("on", "off"):
        return await update.message.reply_text("استفاده: /tozip on|off")
    uid = update.effective_user.id
    get_runtime(uid)["force_zip"] = context.args[0] == "on"
    _save_runtime(uid, "force_zip")
    await update.message.reply_text(f"زیپ اجباری: {get_runtime(uid)['force_zip']}")


async def cmd_zippassword(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /zippassword <رمز> یا /zippassword off")
    uid = update.effective_user.id
    if context.args[0] == "off":
        get_runtime(uid)["zip_password"] = None
        _save_runtime(uid, "zip_password")
        return await update.message.reply_text("زیپ رمزدار خاموش شد.")
    get_runtime(uid)["zip_password"] = context.args[0]
    _save_runtime(uid, "zip_password")
    await update.message.reply_text(
        "رمز زیپ تنظیم شد. از این به بعد همه‌چیز قبل از آپلود رمزدار زیپ می‌شه "
        "(AES) — رمز رو جایی یادداشت کن، گم بشه قابل بازیابی نیست."
    )


async def cmd_splitparts(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /splitparts <تعداد> یا /splitparts auto")
    uid = update.effective_user.id
    if context.args[0] == "auto":
        get_runtime(uid)["split_parts_count"] = None
        _save_runtime(uid, "split_parts_count")
        return await update.message.reply_text("اسپیلت برگشت به حالت «بر اساس سایز».")
    try:
        n = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("عدد معتبر بده.")
    get_runtime(uid)["split_parts_count"] = n
    _save_runtime(uid, "split_parts_count")
    await update.message.reply_text(f"از این به بعد هر فایل دقیقاً به {n} پارت مساوی تقسیم می‌شه.")


async def cmd_rename(update, context):
    if len(context.args) < 2:
        return await update.message.reply_text("استفاده: /rename <hash8> <نام‌جدید>")
    short_hash, new_name = context.args[0], " ".join(context.args[1:])
    h = _full_hash(short_hash)
    item = pending.get(h)
    if not item or not item["files"]:
        return await update.message.reply_text("این تورنت آماده‌ی رنیم نیست.")
    new_path = file_tools.rename_file(item["files"][0], new_name)
    item["files"][0] = new_path
    _persist(h, item)
    await update.message.reply_text(f"تغییر نام شد به: {new_path.name}")


def _get_pending_file(short_hash: str):
    """فایل اول یه تسک pending رو برمی‌گردونه، یا None اگه پیدا نشد."""
    h = _full_hash(short_hash)
    item = pending.get(h)
    if not item or not item["files"]:
        return None, None, None
    return h, item, item["files"][0]


async def cmd_convert(update, context):
    if not ffmpeg_tools.is_available():
        return await update.message.reply_text("ffmpeg رو سیستم نصب نیست.")
    if len(context.args) < 2:
        return await update.message.reply_text("استفاده: /convert <hash8> <پسوند مقصد، مثل mp4>")
    h, item, src = _get_pending_file(context.args[0])
    if not src:
        return await update.message.reply_text("این تورنت آماده نیست.")
    ext = context.args[1].lstrip(".")
    out = WORK_DIR / "ffmpeg" / f"{src.stem}.{ext}"
    await update.message.reply_text(f"🎬 در حال تبدیل به .{ext}...")
    try:
        new_path = await ffmpeg_tools.convert_format(src, out)
    except Exception as e:
        return await update.message.reply_text(f"❌ خطا: {e}")
    item["files"][0] = new_path
    _persist(h, item)
    await update.message.reply_text(f"✅ تبدیل شد: {new_path.name}")


async def cmd_trim(update, context):
    if not ffmpeg_tools.is_available():
        return await update.message.reply_text("ffmpeg رو سیستم نصب نیست.")
    if len(context.args) < 3:
        return await update.message.reply_text("استفاده: /trim <hash8> <شروع_ثانیه> <مدت_ثانیه>")
    h, item, src = _get_pending_file(context.args[0])
    if not src:
        return await update.message.reply_text("این تورنت آماده نیست.")
    try:
        start, duration = float(context.args[1]), float(context.args[2])
    except ValueError:
        return await update.message.reply_text("شروع و مدت باید عدد باشن (ثانیه).")
    out = WORK_DIR / "ffmpeg" / f"{src.stem}_trim{src.suffix}"
    await update.message.reply_text(f"✂️ در حال برش از ثانیه {start} به مدت {duration}...")
    try:
        new_path = await ffmpeg_tools.trim(src, out, start, duration)
    except Exception as e:
        return await update.message.reply_text(f"❌ خطا: {e}")
    item["files"][0] = new_path
    _persist(h, item)
    await update.message.reply_text(f"✅ برش انجام شد: {new_path.name}")


async def cmd_watermark(update, context):
    if not ffmpeg_tools.is_available():
        return await update.message.reply_text("ffmpeg رو سیستم نصب نیست.")
    if len(context.args) < 2:
        return await update.message.reply_text("استفاده: /watermark <hash8> <متن>")
    h, item, src = _get_pending_file(context.args[0])
    if not src:
        return await update.message.reply_text("این تورنت آماده نیست.")
    text = " ".join(context.args[1:])
    out = WORK_DIR / "ffmpeg" / f"{src.stem}_wm{src.suffix}"
    await update.message.reply_text("🖊 در حال اضافه‌کردن واترمارک...")
    try:
        new_path = await ffmpeg_tools.add_text_watermark(src, out, text)
    except Exception as e:
        return await update.message.reply_text(f"❌ خطا: {e}")
    item["files"][0] = new_path
    _persist(h, item)
    await update.message.reply_text(f"✅ واترمارک اضافه شد: {new_path.name}")


async def cmd_thumbnail(update, context):
    if not ffmpeg_tools.is_available():
        return await update.message.reply_text("ffmpeg رو سیستم نصب نیست.")
    if not context.args:
        return await update.message.reply_text("استفاده: /thumbnail <hash8> [ثانیه، پیش‌فرض ۳]")
    h, item, src = _get_pending_file(context.args[0])
    if not src:
        return await update.message.reply_text("این تورنت آماده نیست.")
    ts = float(context.args[1]) if len(context.args) > 1 else 3.0
    out = WORK_DIR / "ffmpeg" / f"{src.stem}_thumb.jpg"
    try:
        thumb_path = await ffmpeg_tools.generate_thumbnail(src, out, ts)
    except Exception as e:
        return await update.message.reply_text(f"❌ خطا: {e}")
    with open(thumb_path, "rb") as f:
        await context.bot.send_photo(update.effective_chat.id, photo=f)


async def cmd_extractaudio(update, context):
    if not ffmpeg_tools.is_available():
        return await update.message.reply_text("ffmpeg رو سیستم نصب نیست.")
    if not context.args:
        return await update.message.reply_text("استفاده: /extractaudio <hash8>")
    h, item, src = _get_pending_file(context.args[0])
    if not src:
        return await update.message.reply_text("این تورنت آماده نیست.")
    out = WORK_DIR / "ffmpeg" / f"{src.stem}.mp3"
    await update.message.reply_text("🎵 در حال استخراج صدا...")
    try:
        new_path = await ffmpeg_tools.extract_audio(src, out)
    except Exception as e:
        return await update.message.reply_text(f"❌ خطا: {e}")
    item["files"][0] = new_path
    _persist(h, item)
    await update.message.reply_text(f"✅ صدا استخراج شد: {new_path.name}")


async def cmd_extract(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /extract <hash8>")
    h = _full_hash(context.args[0])
    item = pending.get(h)
    if not item:
        return await update.message.reply_text("پیدا نشد یا هنوز تموم نشده.")
    archive = next((f for f in item["files"] if file_tools.is_archive(f)), None)
    if not archive:
        return await update.message.reply_text("آرشیوی پیدا نشد.")
    try:
        extracted = file_tools.extract_archive(archive, WORK_DIR / f"extract_{archive.stem}")
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    item["files"] = extracted
    _persist(h, item)
    await update.message.reply_text(f"استخراج شد: {len(extracted)} فایل. حالا /upload بزن.")


async def cmd_mergezip(update, context):
    if len(context.args) < 2:
        return await update.message.reply_text("استفاده: /mergezip <hash8> <hash8> ...")
    all_files: list[Path] = []
    for short_hash in context.args:
        item = pending.get(_full_hash(short_hash))
        if not item:
            return await update.message.reply_text(f"{short_hash} پیدا نشد.")
        all_files.extend(item["files"])
    zip_path = WORK_DIR / "merged.zip"
    file_tools.create_zip(all_files, zip_path)
    merged_key = "merged_" + "_".join(a[:8] for a in context.args)
    pending[merged_key] = {
        "chat_id": update.effective_chat.id,
        "torrent_name": "merged.zip",
        "files": [zip_path],
        "status": "ready",
        "magnet_hash": None,
        "source": "merged",
        "source_url": None,
    }
    await update.message.reply_text(f"زیپ ادغامی ساخته شد:\n/upload {merged_key}")


async def cmd_upload(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /upload <hash8>")
    key = context.args[0] if context.args[0].startswith("merged_") else _full_hash(context.args[0])
    item = pending.get(key)
    if not item:
        return await update.message.reply_text("پیدا نشد.")
    await do_upload(context.application, key, item)


async def cmd_files(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /files <hash8>")
    h = _full_hash(context.args[0])
    try:
        files = qbit_client.list_files(qb(), h)
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    lines = [
        f"{f.id}: {f.name} — {f.size/(1024*1024):.1f}MB — {'✅' if f.priority>0 else '⛔'}"
        for f in files
    ]
    lines.append("\nبرای انتخاب: /selectfiles <hash8> <شماره,شماره,...>")
    await update.message.reply_text("\n".join(lines))


async def cmd_selectfiles(update, context):
    if len(context.args) < 2:
        return await update.message.reply_text("استفاده: /selectfiles <hash8> <1,2,3>")
    h = _full_hash(context.args[0])
    try:
        indices = {int(x) for x in context.args[1].split(",")}
    except ValueError:
        return await update.message.reply_text("شماره‌ها رو با کاما جدا کن، مثل: 0,2,5")
    try:
        qbit_client.set_file_selection(qb(), h, indices)
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    await update.message.reply_text(f"فقط فایل‌های {sorted(indices)} دانلود می‌شن.")


async def cmd_seedlimit(update, context):
    if len(context.args) < 3:
        return await update.message.reply_text(
            "استفاده: /seedlimit <hash8> <نسبت> <دقیقه>\n"
            "برای «بدون محدودیت» عدد -1، برای «مقدار سراسری» عدد -2 بده."
        )
    h = _full_hash(context.args[0])
    try:
        ratio = float(context.args[1])
        minutes = int(context.args[2])
    except ValueError:
        return await update.message.reply_text("نسبت باید عدد اعشاری و دقیقه عدد صحیح باشه.")
    try:
        qbit_client.set_share_limits(qb(), h, ratio_limit=ratio, seeding_time_limit=minutes)
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    await update.message.reply_text(f"محدودیت سید تنظیم شد: نسبت={ratio}، زمان={minutes} دقیقه")


async def cmd_rssadd(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /rssadd <لینک فید> [اسم دلخواه]")
    url = context.args[0]
    name = " ".join(context.args[1:]) if len(context.args) > 1 else url
    try:
        qbit_client.rss_add_feed(qb(), url, name)
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    await update.message.reply_text(f"فید اضافه شد: {name}")


async def cmd_rsslist(update, context):
    try:
        feeds = qbit_client.rss_list_feeds(qb())
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    if not feeds:
        return await update.message.reply_text("هیچ فیدی اضافه نشده.")
    lines = [f"• {name}: {data.get('url', '')}" for name, data in feeds.items()]
    await update.message.reply_text("\n".join(lines))


async def cmd_rssremove(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /rssremove <اسم فید>")
    name = " ".join(context.args)
    try:
        qbit_client.rss_remove_feed(qb(), name)
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    await update.message.reply_text(f"فید حذف شد: {name}")


async def cmd_rssrule(update, context):
    if len(context.args) < 2:
        return await update.message.reply_text(
            "استفاده: /rssrule <اسم rule> <کلمه‌ی کلیدی> [لینک فید مشخص]\n"
            "بدون لینک فید، رو همه‌ی فیدهای اضافه‌شده اعمال می‌شه."
        )
    rule_name, keyword = context.args[0], context.args[1]
    feeds = [context.args[2]] if len(context.args) > 2 else []
    try:
        qbit_client.rss_set_rule(qb(), rule_name, keyword, QB_CATEGORY, affected_feeds=feeds)
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    await update.message.reply_text(
        f"قانون «{rule_name}» ساخته شد: هر آیتمی که شامل «{keyword}» باشه خودکار دانلود می‌شه."
    )


async def cmd_rssrules(update, context):
    try:
        rules = qbit_client.rss_list_rules(qb())
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    if not rules:
        return await update.message.reply_text("هیچ قانونی تعریف نشده.")
    lines = [f"• {name}: شامل «{r.get('mustContain','')}»" for name, r in rules.items()]
    await update.message.reply_text("\n".join(lines))


async def cmd_rssruledel(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /rssruledel <اسم rule>")
    name = " ".join(context.args)
    try:
        qbit_client.rss_remove_rule(qb(), name)
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")
    await update.message.reply_text(f"قانون حذف شد: {name}")


# chat_id -> {"search_id": int, "results": [...]}
_last_search: dict[int, dict] = {}


async def cmd_search(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /search <عبارت جستجو>")
    pattern = " ".join(context.args)
    client = qb()
    try:
        search_id = qbit_client.search_start(client, pattern)
    except Exception as e:
        return await update.message.reply_text(f"خطا: {e}")

    await update.message.reply_text("🔎 در حال جستجو...")
    for _ in range(10):
        await asyncio.sleep(1)
        status = qbit_client.search_status(client, search_id)
        if status and status[0].status == "Stopped":
            break

    results = qbit_client.search_results(client, search_id, limit=8)
    qbit_client.search_stop(client, search_id)

    if not results.results:
        return await update.message.reply_text(
            "نتیجه‌ای پیدا نشد. اگه هیچ‌وقت نتیجه نمی‌گیری، احتمالاً هنوز پلاگین "
            "جستجو نصب نکردی — تو خود qBittorrent (تب Search) نصبش کن."
        )

    chat_id = update.effective_chat.id
    _last_search[chat_id] = {"results": results.results}
    lines = []
    for i, r in enumerate(results.results):
        size_mb = (r.file_size or 0) / (1024 * 1024)
        lines.append(f"{i}: {r.file_name[:60]} — {size_mb:.0f}MB — سیدر:{r.nb_seeders}")
    lines.append("\nبرای دانلود: /searchadd <شماره>")
    await update.message.reply_text("\n".join(lines))


async def cmd_searchadd(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /searchadd <شماره از لیست /search>")
    chat_id = update.effective_chat.id
    cache = _last_search.get(chat_id)
    if not cache:
        return await update.message.reply_text("اول /search بزن.")
    try:
        idx = int(context.args[0])
        result = cache["results"][idx]
    except (ValueError, IndexError):
        return await update.message.reply_text("شماره‌ی نامعتبر.")
    await add_torrent(update, magnet=result.file_url)


async def cmd_backup(update, context):
    """تنظیمات + فیدها/قوانین RSS رو تو یه فایل JSON می‌ریزه و می‌فرسته."""
    import json

    uid = update.effective_user.id
    r = get_runtime(uid)
    client = qb()
    try:
        feeds = qbit_client.rss_list_feeds(client)
        rules = qbit_client.rss_list_rules(client)
    except Exception:
        feeds, rules = {}, {}

    backup = {
        "settings": {
            "destinations": sorted(r["destinations"]),
            "upload_mode": r["upload_mode"],
            "is_premium": r["is_premium"],
            "split_size_mb": r["split_size_mb"],
            "force_zip": r["force_zip"],
            "zip_password": r.get("zip_password"),
            "split_parts_count": r.get("split_parts_count"),
        },
        "rss_feeds": {name: data.get("url", "") for name, data in feeds.items()},
        "rss_rules": {
            name: {"mustContain": r.get("mustContain", ""), "affectedFeeds": r.get("affectedFeeds", [])}
            for name, r in rules.items()
        },
    }

    backup_path = WORK_DIR / "leechdar_backup.json"
    backup_path.write_text(json.dumps(backup, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(backup_path, "rb") as f:
        await context.bot.send_document(update.effective_chat.id, document=f, filename="leechdar_backup.json")
    await update.message.reply_text("✅ بک‌آپ تنظیمات + فیدهای RSS گرفته شد.")


async def cmd_restore(update, context):
    """با ریپلای روی فایل leechdar_backup.json بزن تا تنظیمات برگرده."""
    import json

    if not update.message.reply_to_message or not update.message.reply_to_message.document:
        return await update.message.reply_text(
            "رو پیام حاوی فایل بک‌آپ (leechdar_backup.json) ریپلای کن و /restore بزن."
        )
    doc = update.message.reply_to_message.document
    file = await doc.get_file()
    local_path = Path("/tmp") / "restore_upload.json"
    await file.download_to_drive(custom_path=str(local_path))

    try:
        backup = json.loads(local_path.read_text(encoding="utf-8"))
    except Exception as e:
        return await update.message.reply_text(f"فایل بک‌آپ خراب یا نامعتبره: {e}")

    uid = update.effective_user.id
    s = backup.get("settings", {})
    if "destinations" in s and s["destinations"]:
        get_runtime(uid)["destinations"] = set(s["destinations"]) & VALID_DESTS
        _save_runtime(uid, "destinations")
    for key in ("upload_mode", "is_premium", "split_size_mb", "force_zip", "zip_password", "split_parts_count"):
        if key in s:
            get_runtime(uid)[key] = s[key]
            _save_runtime(uid, key)

    client = qb()
    restored_feeds = 0
    for name, url in backup.get("rss_feeds", {}).items():
        try:
            qbit_client.rss_add_feed(client, url, name)
            restored_feeds += 1
        except Exception as e:
            log.warning("restore feed failed (%s): %s", name, e)

    restored_rules = 0
    for name, r in backup.get("rss_rules", {}).items():
        try:
            qbit_client.rss_set_rule(client, name, r.get("mustContain", ""), QB_CATEGORY, r.get("affectedFeeds", []))
            restored_rules += 1
        except Exception as e:
            log.warning("restore rule failed (%s): %s", name, e)

    await update.message.reply_text(
        f"✅ تنظیمات برگردونده شد. فیدهای RSS بازیابی‌شده: {restored_feeds} | قوانین: {restored_rules}"
    )


async def cmd_update(update, context):
    """با git pull آخرین نسخه رو از ریپو می‌گیره و اگه چیزی عوض شده بود، ربات رو ری‌استارت می‌کنه."""
    repo_dir = Path(__file__).resolve().parent
    if not (repo_dir / ".git").exists():
        return await update.message.reply_text(
            "این پوشه یه ریپوی git نیست (احتمالاً از زیپ دانلود شده، نه git clone). "
            "برای آپدیت خودکار باید با `git clone` نصب شده باشه."
        )

    await update.message.reply_text("🔄 در حال بررسی آپدیت از ریپو...")
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "pull",
            "--ff-only",
            cwd=str(repo_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
    except Exception as e:
        return await update.message.reply_text(f"❌ اجرای git pull شکست خورد: {e}")

    output = (stdout.decode(errors="ignore") + stderr.decode(errors="ignore")).strip()

    if proc.returncode != 0:
        return await update.message.reply_text(f"❌ git pull خطا داد:\n```\n{output[-1200:]}\n```", parse_mode="Markdown")

    if "up to date" in output.lower() or "up-to-date" in output.lower():
        return await update.message.reply_text("✅ از قبل آخرین نسخه بودی، چیزی تغییر نکرد.")

    await update.message.reply_text(
        f"✅ آپدیت گرفته شد:\n```\n{output[-1200:]}\n```\n\n♻️ ربات داره ری‌استارت می‌شه — چند ثانیه صبر کن.",
        parse_mode="Markdown",
    )
    log.info("آپدیت از git pull گرفته شد؛ در حال ری‌استارت فرآیند...")
    os.execv(sys.executable, [sys.executable] + sys.argv)


SETUP_HOST, SETUP_PORT, SETUP_USERNAME, SETUP_PASSWORD = range(4)


async def setup_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    current = get_qb_settings()
    await update.message.reply_text(
        "⚙️ راه‌اندازی اتصال qBittorrent — بدون این، دانلود تورنت کار نمی‌کنه "
        "(بقیه‌ی امکانات مثل yt-dlp/aria2 نیاز به این مرحله ندارن).\n\n"
        "تو هر مرحله یا مقدار جدید رو بفرست، یا /skip بزن تا همون مقدار فعلی بمونه. "
        "هر وقت خواستی با /cancel کامل لغو کن.\n\n"
        f"هاست فعلی: {current['host']}\nهاست جدید qBittorrent رو بفرست (مثلاً localhost):"
    )
    return SETUP_HOST


async def setup_host(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text != "/skip":
        context.user_data["qb_host"] = text
    current = get_qb_settings()
    await update.message.reply_text(f"پورت فعلی: {current['port']}\nپورت جدید؟ (یا /skip)")
    return SETUP_PORT


async def setup_port(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text != "/skip":
        try:
            int(text)
        except ValueError:
            await update.message.reply_text("پورت باید فقط عدد باشه. دوباره بفرست یا /skip بزن.")
            return SETUP_PORT
        context.user_data["qb_port"] = text
    current = get_qb_settings()
    await update.message.reply_text(f"یوزرنیم فعلی: {current['username']}\nیوزرنیم جدید؟ (یا /skip)")
    return SETUP_USERNAME


async def setup_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text != "/skip":
        context.user_data["qb_username"] = text
    await update.message.reply_text("پسورد جدید؟ (یا /skip — پسورد قبلی دست‌نخورده می‌مونه)")
    return SETUP_PASSWORD


async def setup_password(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text != "/skip":
        context.user_data["qb_password"] = text

    for key, value in context.user_data.items():
        db.set_setting(key, value)  # تنظیمات سراسری (user_id=0 پیش‌فرض)

    await update.message.reply_text("🔌 در حال تست اتصال با مقادیر جدید...")
    try:
        await asyncio.to_thread(qb)
        await update.message.reply_text("✅ تنظیمات ذخیره شد و اتصال به qBittorrent موفق بود. آماده‌ای!")
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ تنظیمات ذخیره شد ولی الان نتونستم وصل بشم: {e}\n"
            "مطمئن شو qBittorrent روشنه و Web UI فعاله، بعد دوباره /setup بزن."
        )
    context.user_data.clear()
    return ConversationHandler.END


async def setup_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text("لغو شد؛ چیزی تغییر نکرد.")
    return ConversationHandler.END


async def cmd_adduser(update, context):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("فقط مالک ربات می‌تونه کاربر اضافه کنه.")
    if not context.args:
        return await update.message.reply_text("استفاده: /adduser <آیدی عددی تلگرام>")
    try:
        new_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("آیدی باید عدد باشه (از @userinfobot بگیرش).")
    db.add_allowed_user(new_id)
    await update.message.reply_text(f"✅ کاربر {new_id} اضافه شد.")


async def cmd_removeuser(update, context):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("فقط مالک ربات می‌تونه کاربر حذف کنه.")
    if not context.args:
        return await update.message.reply_text("استفاده: /removeuser <آیدی عددی تلگرام>")
    try:
        target_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("آیدی باید عدد باشه.")
    if target_id == update.effective_user.id:
        return await update.message.reply_text("نمی‌تونی خودت رو حذف کنی.")
    db.remove_allowed_user(target_id)
    await update.message.reply_text(f"🗑 کاربر {target_id} حذف شد.")


async def cmd_listusers(update, context):
    if not is_owner(update.effective_user.id):
        return await update.message.reply_text("فقط مالک ربات می‌تونه این لیست رو ببینه.")
    users = db.list_allowed_users()
    config_ids = config.ALLOWED_USER_IDS - {u["user_id"] for u in users}
    lines = [f"• {u['user_id']}{' (مالک)' if u['is_owner'] else ''}" for u in users]
    lines += [f"• {uid} (از config.py)" for uid in config_ids]
    await update.message.reply_text("\n".join(lines) if lines else "هیچ کاربری ثبت نشده.")


async def cmd_log(update, context):
    log_path = Path(config.LOG_FILE)
    if not log_path.exists():
        return await update.message.reply_text("هنوز لاگی ثبت نشده.")
    lines = log_path.read_text(encoding="utf-8", errors="ignore").splitlines()[-40:]
    text = "\n".join(lines) or "خالیه."
    if len(text) > 3500:
        text = text[-3500:]
    await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")


def _full_hash(short_hash: str):
    if short_hash.startswith("merged_"):
        return short_hash
    for h in pending:
        if h.startswith(short_hash.lower()):
            return h
    return short_hash


# ------------------------------------------------------------------
# افزودن تورنت جدید
# ------------------------------------------------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update.effective_user.id):
        return await update.message.reply_text("دسترسی نداری.")
    text = update.message.text.strip()
    if text.startswith("magnet:"):
        return await add_torrent(update, magnet=text)

    if downloader_mega.is_mega_url(text):
        return await add_mega_link(update, context, text)

    if not downloader_ytdlp.is_supported_url(text):
        return await update.message.reply_text("این یه لینک مگنت یا یه لینک http/https معتبر نیست.")

    if looks_like_direct_file(text):
        return await add_aria2_link(update, context, text)

    # پسوند فایل مشخص نیست؛ با یه HEAD request واقعی تشخیص بده صفحه‌ست یا فایل
    kind = await content_sniff.classify_url(text)
    if kind == "aria2":
        return await add_aria2_link(update, context, text)
    # "ytdlp" یا "unknown" (مثلاً HEAD رد شد) — بذار yt-dlp امتحان کنه،
    # چون پوشش گسترده‌تری داره و خودش هم خطای مناسب می‌ده
    await add_ytdl_link(update, context, text)


async def handle_torrent_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_allowed(update.effective_user.id):
        return await update.message.reply_text("دسترسی نداری.")
    doc = update.message.document
    if doc.file_name.endswith(".torrent"):
        file = await doc.get_file()
        local_path = Path("/tmp") / doc.file_name
        await file.download_to_drive(custom_path=str(local_path))
        return await add_torrent(update, torrent_file=str(local_path))

    # فایل غیر-تورنت: تبدیلش کن به تورنت و سیدش کن (آپلود به‌عنوان تورنت)
    await create_torrent_from_upload(update, context, doc)


async def create_torrent_from_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, doc) -> None:
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"📦 در حال دانلود و ساخت تورنت از «{doc.file_name}»...")

    dest_dir = Path(config.CREATED_TORRENTS_DIR) / str(chat_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    local_path = dest_dir / doc.file_name

    try:
        file = await doc.get_file()
        await file.download_to_drive(custom_path=str(local_path))
    except Exception as e:
        return await update.message.reply_text(
            f"❌ دانلود فایل از تلگرام شکست خورد: {e}\n"
            "(توجه: Bot API معمولی فقط فایل‌های تا ۲۰ مگابایت رو اجازه‌ی دانلود می‌ده؛ "
            "برای فایل‌های بزرگ‌تر باید از حالت pyrogram_user استفاده بشه.)"
        )

    torrent_path = dest_dir / (doc.file_name + ".torrent")
    try:
        _, magnet = await asyncio.to_thread(torrent_creator.create_torrent, local_path, torrent_path)
    except Exception as e:
        return await update.message.reply_text(f"❌ خطا تو ساخت تورنت: {e}")

    client = qb()
    try:
        with open(torrent_path, "rb") as f:
            client.torrents_add(
                torrent_files=f, save_path=str(dest_dir), is_skip_checking=True, category=QB_CATEGORY
            )
    except Exception as e:
        return await update.message.reply_text(f"❌ اضافه‌کردن به qBittorrent شکست خورد: {e}")

    await update.message.reply_text(
        f"✅ تورنت ساخته شد و شروع به سید کرد.\n\n"
        f"مگنت‌لینک (به هرکی بفرستی می‌تونه ازت دانلود کنه، تا وقتی ربات/qBittorrent روشنه):\n"
        f"`{magnet}`",
        parse_mode="Markdown",
    )
    with open(torrent_path, "rb") as f:
        await context.bot.send_document(chat_id, document=f, filename=torrent_path.name)


async def cmd_webapp(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not config.WEBUI_ENABLED:
        return await update.message.reply_text("داشبورد وب فعال نیست (WEBUI_ENABLED=false).")
    if not config.WEBUI_PUBLIC_URL:
        return await update.message.reply_text(
            "داشبورد فعاله ولی WEBUI_PUBLIC_URL تنظیم نشده — تلگرام فقط لینک HTTPS عمومی رو "
            "به‌عنوان WebApp قبول می‌کنه، نه localhost. یه دامنه/HTTPS (مثلاً پشت Caddy یا "
            "Cloudflare Tunnel) جلوش بذار و لینکش رو تو config.py بذار."
        )
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📊 باز کردن داشبورد", web_app=WebAppInfo(url=config.WEBUI_PUBLIC_URL))]]
    )
    await update.message.reply_text("داشبورد رو همین‌جا تو تلگرام باز کن:", reply_markup=keyboard)


async def cmd_forceadd(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /forceadd <magnet>")
    magnet = " ".join(context.args)
    await add_torrent(update, magnet=magnet, force=True)


async def cmd_ytdl(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /ytdl <لینک>")
    await add_ytdl_link(update, context, context.args[0])


async def cmd_forceytdl(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /forceytdl <لینک>")
    await add_ytdl_link(update, context, context.args[0], force=True)


async def cmd_mega(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /mega <لینک مگا>")
    await add_mega_link(update, context, context.args[0])


async def cmd_forcemega(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /forcemega <لینک مگا>")
    await add_mega_link(update, context, context.args[0], force=True)


async def cmd_jdadd(update, context):
    if not config.JDOWNLOADER_ENABLED:
        return await update.message.reply_text("JDOWNLOADER_ENABLED خاموشه.")
    if not context.args:
        return await update.message.reply_text("استفاده: /jdadd <لینک>")
    url = context.args[0]
    await update.message.reply_text("⏳ در حال وصل‌شدن به MyJDownloader و افزودن لینک...")
    try:
        await jdownloader_client.add_link(
            config.JDOWNLOADER_EMAIL, config.JDOWNLOADER_PASSWORD, config.JDOWNLOADER_DEVICE_NAME, url
        )
    except Exception as e:
        return await update.message.reply_text(
            f"❌ افزودن به JDownloader شکست خورد: {e}\n"
            "(JDownloader باید رو یه سیستم باز و به همون اکانت MyJDownloader وصل باشه.)"
        )
    await update.message.reply_text(
        "✅ لینک به صف JDownloader اضافه شد — خود JDownloader (نه این ربات) دانلودش می‌کنه."
    )


async def cmd_leech(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /leech <لینک مستقیم>")
    await add_aria2_link(update, context, context.args[0])


async def cmd_forceleech(update, context):
    if not context.args:
        return await update.message.reply_text("استفاده: /forceleech <لینک مستقیم>")
    await add_aria2_link(update, context, context.args[0], force=True)


async def add_aria2_link(
    update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, force: bool = False
) -> None:
    identifier = _url_identifier(url)

    if config.STOP_DUPLICATES and not force and db.is_completed(identifier):
        return await update.message.reply_text(
            f"⚠️ این لینک قبلاً دانلود و آپلود شده. اگه مطمئنی دوباره می‌خوای:\n/forceleech {url}"
        )

    free_gb = reliability.free_disk_gb(config.DISK_CHECK_PATH)
    if free_gb < config.MIN_FREE_DISK_GB:
        return await update.message.reply_text(
            f"⚠️ فضای آزاد دیسک ({free_gb:.1f}GB) کمتر از حداقل مجاز "
            f"({config.MIN_FREE_DISK_GB}GB) هست. دانلود جدید رد شد."
        )

    try:
        get_aria2_api()
    except Exception as e:
        return await update.message.reply_text(f"❌ سرویس aria2 در دسترس نیست: {e}")

    h = "ar" + identifier[:22]
    queued = active_downloading_count() >= config.MAX_CONCURRENT_DOWNLOADS or not is_within_schedule()
    item = {
        "chat_id": update.effective_chat.id,
        "torrent_name": url,
        "files": [],
        "status": "queued" if queued else "downloading",
        "magnet_hash": identifier,
        "source": "aria2",
        "source_url": url,
        "aria2_gid": None,
    }
    if not queued:
        item["aria2_gid"] = aria2_client.add_uri(get_aria2_api(), url, config.ARIA2_DOWNLOAD_DIR)

    pending[h] = item
    _persist(h, item)

    note = " (تو صف — به‌محض آزاد شدن جا شروع می‌شه)" if queued else ""
    await update.message.reply_text(f"➕ اضافه شد (لینک مستقیم): {url}\nهش: {h[:8]}{note}")


def _url_identifier(url: str) -> str:
    import hashlib

    return hashlib.sha256(url.encode()).hexdigest()


async def add_ytdl_link(
    update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, force: bool = False
) -> None:
    identifier = _url_identifier(url)

    if config.STOP_DUPLICATES and not force and db.is_completed(identifier):
        return await update.message.reply_text(
            "⚠️ این لینک قبلاً دانلود و آپلود شده. اگه مطمئنی دوباره می‌خوای:\n"
            f"/forceytdl {url}"
        )

    free_gb = reliability.free_disk_gb(config.DISK_CHECK_PATH)
    if free_gb < config.MIN_FREE_DISK_GB:
        return await update.message.reply_text(
            f"⚠️ فضای آزاد دیسک ({free_gb:.1f}GB) کمتر از حداقل مجاز "
            f"({config.MIN_FREE_DISK_GB}GB) هست. دانلود جدید رد شد."
        )

    h = "yt" + identifier[:22]  # پیشوند yt تا با هش تورنت قاطی نشه
    queued = active_downloading_count() >= config.MAX_CONCURRENT_DOWNLOADS or not is_within_schedule()
    item = {
        "chat_id": update.effective_chat.id,
        "torrent_name": url,  # بعد از دانلود با عنوان واقعی جایگزین می‌شه
        "files": [],
        "status": "queued" if queued else "downloading",
        "magnet_hash": identifier,
        "source": "ytdlp",
        "source_url": url,
    }
    pending[h] = item
    _persist(h, item)

    if queued:
        await update.message.reply_text(f"➕ تو صف رفت (yt-dlp): {url}\nهش: {h[:8]}")
    else:
        await update.message.reply_text(f"➕ شروع دانلود (yt-dlp): {url}\nهش: {h[:8]}")
        asyncio.create_task(_run_ytdlp(context.application, h, item))


async def _run_ytdlp(app: Application, h: str, item: dict) -> None:
    chat_id = item["chat_id"]
    try:
        files, title = await downloader_ytdlp.download(
            item["source_url"],
            WORK_DIR / "ytdl" / h[:10],
            h,
            config.YTDLP_FORMAT,
            subtitles=config.YTDLP_SUBTITLES,
            sub_langs=config.YTDLP_SUB_LANGS,
        )
    except Exception as e:
        log.exception("دانلود yt-dlp شکست خورد")
        await app.bot.send_message(chat_id, f"❌ دانلود شکست خورد: {e}")
        pending.pop(h, None)
        db.delete_task(h)
        promote_queued(qb(), app)
        return

    if not files:
        await app.bot.send_message(chat_id, "دانلود تموم شد ولی فایلی پیدا نشد.")
        pending.pop(h, None)
        db.delete_task(h)
        promote_queued(qb(), app)
        return

    item["files"] = files
    item["torrent_name"] = title
    item["status"] = "ready"
    _persist(h, item)

    if config.AUTO_UPLOAD_ON_COMPLETE:
        await app.bot.send_message(chat_id, f"✅ دانلود تموم شد: {title}\nدر حال آماده‌سازی آپلود...")
        await do_upload(app, h, item)
    else:
        await app.bot.send_message(
            chat_id, f"✅ دانلود تموم شد: {title}\nهش: {h[:8]}\nبرای آپلود: /upload {h[:8]}"
        )


async def add_mega_link(
    update: Update, context: ContextTypes.DEFAULT_TYPE, url: str, force: bool = False
) -> None:
    identifier = _url_identifier(url)

    if config.STOP_DUPLICATES and not force and db.is_completed(identifier):
        return await update.message.reply_text(
            f"⚠️ این لینک مگا قبلاً دانلود و آپلود شده. اگه مطمئنی دوباره می‌خوای:\n/forcemega {url}"
        )

    free_gb = reliability.free_disk_gb(config.DISK_CHECK_PATH)
    if free_gb < config.MIN_FREE_DISK_GB:
        return await update.message.reply_text(
            f"⚠️ فضای آزاد دیسک ({free_gb:.1f}GB) کمتر از حداقل مجاز ({config.MIN_FREE_DISK_GB}GB) هست."
        )

    h = "mg" + identifier[:22]
    queued = active_downloading_count() >= config.MAX_CONCURRENT_DOWNLOADS or not is_within_schedule()
    item = {
        "chat_id": update.effective_chat.id,
        "torrent_name": url,
        "files": [],
        "status": "queued" if queued else "downloading",
        "magnet_hash": identifier,
        "source": "mega",
        "source_url": url,
        "aria2_gid": None,
    }
    pending[h] = item
    _persist(h, item)

    if queued:
        await update.message.reply_text(f"➕ تو صف رفت (Mega): {url}\nهش: {h[:8]}")
    else:
        await update.message.reply_text(f"➕ شروع دانلود (Mega): {url}\nهش: {h[:8]}")
        asyncio.create_task(_run_mega(context.application, h, item))


async def _run_mega(app: Application, h: str, item: dict) -> None:
    chat_id = item["chat_id"]
    try:
        files = await downloader_mega.download(
            item["source_url"], WORK_DIR / "mega" / h[:10], config.MEGA_EMAIL or None, config.MEGA_PASSWORD or None
        )
    except Exception as e:
        log.exception("دانلود Mega شکست خورد")
        await app.bot.send_message(
            chat_id,
            f"❌ دانلود از مگا شکست خورد: {e}\n"
            "(کتابخونه‌ی mega.py رسمی نیست و گاهی با تغییرات مگا قطع می‌شه؛ "
            "اگه مداوم خطا می‌ده، README رو برای به‌روزرسانیش ببین.)",
        )
        pending.pop(h, None)
        db.delete_task(h)
        promote_queued(qb(), app)
        return

    item["files"] = files
    if files:
        item["torrent_name"] = files[0].name
    item["status"] = "ready"
    _persist(h, item)

    if config.AUTO_UPLOAD_ON_COMPLETE:
        await app.bot.send_message(chat_id, f"✅ دانلود از مگا تموم شد: {item['torrent_name']}\nدر حال آپلود...")
        await do_upload(app, h, item)
    else:
        await app.bot.send_message(chat_id, f"✅ دانلود تموم شد. برای آپلود: /upload {h[:8]}")


async def add_torrent(
    update: Update,
    magnet: str | None = None,
    torrent_file: str | None = None,
    force: bool = False,
) -> None:
    btih = extract_btih(magnet) if magnet else None

    if config.STOP_DUPLICATES and not force and btih and db.is_completed(btih):
        return await update.message.reply_text(
            "⚠️ این آیتم قبلاً دانلود و آپلود شده. اگه مطمئنی دوباره می‌خوای:\n"
            f"/forceadd {magnet}"
        )

    free_gb = reliability.free_disk_gb(config.DISK_CHECK_PATH)
    if free_gb < config.MIN_FREE_DISK_GB:
        return await update.message.reply_text(
            f"⚠️ فضای آزاد دیسک ({free_gb:.1f}GB) کمتر از حداقل مجاز "
            f"({config.MIN_FREE_DISK_GB}GB) هست. دانلود جدید رد شد."
        )

    client = qb()
    before = {t.hash for t in client.torrents_info()}
    if magnet:
        client.torrents_add(urls=magnet, category=QB_CATEGORY)
    else:
        with open(torrent_file, "rb") as f:
            client.torrents_add(torrent_files=f, category=QB_CATEGORY)

    await asyncio.sleep(1.5)
    after = client.torrents_info(category=QB_CATEGORY)
    new_ones = [t for t in after if t.hash not in before]
    if not new_ones:
        return await update.message.reply_text("اضافه شد ولی هش پیدا نشد؛ با /status چک کن.")

    for t in new_ones:
        queued = active_downloading_count() >= config.MAX_CONCURRENT_DOWNLOADS or not is_within_schedule()
        status = "queued" if queued else "downloading"
        if queued:
            qbit_client.pause_torrent(client, t.hash)
        if config.SEED_RATIO_LIMIT != -2 or config.SEED_TIME_LIMIT_MIN != -2:
            try:
                qbit_client.set_share_limits(
                    client, t.hash, ratio_limit=config.SEED_RATIO_LIMIT, seeding_time_limit=config.SEED_TIME_LIMIT_MIN
                )
            except Exception as e:
                log.warning("تنظیم seed limit شکست خورد: %s", e)
        item = {
            "chat_id": update.effective_chat.id,
            "torrent_name": t.name,
            "files": [],
            "status": status,
            "magnet_hash": btih,
            "source": "torrent",
            "source_url": None,
        }
        pending[t.hash] = item
        _persist(t.hash, item)
        note = " (تو صف — به‌محض آزاد شدن جا شروع می‌شه)" if queued else ""
        await update.message.reply_text(f"➕ اضافه شد: {t.name}\nهش کوتاه: {t.hash[:8]}{note}")


# ------------------------------------------------------------------
# نظارت بر اتمام دانلود
# ------------------------------------------------------------------

FINISHED_STATES = {"uploading", "stalledUP", "queuedUP", "pausedUP", "forcedUP"}


def _adopt_rss_torrents(client) -> None:
    """
    تورنت‌هایی که rule RSS خودکار (نه از طریق تلگرام) به دسته‌ی ما
    اضافه کرده رو پیدا و به pending/db اضافه می‌کنه تا وارد چرخه‌ی
    عادی آپلود بشن.
    """
    if not config.RSS_NOTIFY_CHAT_ID:
        return
    try:
        torrents = client.torrents_info(category=QB_CATEGORY)
    except Exception:
        return
    for t in torrents:
        if t.hash in pending:
            continue
        item = {
            "chat_id": config.RSS_NOTIFY_CHAT_ID,
            "torrent_name": t.name,
            "files": [],
            "status": "downloading",
            "magnet_hash": None,
            "source": "rss",
            "source_url": None,
            "aria2_gid": None,
        }
        pending[t.hash] = item
        _persist(t.hash, item)
        log.info("تورنت RSS خودکار شناسایی و adopt شد: %s", t.name)


async def watcher_loop(app: Application) -> None:
    client = qb()
    notified: set[str] = set()
    while True:
        await asyncio.sleep(config.POLL_INTERVAL_SECONDS)
        try:
            _adopt_rss_torrents(client)
            promote_queued(client, app)  # اگه الان وارد بازه‌ی زمان‌بندی شدیم، صف رو تکون بده

            for h in [k for k in list(pending.keys()) if not k.startswith("merged_")]:
                item = pending.get(h)
                if not item or item["status"] != "downloading" or item["files"]:
                    continue

                if item.get("source") == "aria2":
                    await _check_aria2_task(app, h, item, notified)
                    continue

                if item.get("source") == "ytdlp":
                    continue  # این‌ها تو تسک جدای asyncio خودشون پیش می‌رن، نه با پولینگ

                try:
                    info = client.torrents_info(torrent_hashes=h)
                except Exception as e:
                    log.warning("qBittorrent query failed: %s", e)
                    continue
                if not info:
                    continue
                t = info[0]
                if t.state in FINISHED_STATES and h not in notified:
                    notified.add(h)
                    files = [
                        Path(t.save_path) / f.name
                        for f in t.files
                        if f.priority > 0 and (Path(t.save_path) / f.name).exists()
                    ]
                    item["files"] = files
                    item["status"] = "ready"
                    _persist(h, item)
                    chat_id = item["chat_id"]
                    if config.AUTO_UPLOAD_ON_COMPLETE:
                        await app.bot.send_message(chat_id, f"✅ دانلود تموم شد: {t.name}\nدر حال آماده‌سازی آپلود...")
                        await do_upload(app, h, item)
                    else:
                        await app.bot.send_message(
                            chat_id,
                            f"✅ دانلود تموم شد: {t.name}\nهش: {h[:8]}\n"
                            f"می‌تونی /rename /tozip /extract بزنی، بعد /upload {h[:8]}",
                        )
        except Exception:
            log.exception("خطای غیرمنتظره تو watcher_loop")


async def _check_aria2_task(app: Application, h: str, item: dict, notified: set[str]) -> None:
    chat_id = item["chat_id"]
    try:
        d = aria2_client.get_download(get_aria2_api(), item["aria2_gid"])
    except Exception as e:
        log.warning("aria2 query failed: %s", e)
        return

    if d.has_failed:
        await app.bot.send_message(chat_id, f"❌ دانلود شکست خورد: {d.error_message}")
        pending.pop(h, None)
        db.delete_task(h)
        promote_queued(qb(), app)
        return

    if d.is_complete and h not in notified:
        notified.add(h)
        files = [f.path for f in d.files if f.path.exists()]
        item["files"] = files
        if files:
            item["torrent_name"] = files[0].name
        item["status"] = "ready"
        _persist(h, item)
        name = item["torrent_name"]
        if config.AUTO_UPLOAD_ON_COMPLETE:
            await app.bot.send_message(chat_id, f"✅ دانلود تموم شد: {name}\nدر حال آماده‌سازی آپلود...")
            await do_upload(app, h, item)
        else:
            await app.bot.send_message(
                chat_id, f"✅ دانلود تموم شد: {name}\nهش: {h[:8]}\nبرای آپلود: /upload {h[:8]}"
            )


# ------------------------------------------------------------------
# پردازش نهایی (زیپ/اسپیلت) و آپلود
# ------------------------------------------------------------------

async def do_upload(app: Application, key: str, item: dict) -> None:
    chat_id = item["chat_id"]
    r = get_runtime(chat_id)  # چت خصوصی == کاربر، پس تنظیمات همون کاربره
    files: list[Path] = item["files"]
    if not files:
        await app.bot.send_message(chat_id, "فایلی برای آپلود پیدا نشد.")
        return

    should_zip = r["force_zip"] or r.get("zip_password") or (config.AUTO_ZIP_MULTI_FILE_TORRENTS and len(files) > 1)
    if should_zip and not (len(files) == 1 and file_tools.is_archive(files[0])):
        zip_name = (files[0].stem if len(files) == 1 else item["torrent_name"]) + ".zip"
        zip_path = WORK_DIR / zip_name
        pwd = r.get("zip_password")
        note = " (رمزدار)" if pwd else ""
        await app.bot.send_message(chat_id, f"📦 در حال زیپ‌کردن {len(files)} فایل{note}...")
        file_tools.create_zip(files, zip_path, password=pwd)
        files = [zip_path]

    for f in files:
        if r.get("split_parts_count"):
            parts = file_tools.split_file_into_n_parts(f, r["split_parts_count"], out_dir=WORK_DIR)
        else:
            parts = file_tools.split_file(f, current_split_size_bytes(chat_id), out_dir=WORK_DIR)
        if len(parts) > 1:
            await app.bot.send_message(chat_id, f"✂️ {f.name} به {len(parts)} پارت تقسیم شد.")

        dests = r["destinations"]
        if "telegram" in dests and r["upload_mode"] == "bot_api" and len(parts) > 1:
            await upload_parts_as_telegram_album(app, chat_id, parts)
            other_dests = dests - {"telegram"}
            if other_dests:
                for part in parts:
                    await upload_one_file(app, chat_id, part, dests=other_dests)
        else:
            for part in parts:
                await upload_one_file(app, chat_id, part)

    await app.bot.send_message(chat_id, f"🎉 آپلود کامل شد: {item['torrent_name']}")

    if not key.startswith("merged_"):
        if item.get("magnet_hash"):
            db.mark_completed(item["magnet_hash"], item["torrent_name"])
        else:
            db.mark_completed(key, item["torrent_name"])  # fallback: با خود هش تورنت
        if config.DELETE_AFTER_UPLOAD:
            try:
                qbit_client.cancel_torrent(qb(), key, delete_files=True)
            except Exception as e:
                log.warning("cleanup failed: %s", e)

    pending.pop(key, None)
    db.delete_task(key)
    if not key.startswith("merged_"):
        promote_queued(qb(), app)


async def upload_parts_as_telegram_album(app: Application, chat_id: int, parts: list[Path]) -> None:
    """
    چندتا پارت رو به‌جای فرستادن تک‌تک، به‌صورت آلبوم (media group)
    می‌فرسته — حداکثر ۱۰ فایل تو هر آلبوم طبق محدودیت خود تلگرام.
    """
    cap = current_cap_mb(chat_id)
    oversized = [p for p in parts if p.stat().st_size / (1024 * 1024) > cap]
    if oversized:
        await app.bot.send_message(
            chat_id, f"⚠️ {len(oversized)} پارت از سقف {cap}MB بزرگ‌تره؛ به‌جای آلبوم تک‌تک می‌فرستم."
        )
        for p in parts:
            await upload_one_file(app, chat_id, p, dests={"telegram"})
        return

    await app.bot.send_message(chat_id, f"⬆️ آپلود {len(parts)} پارت به‌صورت آلبوم به تلگرام...")
    for i in range(0, len(parts), 10):
        chunk = parts[i : i + 10]
        handles = [open(p, "rb") for p in chunk]
        try:
            media = [InputMediaDocument(media=fh, filename=p.name) for fh, p in zip(handles, chunk)]
            await reliability.retry_async(app.bot.send_media_group, chat_id, media)
        except Exception as e:
            log.exception("ارسال آلبومی شکست خورد")
            await app.bot.send_message(chat_id, f"❌ ارسال آلبومی شکست خورد: {e}؛ تک‌تک می‌فرستم.")
            for p in chunk:
                await upload_one_file(app, chat_id, p, dests={"telegram"})
        finally:
            for fh in handles:
                fh.close()


async def upload_one_file(app: Application, chat_id: int, file_path: Path, dests: set[str] | None = None) -> None:
    r = get_runtime(chat_id)
    dests = dests if dests is not None else r["destinations"]
    size_mb = file_path.stat().st_size / (1024 * 1024)

    if "telegram" in dests:
        cap = current_cap_mb(chat_id)
        if size_mb > cap:
            await app.bot.send_message(
                chat_id,
                f"⚠️ {file_path.name} ({size_mb:.0f}MB) از سقف فعلی ({cap}MB) بزرگ‌تره؛ آپلود تلگرام رد شد.",
            )
        else:
            await app.bot.send_message(chat_id, f"⬆️ آپلود به تلگرام: {file_path.name}")
            try:
                if r["upload_mode"] == "bot_api":
                    await reliability.retry_async(upload_via_bot_api, app.bot, chat_id, file_path)
                else:
                    await reliability.retry_async(
                        upload_via_pyrogram_user,
                        config.PYROGRAM_API_ID,
                        config.PYROGRAM_API_HASH,
                        config.PYROGRAM_SESSION_STRING,
                        chat_id,
                        file_path,
                    )
            except Exception as e:
                log.exception("آپلود تلگرام شکست خورد")
                await app.bot.send_message(chat_id, f"❌ آپلود {file_path.name} به تلگرام شکست خورد: {e}")
                await notify_fallback(f"آپلود {file_path.name} به تلگرام شکست خورد: {e}")

    if "gdrive" in dests:
        if not config.GDRIVE_ENABLED or upload_to_gdrive is None:
            await app.bot.send_message(chat_id, "گوگل‌درایو فعال نیست.")
        else:
            await app.bot.send_message(chat_id, f"⬆️ آپلود به گوگل‌درایو: {file_path.name}")
            try:
                link = await reliability.retry_async(
                    asyncio.to_thread,
                    upload_to_gdrive,
                    config.GDRIVE_CREDENTIALS_FILE,
                    config.GDRIVE_TOKEN_FILE,
                    file_path,
                    config.GDRIVE_FOLDER_ID,
                )
                await app.bot.send_message(chat_id, f"🔗 لینک درایو: {link}")
            except Exception as e:
                log.exception("آپلود گوگل‌درایو شکست خورد")
                await app.bot.send_message(chat_id, f"❌ آپلود {file_path.name} به گوگل‌درایو شکست خورد: {e}")
                await notify_fallback(f"آپلود {file_path.name} به گوگل‌درایو شکست خورد: {e}")

    if "rclone" in dests:
        if not (config.RCLONE_ENABLED and config.RCLONE_REMOTE):
            await app.bot.send_message(chat_id, "rclone فعال نیست.")
        else:
            await app.bot.send_message(chat_id, f"⬆️ آپلود با rclone: {file_path.name}")
            try:
                await reliability.retry_async(
                    uploader_rclone.upload_via_rclone,
                    config.RCLONE_REMOTE,
                    file_path,
                    config.RCLONE_BINARY,
                )
                await app.bot.send_message(chat_id, f"✅ rclone تموم شد: {file_path.name}")
            except Exception as e:
                log.exception("آپلود rclone شکست خورد")
                await app.bot.send_message(chat_id, f"❌ آپلود {file_path.name} با rclone شکست خورد: {e}")
                await notify_fallback(f"آپلود {file_path.name} با rclone شکست خورد: {e}")

    if "gofile" in dests:
        if not config.GOFILE_ENABLED:
            await app.bot.send_message(chat_id, "GoFile فعال نیست.")
        else:
            await app.bot.send_message(chat_id, f"⬆️ آپلود به GoFile: {file_path.name}")
            try:
                link = await reliability.retry_async(
                    uploader_gofile.upload_to_gofile, file_path, config.GOFILE_API_TOKEN or None
                )
                await app.bot.send_message(chat_id, f"🔗 لینک GoFile: {link}")
            except Exception as e:
                log.exception("آپلود GoFile شکست خورد")
                await app.bot.send_message(chat_id, f"❌ آپلود {file_path.name} به GoFile شکست خورد: {e}")
                await notify_fallback(f"آپلود {file_path.name} به GoFile شکست خورد: {e}")

    if "pixeldrain" in dests:
        if not config.PIXELDRAIN_ENABLED:
            await app.bot.send_message(chat_id, "PixelDrain فعال نیست.")
        else:
            await app.bot.send_message(chat_id, f"⬆️ آپلود به PixelDrain: {file_path.name}")
            try:
                link = await reliability.retry_async(
                    uploader_pixeldrain.upload_to_pixeldrain, file_path, config.PIXELDRAIN_API_KEY or None
                )
                await app.bot.send_message(chat_id, f"🔗 لینک PixelDrain: {link}")
            except Exception as e:
                log.exception("آپلود PixelDrain شکست خورد")
                await app.bot.send_message(chat_id, f"❌ آپلود {file_path.name} به PixelDrain شکست خورد: {e}")
                await notify_fallback(f"آپلود {file_path.name} به PixelDrain شکست خورد: {e}")


_running_app: Application | None = None


async def post_init(app: Application) -> None:
    global _running_app
    _running_app = app
    load_pending_from_db()
    app.create_task(watcher_loop(app))
    if config.WEBUI_ENABLED:
        import webui

        webui.run_server(config.WEBUI_HOST, config.WEBUI_PORT)
        log.info("رابط وب روی http://%s:%s بالا اومد", config.WEBUI_HOST, config.WEBUI_PORT)


async def notify_fallback(message: str, title: str = "LeechDar") -> None:
    """اگه NTFY فعال باشه، این رو به‌عنوان یه کانال اعلان یدکی هم می‌فرسته (مثلاً وقتی خود تلگرام مشکل داره)."""
    if config.NTFY_ENABLED and config.NTFY_URL:
        await notifier.send_ntfy(config.NTFY_URL, message, title=title)


def health_check() -> None:
    """
    قبل از بالا اومدن ربات، اتصال qBittorrent رو چک می‌کنه. اگه وصل
    نشد، فقط هشدار می‌ده و ربات رو می‌ذاره بالا بیاد (sys.exit نمی‌کنه) —
    چون ممکنه کاربر هنوز /setup رو نزده باشه؛ باید بتونه با تلگرام
    وصل بشه و از همونجا تنظیمش کنه.
    """
    try:
        qb()
        log.info("health-check: qBittorrent در دسترسه.")
    except Exception as e:
        log.warning(
            "اتصال اولیه به qBittorrent برقرار نشد (%s). ربات بالا میاد ولی دانلود تورنت کار "
            "نمی‌کنه تا با دستور /setup (تو خود تلگرام) تنظیمش کنی.",
            e,
        )

    try:
        get_aria2_api()
        log.info("health-check: aria2 هم در دسترسه (لینک مستقیم فعاله).")
    except Exception as e:
        log.warning("aria2 در دسترس نیست (%s) — لینک مستقیم غیرفعال می‌مونه، بقیه‌ی امکانات عادی کار می‌کنن.", e)


async def _global_auth_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    قبل از هر دستور یا پیامی اجرا می‌شه (group=-1): اگه کاربر تو
    ALLOWED_USER_IDS نباشه، همینجا جلوش گرفته می‌شه — بدون این، هر
    دستوری که خودش جداگانه is_allowed رو چک نکرده بود (اکثرشون) برای
    هرکسی که آیدی ربات رو پیدا کنه باز بود.
    """
    user = update.effective_user
    if user and not is_allowed(user.id):
        if try_auto_claim(user.id):
            return  # همین الان مالک شد، اجازه بده ادامه بده
        if update.message:
            await update.message.reply_text("دسترسی نداری.")
        raise ApplicationHandlerStop


def main() -> None:
    health_check()
    app = Application.builder().token(config.TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    app.add_handler(TypeHandler(Update, _global_auth_check), group=-1)

    setup_conv = ConversationHandler(
        entry_points=[CommandHandler("setup", setup_start)],
        states={
            SETUP_HOST: [MessageHandler(filters.TEXT, setup_host)],
            SETUP_PORT: [MessageHandler(filters.TEXT, setup_port)],
            SETUP_USERNAME: [MessageHandler(filters.TEXT, setup_username)],
            SETUP_PASSWORD: [MessageHandler(filters.TEXT, setup_password)],
        },
        fallbacks=[CommandHandler("cancel", setup_cancel)],
    )
    app.add_handler(setup_conv)
    app.add_handler(CommandHandler("adduser", cmd_adduser))
    app.add_handler(CommandHandler("removeuser", cmd_removeuser))
    app.add_handler(CommandHandler("listusers", cmd_listusers))
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("pause", cmd_pause))
    app.add_handler(CommandHandler("resume", cmd_resume))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("cancelconfirm", cmd_cancelconfirm))
    app.add_handler(CommandHandler("destination", cmd_destination))
    app.add_handler(CommandHandler("uploadmode", cmd_uploadmode))
    app.add_handler(CommandHandler("premium", cmd_premium))
    app.add_handler(CommandHandler("splitsize", cmd_splitsize))
    app.add_handler(CommandHandler("tozip", cmd_tozip))
    app.add_handler(CommandHandler("zippassword", cmd_zippassword))
    app.add_handler(CommandHandler("splitparts", cmd_splitparts))
    app.add_handler(CommandHandler("rename", cmd_rename))
    app.add_handler(CommandHandler("extract", cmd_extract))
    app.add_handler(CommandHandler("convert", cmd_convert))
    app.add_handler(CommandHandler("trim", cmd_trim))
    app.add_handler(CommandHandler("watermark", cmd_watermark))
    app.add_handler(CommandHandler("thumbnail", cmd_thumbnail))
    app.add_handler(CommandHandler("extractaudio", cmd_extractaudio))
    app.add_handler(CommandHandler("mergezip", cmd_mergezip))
    app.add_handler(CommandHandler("upload", cmd_upload))
    app.add_handler(CommandHandler("files", cmd_files))
    app.add_handler(CommandHandler("selectfiles", cmd_selectfiles))
    app.add_handler(CommandHandler("seedlimit", cmd_seedlimit))
    app.add_handler(CommandHandler("rssadd", cmd_rssadd))
    app.add_handler(CommandHandler("rsslist", cmd_rsslist))
    app.add_handler(CommandHandler("rssremove", cmd_rssremove))
    app.add_handler(CommandHandler("rssrule", cmd_rssrule))
    app.add_handler(CommandHandler("rssrules", cmd_rssrules))
    app.add_handler(CommandHandler("rssruledel", cmd_rssruledel))
    app.add_handler(CommandHandler("search", cmd_search))
    app.add_handler(CommandHandler("searchadd", cmd_searchadd))
    app.add_handler(CommandHandler("webapp", cmd_webapp))
    app.add_handler(CommandHandler("forceadd", cmd_forceadd))
    app.add_handler(CommandHandler("ytdl", cmd_ytdl))
    app.add_handler(CommandHandler("forceytdl", cmd_forceytdl))
    app.add_handler(CommandHandler("mega", cmd_mega))
    app.add_handler(CommandHandler("forcemega", cmd_forcemega))
    app.add_handler(CommandHandler("jdadd", cmd_jdadd))
    app.add_handler(CommandHandler("leech", cmd_leech))
    app.add_handler(CommandHandler("forceleech", cmd_forceleech))
    app.add_handler(CommandHandler("backup", cmd_backup))
    app.add_handler(CommandHandler("restore", cmd_restore))
    app.add_handler(CommandHandler("update", cmd_update))
    app.add_handler(CommandHandler("log", cmd_log))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_torrent_file))
    log.info("LeechDar starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
