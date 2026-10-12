# این فایل رو کپی کن به config.py.
#
# دو روش استفاده (هر دو هم‌زمان کار می‌کنن، env اولویت داره):
#   ۱) اجرای محلی (ویندوز/مک): مقادیر رو مستقیم همین‌جا ویرایش کن.
#   ۲) اجرای روی سرور/Docker: به‌جای ویرایش این فایل، همین اسم‌ها رو
#      به‌عنوان environment variable ست کن (مثلاً تو .env یا
#      docker-compose.yml) — بدون اینکه config.py دست بخوره.
#
# config.py رو هیچوقت جایی پابلیک (مثل گیت‌هاب) آپلود نکن — تو
# .gitignore هست.

import os


def _env(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_int(key: str, default: int) -> int:
    val = os.getenv(key)
    return int(val) if val not in (None, "") else default


def _env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def _env_id_set(key: str, default: set[int]) -> set[int]:
    val = os.getenv(key)
    if not val:
        return default
    result = {int(x) for x in val.split(",") if x.strip()}
    return result or default


# --- تلگرام: ربات ---
TELEGRAM_BOT_TOKEN = _env("TELEGRAM_BOT_TOKEN", "123456789:AAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx")
ALLOWED_USER_IDS = _env_id_set("ALLOWED_USER_IDS", {111111111})   # آیدی عددی از @userinfobot

# --- qBittorrent Web UI ---
# روی سرور/داکر معمولاً QB_HOST میشه اسم سرویس qBittorrent تو
# docker-compose (مثلاً "qbittorrent")، نه "localhost"
QB_HOST = _env("QB_HOST", "localhost")
QB_PORT = _env_int("QB_PORT", 8080)
QB_USERNAME = _env("QB_USERNAME", "admin")
QB_PASSWORD = _env("QB_PASSWORD", "adminadmin")
POLL_INTERVAL_SECONDS = _env_int("POLL_INTERVAL_SECONDS", 15)

# اگه False باشه، بعد از اتمام دانلود صبر می‌کنه تا دستور /upload بدی
AUTO_UPLOAD_ON_COMPLETE = _env_bool("AUTO_UPLOAD_ON_COMPLETE", True)

# --- آپلود به تلگرام ---
# "bot_api"      : از طریق توکن ربات، سقف ۵۰ مگابایت
# "pyrogram_user": از طریق حساب کاربری خودت، سقف ۲ گیگ / ۴ گیگ با پریمیوم
TELEGRAM_UPLOAD_MODE = _env("TELEGRAM_UPLOAD_MODE", "bot_api")
TELEGRAM_IS_PREMIUM = _env_bool("TELEGRAM_IS_PREMIUM", False)

# فقط برای TELEGRAM_UPLOAD_MODE = "pyrogram_user" لازمه (با generate_session.py بساز):
PYROGRAM_API_ID = _env_int("PYROGRAM_API_ID", 0)
PYROGRAM_API_HASH = _env("PYROGRAM_API_HASH", "")
PYROGRAM_SESSION_STRING = _env("PYROGRAM_SESSION_STRING", "")

# --- تقسیم فایل (اسپیلت) ---
_split = os.getenv("SPLIT_SIZE_MB")
SPLIT_SIZE_MB = int(_split) if _split else None  # None => خودکار بر اساس سقف آپلود

AUTO_ZIP_MULTI_FILE_TORRENTS = _env_bool("AUTO_ZIP_MULTI_FILE_TORRENTS", True)
DELETE_AFTER_UPLOAD = _env_bool("DELETE_AFTER_UPLOAD", False)

# --- گوگل درایو (اختیاری) ---
GDRIVE_ENABLED = _env_bool("GDRIVE_ENABLED", False)
GDRIVE_CREDENTIALS_FILE = _env("GDRIVE_CREDENTIALS_FILE", "credentials.json")
GDRIVE_TOKEN_FILE = _env("GDRIVE_TOKEN_FILE", "gdrive_token.json")
GDRIVE_FOLDER_ID = _env("GDRIVE_FOLDER_ID", "")

# --- rclone (اختیاری) — آپلود به هر کلاودی که rclone پشتیبانی می‌کنه ---
# rclone باید از قبل نصب و remote موردنظر با `rclone config` تنظیم شده باشه
RCLONE_ENABLED = _env_bool("RCLONE_ENABLED", False)
RCLONE_REMOTE = _env("RCLONE_REMOTE", "")  # مثلاً "gdrive:LeechDar/" یا "mys3:bucket/path/"
RCLONE_BINARY = _env("RCLONE_BINARY", "rclone")

# --- yt-dlp (همیشه فعاله، وابستگی سنگینی نداره) ---
YTDLP_FORMAT = _env("YTDLP_FORMAT", "bestvideo+bestaudio/best")

# --- aria2 (لینک مستقیم HTTP/HTTPS/FTP) ---
# نیازی به سرویس جدا نیست؛ خود ربات یه دیمون aria2c رو بغل خودش
# بالا میاره (فقط باینری aria2c باید نصب باشه)
ARIA2_HOST = _env("ARIA2_HOST", "http://localhost")
ARIA2_PORT = _env_int("ARIA2_PORT", 6800)
ARIA2_SECRET = _env("ARIA2_SECRET", "leechdar-secret-change-me")
ARIA2_DOWNLOAD_DIR = _env("ARIA2_DOWNLOAD_DIR", "work/aria2_downloads")
ARIA2_BINARY = _env("ARIA2_BINARY", "aria2c")

# --- ساخت تورنت از فایل آپلودی (seed) ---
CREATED_TORRENTS_DIR = _env("CREATED_TORRENTS_DIR", "work/created_torrents")

# --- کنترل سید ---
# -2 = مقدار سراسری qBittorrent، -1 = بدون محدودیت، عدد مثبت = مقدار مشخص
SEED_RATIO_LIMIT = float(_env("SEED_RATIO_LIMIT", "-2"))
SEED_TIME_LIMIT_MIN = _env_int("SEED_TIME_LIMIT_MIN", -2)

# --- RSS ---
# تورنت‌هایی که با rule خودکار qBittorrent دانلود می‌شن (نه با دستور
# مستقیم تو تلگرام)، نتیجه رو به این چت آیدی می‌فرسته
RSS_NOTIFY_CHAT_ID = _env_int("RSS_NOTIFY_CHAT_ID", 0) or (
    next(iter(ALLOWED_USER_IDS)) if ALLOWED_USER_IDS else 0
)

# --- رابط وب (اختیاری) ---
# داشبورد ساده برای مدیریت از مرورگر، بدون نیاز به تلگرام
WEBUI_ENABLED = _env_bool("WEBUI_ENABLED", False)
WEBUI_HOST = _env("WEBUI_HOST", "127.0.0.1")  # رو سرور با Docker این معمولاً 0.0.0.0 میشه
WEBUI_PORT = _env_int("WEBUI_PORT", 8090)
WEBUI_PASSWORD = _env("WEBUI_PASSWORD", "change-me-please")
# لینک عمومی HTTPS که /webapp توی تلگرام بازش می‌کنه (الزام خود تلگرام:
# فقط HTTPS، نه http://localhost). بدون این، دکمه‌ی WebApp کار نمی‌کنه
# ولی خود داشبورد از پسورد معمولی تو مرورگر هنوز قابل استفاده‌ست.
WEBUI_PUBLIC_URL = _env("WEBUI_PUBLIC_URL", "")

# --- اعلان جایگزین (وقتی تلگرام در دسترس نیست) ---
NTFY_ENABLED = _env_bool("NTFY_ENABLED", False)
NTFY_URL = _env("NTFY_URL", "")  # مثلاً https://ntfy.sh/my-leechdar-alerts

# --- زیرنویس خودکار (yt-dlp) ---
YTDLP_SUBTITLES = _env_bool("YTDLP_SUBTITLES", False)
YTDLP_SUB_LANGS = _env("YTDLP_SUB_LANGS", "fa,en")

# --- تایید دوم قبل از حذف قطعی ---
REQUIRE_CANCEL_CONFIRM = _env_bool("REQUIRE_CANCEL_CONFIRM", True)

# --- زمان‌بندی دانلود ---
# اگه فعال باشه، دانلود جدید فقط تو این بازه‌ی ساعتی (به وقت سرور،
# ۲۴ساعته) شروع می‌شه؛ خارج از بازه صف می‌مونه تا وقتش برسه.
SCHEDULE_ENABLED = _env_bool("SCHEDULE_ENABLED", False)
SCHEDULE_START_HOUR = _env_int("SCHEDULE_START_HOUR", 0)  # مثلاً 1 برای ساعت ۱ بامداد
SCHEDULE_END_HOUR = _env_int("SCHEDULE_END_HOUR", 24)  # مثلاً 7 برای ساعت ۷ صبح

# --- GoFile (اختیاری) ---
GOFILE_ENABLED = _env_bool("GOFILE_ENABLED", False)
GOFILE_API_TOKEN = _env("GOFILE_API_TOKEN", "")  # خالی = آپلود مهمان (لینک موقت)

# --- PixelDrain (اختیاری) ---
PIXELDRAIN_ENABLED = _env_bool("PIXELDRAIN_ENABLED", False)
PIXELDRAIN_API_KEY = _env("PIXELDRAIN_API_KEY", "")  # خالی = آپلود مهمان

# --- Mega.nz (اختیاری، فقط دانلود لینک مستقیم) ---
MEGA_EMAIL = _env("MEGA_EMAIL", "")  # خالی = لاگین anonymous (برای لینک‌های عمومی کافیه)
MEGA_PASSWORD = _env("MEGA_PASSWORD", "")

# --- JDownloader / MyJDownloader (اختیاری، فقط فوروارد لینک) ---
JDOWNLOADER_ENABLED = _env_bool("JDOWNLOADER_ENABLED", False)
JDOWNLOADER_EMAIL = _env("JDOWNLOADER_EMAIL", "")
JDOWNLOADER_PASSWORD = _env("JDOWNLOADER_PASSWORD", "")
JDOWNLOADER_DEVICE_NAME = _env("JDOWNLOADER_DEVICE_NAME", "")

# --- صف و همزمانی ---
# چند تا تورنت هم‌زمان دانلود بشه؛ بقیه صف می‌شن و خودکار وقتی یکی
# تموم شد شروع می‌شن
MAX_CONCURRENT_DOWNLOADS = _env_int("MAX_CONCURRENT_DOWNLOADS", 2)

# --- فضای دیسک ---
# اگه فضای آزاد کمتر از این مقدار (گیگابایت) باشه، دانلود جدید رد میشه
MIN_FREE_DISK_GB = _env_int("MIN_FREE_DISK_GB", 5)
DISK_CHECK_PATH = _env("DISK_CHECK_PATH", ".")

# --- جلوگیری از دانلود تکراری ---
# اگه همون هش قبلاً کامل و آپلود شده باشه، دوباره اجازه نمیده
# (مگر با /forceadd)
STOP_DUPLICATES = _env_bool("STOP_DUPLICATES", True)

# --- لاگ ---
LOG_LEVEL = _env("LOG_LEVEL", "INFO")
LOG_FILE = _env("LOG_FILE", "work/logs/leechdar.log")
