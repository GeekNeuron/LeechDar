FROM python:3.12-slim

# unrar-free برای استخراج rar، curl برای نصب rclone، aria2 برای لینک مستقیم
RUN apt-get update && apt-get install -y --no-install-recommends \
    unrar-free \
    curl \
    aria2 \
    && curl https://rclone.org/install.sh | bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# پوشه‌ی مشترک با qBittorrent برای خوندن فایل‌های دانلودشده
RUN mkdir -p /downloads work

CMD ["python", "bot.py"]
