"""
اجرای یک‌باره: با شماره تلفن خودت وارد میشی (کد تایید از تلگرام میاد)
و در آخر یه SESSION_STRING چاپ میشه که باید تو config.py بذاریش
(PYROGRAM_SESSION_STRING). این کار جایگزین لاگین با هر بار اجرای رباته.

قبلش باید از https://my.telegram.org مقادیر api_id و api_hash رو بگیری
(با همون شماره تلفنی که می‌خوای باهاش آپلود کنی وارد شو، بعد
API Development Tools رو انتخاب کن).

استفاده:
    python generate_session.py
"""

from pyrogram import Client

API_ID = int(input("API ID (از my.telegram.org): ").strip())
API_HASH = input("API HASH (از my.telegram.org): ").strip()

with Client("session_gen", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
    session_string = app.export_session_string()
    print("\n✅ این رو تو config.py بذار:\n")
    print(f'PYROGRAM_API_ID = {API_ID}')
    print(f'PYROGRAM_API_HASH = "{API_HASH}"')
    print(f'PYROGRAM_SESSION_STRING = "{session_string}"')
