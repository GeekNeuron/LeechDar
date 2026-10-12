"""
رابط وب سبک برای LeechDar — داشبورد، تنظیمات، انتخاب فایل.

این ماژول مستقل از تلگرام کار می‌کنه؛ همون فرآیند و همون event loop
ربات رو به اشتراک می‌ذاره (از طریق import مستقیم ماژول bot)، پس به
qBittorrent/aria2/دیتابیس دومرتبه وصل نمی‌شه.

احراز هویت: یه رمز ساده (WEBUI_PASSWORD) + کوکی session در حافظه —
برای استفاده‌ی شخصی پشت یه شبکه‌ی قابل‌اعتماد یا reverse proxy با
HTTPS کافیه؛ برای اینترنت عمومی بدون HTTPS جلوش استفاده نکن.
"""

from __future__ import annotations

import secrets

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import telegram_webapp_auth

app = FastAPI(title="LeechDar")
_valid_tokens: dict[str, int] = {}  # session token -> user_id (تا هرکس تنظیمات خودش رو ببینه)

TELEGRAM_WEBAPP_JS = '<script src="https://telegram.org/js/telegram-web-app.js"></script>'


def _bot():
    import bot  # ایمپورت دیرهنگام تا از circular import جلوگیری بشه

    return bot


def _is_authed(request: Request) -> bool:
    return request.cookies.get("session") in _valid_tokens


def _current_user_id(request: Request) -> int:
    return _valid_tokens.get(request.cookies.get("session"), 0)


def _page(title: str, body: str, authed: bool = True) -> HTMLResponse:
    nav = (
        """
        <nav>
          <a href="/">داشبورد</a>
          <a href="/settings">تنظیمات</a>
          <a href="/logout">خروج</a>
        </nav>
        """
        if authed
        else ""
    )
    html = f"""<!doctype html>
<html lang="fa" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title} — LeechDar</title>
{TELEGRAM_WEBAPP_JS}
<style>
  body {{ font-family: Tahoma, sans-serif; background: #0f1115; color: #e6e6e6; margin: 0; }}
  nav {{ background: #1a1d24; padding: 12px 20px; display: flex; gap: 16px; }}
  nav a {{ color: #8ab4f8; text-decoration: none; }}
  main {{ max-width: 900px; margin: 20px auto; padding: 0 16px; }}
  h1 {{ font-size: 1.3em; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
  th, td {{ padding: 8px; text-align: right; border-bottom: 1px solid #2a2d34; font-size: 0.9em; }}
  input, select {{ background: #1a1d24; color: #e6e6e6; border: 1px solid #333; padding: 6px; border-radius: 4px; }}
  button {{ background: #2563eb; color: white; border: none; padding: 6px 12px; border-radius: 4px; cursor: pointer; }}
  button.danger {{ background: #dc2626; }}
  form.inline {{ display: inline; }}
  .badge {{ padding: 2px 8px; border-radius: 10px; font-size: 0.8em; }}
  .badge.downloading {{ background: #1e40af; }}
  .badge.queued {{ background: #78716c; }}
  .badge.ready {{ background: #15803d; }}
  fieldset {{ border: 1px solid #333; border-radius: 6px; margin-bottom: 16px; }}
  label {{ display: block; margin: 8px 0 4px; }}
</style>
</head>
<body>
{nav}
<main>
{body}
</main>
</body>
</html>"""
    return HTMLResponse(html)


LOGIN_FORM = """
<h1>ورود به LeechDar</h1>
<p id="webapp-status" style="color:#9ca3af"></p>
<form method="post" action="/login" id="password-form">
  <label>رمز عبور (اگه از مرورگر معمولی وارد شدی)</label>
  <input type="password" name="password" required autofocus>
  <button type="submit">ورود</button>
</form>

<script>
(function() {
  const tg = window.Telegram && window.Telegram.WebApp;
  const statusEl = document.getElementById('webapp-status');
  if (tg && tg.initData) {
    statusEl.textContent = 'در حال ورود خودکار با حساب تلگرام...';
    document.getElementById('password-form').style.display = 'none';
    tg.ready();
    fetch('/webapp-auth', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: 'init_data=' + encodeURIComponent(tg.initData),
    }).then(r => {
      if (r.redirected || r.ok) { window.location.href = '/'; }
      else {
        statusEl.textContent = 'ورود خودکار ناموفق بود؛ از رمز عبور استفاده کن.';
        document.getElementById('password-form').style.display = 'block';
      }
    }).catch(() => {
      statusEl.textContent = '';
      document.getElementById('password-form').style.display = 'block';
    });
  }
})();
</script>
"""


@app.get("/login", response_class=HTMLResponse)
async def login_form():
    return _page("ورود", LOGIN_FORM, authed=False)


@app.post("/webapp-auth")
async def webapp_auth(init_data: str = Form(...)):
    """
    ورود خودکار وقتی صفحه از داخل خود تلگرام (Telegram Mini App) باز
    شده باشه — بدون نیاز به تایپ رمز، چون initData امضاشده توسط
    تلگراممه و نشون میده کاربر واقعاً همون کسیه که ربات رو باز کرده.
    """
    b = _bot()
    user = telegram_webapp_auth.validate_init_data(init_data, b.config.TELEGRAM_BOT_TOKEN)
    if not user:
        return JSONResponse({"ok": False}, status_code=401)
    if b.config.ALLOWED_USER_IDS and user.get("id") not in b.config.ALLOWED_USER_IDS:
        return JSONResponse({"ok": False, "reason": "not allowed"}, status_code=403)

    token = secrets.token_hex(16)
    _valid_tokens[token] = int(user["id"])
    resp = JSONResponse({"ok": True})
    resp.set_cookie("session", token, httponly=True, samesite="lax")
    return resp


@app.post("/login")
async def login_submit(password: str = Form(...)):
    b = _bot()
    if secrets.compare_digest(password, b.config.WEBUI_PASSWORD):
        token = secrets.token_hex(16)
        # ورود با رمز (نه initData) یعنی هویت تلگرامی مشخص نیست؛ به‌عنوان
        # اولین کاربر مجاز در نظرش می‌گیریم (برای استفاده‌ی شخصی تک‌کاربره کافیه)
        admin_id = next(iter(b.config.ALLOWED_USER_IDS), 0) if b.config.ALLOWED_USER_IDS else 0
        _valid_tokens[token] = admin_id
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie("session", token, httponly=True, samesite="lax")
        return resp
    return _page("ورود", LOGIN_FORM + "<p style='color:#f87171'>رمز اشتباهه.</p>", authed=False)


@app.get("/logout")
async def logout(request: Request):
    _valid_tokens.pop(request.cookies.get("session"), None)
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    b = _bot()

    rows = []
    for h, item in b.pending.items():
        pct = ""
        if item.get("source") == "ytdlp":
            pct = f"{round(b.downloader_ytdlp.get_progress(h) * 100, 1)}%"
        elif item.get("source") == "aria2" and item.get("aria2_gid"):
            try:
                d = b.aria2_client.get_download(b.get_aria2_api(), item["aria2_gid"])
                pct = f"{round(d.progress, 1)}%"
            except Exception:
                pct = "?"
        actions = f"""
          <form class="inline" method="post" action="/action/{h}"><input type="hidden" name="action" value="upload"><button>آپلود</button></form>
          <form class="inline" method="post" action="/action/{h}"><input type="hidden" name="action" value="cancel"><button class="danger">لغو</button></form>
          <a href="/files/{h}">فایل‌ها</a>
        """
        rows.append(
            f"<tr><td>{h[:8]}</td><td>{item['torrent_name'][:50]}</td>"
            f"<td><span class='badge {item['status']}'>{item['status']}</span></td>"
            f"<td>{item.get('source','torrent')}</td><td>{pct}</td><td>{actions}</td></tr>"
        )

    table = (
        "<table><tr><th>هش</th><th>نام</th><th>وضعیت</th><th>منبع</th><th>پیشرفت</th><th>عملیات</th></tr>"
        + "".join(rows)
        + "</table>"
        if rows
        else "<p>هیچ کاری در جریان نیست.</p>"
    )

    add_form = """
    <fieldset>
      <legend>افزودن جدید</legend>
      <form method="post" action="/add">
        <input type="text" name="link" placeholder="مگنت، لینک مستقیم، یا لینک یوتیوب" style="width:70%">
        <button type="submit">اضافه کن</button>
      </form>
    </fieldset>
    """
    return _page("داشبورد", f"<h1>داشبورد</h1>{add_form}{table}")


@app.post("/add")
async def add_link(request: Request, link: str = Form(...)):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    b = _bot()
    fake_update = _FakeUpdate(request)
    fake_context = _FakeContext(b._running_app)
    link = link.strip()
    if link.startswith("magnet:"):
        await b.add_torrent(fake_update, magnet=link)
    elif b.looks_like_direct_file(link):
        await b.add_aria2_link(fake_update, fake_context, link)
    elif b.downloader_ytdlp.is_supported_url(link):
        await b.add_ytdl_link(fake_update, fake_context, link)
    return RedirectResponse("/", status_code=303)


@app.post("/action/{hash_key}")
async def do_action(hash_key: str, request: Request, action: str = Form(...)):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    b = _bot()
    item = b.pending.get(hash_key)
    if item:
        if action == "upload":
            await b.do_upload(b._running_app, hash_key, item)
        elif action == "cancel":
            fake_update = _FakeUpdate(request)
            fake_context = _FakeContext(b._running_app, args=[hash_key[:8]])
            await b._do_cancel(fake_update, fake_context, hash_key[:8])
    return RedirectResponse("/", status_code=303)


@app.get("/files/{hash_key}", response_class=HTMLResponse)
async def files_page(hash_key: str, request: Request):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    b = _bot()
    try:
        files = b.qbit_client.list_files(b.qb(), hash_key)
    except Exception as e:
        return _page("فایل‌ها", f"<p>خطا: {e}</p>")

    rows = "".join(
        f"<tr><td><input type='checkbox' name='idx' value='{f.id}' {'checked' if f.priority > 0 else ''}></td>"
        f"<td>{f.name}</td><td>{f.size/(1024*1024):.1f}MB</td></tr>"
        for f in files
    )
    body = f"""
    <h1>انتخاب فایل — {hash_key[:8]}</h1>
    <form method="post" action="/files/{hash_key}">
      <table><tr><th>دانلود؟</th><th>نام</th><th>سایز</th></tr>{rows}</table>
      <button type="submit">ذخیره انتخاب</button>
    </form>
    """
    return _page("فایل‌ها", body)


@app.post("/files/{hash_key}")
async def files_submit(hash_key: str, request: Request):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    b = _bot()
    form = await request.form()
    indices = {int(v) for v in form.getlist("idx")}
    try:
        b.qbit_client.set_file_selection(b.qb(), hash_key, indices)
    except Exception as e:
        return _page("فایل‌ها", f"<p>خطا: {e}</p>")
    return RedirectResponse("/", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    b = _bot()
    r = b.get_runtime(_current_user_id(request))
    dest_options = "".join(
        f"<option value='{d}' {'selected' if d in r['destinations'] else ''}>{d}</option>"
        for d in sorted(b.VALID_DESTS)
    )
    body = f"""
    <h1>تنظیمات</h1>
    <form method="post" action="/settings">
      <label>مقصد آپلود (چندتایی قابل انتخاب)</label>
      <select name="destinations" multiple size="3">{dest_options}</select>

      <label>حالت آپلود تلگرام</label>
      <select name="upload_mode">
        <option value="bot_api" {'selected' if r['upload_mode']=='bot_api' else ''}>bot_api (۵۰MB)</option>
        <option value="pyrogram_user" {'selected' if r['upload_mode']=='pyrogram_user' else ''}>pyrogram_user (۲/۴GB)</option>
      </select>

      <label><input type="checkbox" name="is_premium" {'checked' if r['is_premium'] else ''}> تلگرام پریمیوم (سقف ۴GB)</label>

      <label>سایز پارت (مگابایت، خالی = خودکار)</label>
      <input type="number" name="split_size_mb" value="{r['split_size_mb'] or ''}">

      <label><input type="checkbox" name="force_zip" {'checked' if r['force_zip'] else ''}> همیشه زیپ کن</label>

      <br><br>
      <button type="submit">ذخیره</button>
    </form>
    """
    return _page("تنظیمات", body)


@app.post("/settings")
async def settings_submit(request: Request):
    if not _is_authed(request):
        return RedirectResponse("/login", status_code=303)
    b = _bot()
    uid = _current_user_id(request)
    r = b.get_runtime(uid)
    form = await request.form()

    dests = set(form.getlist("destinations")) & b.VALID_DESTS
    if dests:
        r["destinations"] = dests
        b._save_runtime(uid, "destinations")

    mode = form.get("upload_mode")
    if mode in ("bot_api", "pyrogram_user"):
        r["upload_mode"] = mode
        b._save_runtime(uid, "upload_mode")

    r["is_premium"] = "is_premium" in form
    b._save_runtime(uid, "is_premium")

    split_raw = (form.get("split_size_mb") or "").strip()
    r["split_size_mb"] = int(split_raw) if split_raw else None
    b._save_runtime(uid, "split_size_mb")

    r["force_zip"] = "force_zip" in form
    b._save_runtime(uid, "force_zip")

    return RedirectResponse("/settings", status_code=303)


# ------------------------------------------------------------------
# شبیه‌سازهای سبک Update/Context تلگرام، تا بشه از توابع add_torrent/
# add_ytdl_link/add_aria2_link/cmd_cancel که برای تلگرام نوشته شدن
# از وب هم استفاده کرد بدون تکرار منطق.
# ------------------------------------------------------------------

class _FakeMessage:
    def __init__(self):
        self.text_replies: list[str] = []

    async def reply_text(self, text, **kwargs):
        self.text_replies.append(text)


class _FakeChat:
    def __init__(self, chat_id: int):
        self.id = chat_id


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id


class _FakeUpdate:
    def __init__(self, request: Request):
        chat_id = _current_user_id(request) or next(
            iter(_bot().config.ALLOWED_USER_IDS), 0
        )
        self.message = _FakeMessage()
        self.effective_chat = _FakeChat(chat_id)
        self.effective_user = _FakeUser(chat_id)


class _FakeContext:
    def __init__(self, application, args: list[str] | None = None):
        self.application = application
        self.args = args or []


def run_server(host: str, port: int) -> None:
    """این تابع تو یه asyncio task جدا (از داخل bot.py) صدا زده می‌شه."""
    import asyncio

    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.ensure_future(server.serve())
