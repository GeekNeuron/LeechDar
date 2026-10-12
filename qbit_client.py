"""لایه‌ی نازک روی qbittorrent-api برای عملیات پرکاربرد."""

from __future__ import annotations

import qbittorrentapi


def get_client(host: str, port: int, username: str, password: str) -> qbittorrentapi.Client:
    client = qbittorrentapi.Client(host=host, port=port, username=username, password=password)
    client.auth_log_in()
    return client


def find_by_short_hash(client: qbittorrentapi.Client, category: str, short_hash: str):
    """چون هش کامل تورنت طولانیه، اجازه میدیم کاربر فقط ۸ کاراکتر اول رو بفرسته."""
    torrents = client.torrents_info(category=category)
    matches = [t for t in torrents if t.hash.startswith(short_hash.lower())]
    return matches[0] if len(matches) == 1 else None


def pause_torrent(client: qbittorrentapi.Client, torrent_hash: str) -> None:
    client.torrents_pause(torrent_hashes=torrent_hash)


def resume_torrent(client: qbittorrentapi.Client, torrent_hash: str) -> None:
    client.torrents_resume(torrent_hashes=torrent_hash)


def cancel_torrent(client: qbittorrentapi.Client, torrent_hash: str, delete_files: bool = True) -> None:
    client.torrents_delete(torrent_hashes=torrent_hash, delete_files=delete_files)


def list_files(client: qbittorrentapi.Client, torrent_hash: str):
    """لیست فایل‌های یه تورنت با ایندکس، اسم، سایز و اولویت فعلی."""
    return client.torrents_files(torrent_hash=torrent_hash)


def set_file_selection(client: qbittorrentapi.Client, torrent_hash: str, selected_indices: set[int]) -> None:
    """
    فقط فایل‌های با ایندکس تو selected_indices دانلود می‌شن (اولویت
    عادی)، بقیه اولویت صفر (skip) می‌گیرن و دانلود نمی‌شن.
    """
    files = client.torrents_files(torrent_hash=torrent_hash)
    to_download = [f.id for f in files if f.id in selected_indices]
    to_skip = [f.id for f in files if f.id not in selected_indices]
    if to_download:
        client.torrents_file_priority(torrent_hash=torrent_hash, file_ids=to_download, priority=1)
    if to_skip:
        client.torrents_file_priority(torrent_hash=torrent_hash, file_ids=to_skip, priority=0)


# ------------------------------------------------------------------
# کنترل سید (نسبت/زمان)
# ------------------------------------------------------------------

def set_share_limits(
    client: qbittorrentapi.Client,
    torrent_hash: str,
    ratio_limit: float = -2,
    seeding_time_limit: int = -2,
    inactive_seeding_time_limit: int = -2,
) -> None:
    """
    -۲ یعنی «از مقدار سراسری qBittorrent استفاده کن»، -۱ یعنی
    «بدون محدودیت». هر سه پارامتر لازمه، وگرنه qBittorrent خطای
    «پارامتر ناقص» می‌ده.
    """
    client.torrents_set_share_limits(
        torrent_hashes=torrent_hash,
        ratio_limit=ratio_limit,
        seeding_time_limit=seeding_time_limit,
        inactive_seeding_time_limit=inactive_seeding_time_limit,
    )


# ------------------------------------------------------------------
# RSS (فید و قانون دانلود خودکار) — قابلیت بومی خود qBittorrent
# ------------------------------------------------------------------

def rss_add_feed(client: qbittorrentapi.Client, url: str, name: str | None = None) -> None:
    client.rss_add_feed(url=url, item_path=name or url)


def rss_remove_feed(client: qbittorrentapi.Client, name: str) -> None:
    client.rss_remove_item(item_path=name)


def rss_list_feeds(client: qbittorrentapi.Client):
    return client.rss_items(include_feed_data=False)


def rss_set_rule(
    client: qbittorrentapi.Client,
    rule_name: str,
    must_contain: str,
    category: str,
    affected_feeds: list[str] | None = None,
    must_not_contain: str = "",
) -> None:
    rule_def = {
        "enabled": True,
        "mustContain": must_contain,
        "mustNotContain": must_not_contain,
        "useRegex": False,
        "episodeFilter": "",
        "smartFilter": False,
        "previouslyMatchedEpisodes": [],
        "affectedFeeds": affected_feeds or [],
        "ignoreDays": 0,
        "lastMatch": "",
        "addPaused": None,
        "assignedCategory": category,
        "savePath": "",
    }
    client.rss_set_rule(rule_name=rule_name, rule_def=rule_def)


def rss_list_rules(client: qbittorrentapi.Client):
    return client.rss_rules()


def rss_remove_rule(client: qbittorrentapi.Client, rule_name: str) -> None:
    client.rss_remove_rule(rule_name)


# ------------------------------------------------------------------
# جستجوی تورنت (نیازمند نصب پلاگین جستجو از داخل خود qBittorrent)
# ------------------------------------------------------------------

def search_start(client: qbittorrentapi.Client, pattern: str, category: str = "all", plugins: str = "enabled") -> int:
    job = client.search_start(pattern=pattern, plugins=plugins, category=category)
    return job.id


def search_status(client: qbittorrentapi.Client, search_id: int):
    return client.search_status(search_id=search_id)


def search_results(client: qbittorrentapi.Client, search_id: int, limit: int = 10):
    return client.search_results(search_id=search_id, limit=limit)


def search_stop(client: qbittorrentapi.Client, search_id: int) -> None:
    try:
        client.search_stop(search_id=search_id)
    except Exception:
        pass  # ممکنه از قبل خودش تموم شده باشه
