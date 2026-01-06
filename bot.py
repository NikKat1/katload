import os
import re
import time
import asyncio
import logging
import traceback
from collections import deque
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# ================= НАСТРОЙКИ =================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = "@nikkatfun"
ADMIN_ID = 985545005
DOWNLOAD_PATH = "downloads"
RATE_LIMIT_SECONDS = 60  # 1 видео в минуту

if not BOT_TOKEN:
    raise RuntimeError("❌ Не задана переменная окружения BOT_TOKEN")

os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# ================= ЛОГИ =================
logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logging.info("Бот запущен")

# ================= ПАМЯТЬ =================
user_last_download = {}
download_queue = deque()
queue_lock = asyncio.Lock()

# ================= ПРОГРЕСС =================
def make_progress_hook(app, chat_id, message_id):
    last = {"p": -1}
    def hook(d):
        try:
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate")
                got = d.get("downloaded_bytes", 0)
                if total:
                    p = int(got * 100 / total)
                    if p != last["p"]:
                        last["p"] = p
                        app.create_task(app.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=message_id,
                            text=f"⏬ Скачиваю: {p}%"
                        ))
            elif d.get("status") == "finished":
                app.create_task(app.bot.edit_message_text(
                    chat_id=chat_id,
                    message_id=message_id,
                    text="📤 Отправляю видео..."
                ))
        except:
            pass
    return hook

# ================= ПРОВЕРКА ПОДПИСКИ =================
async def check_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        m = await context.bot.get_chat_member(CHANNEL_USERNAME, update.effective_user.id)
        return m.status in ["member", "administrator", "creator"]
    except:
        return False

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_sub(update, context):
        await update.message.reply_text(
            "🔒 Подпишись на канал: https://t.me/nikkatfun\n"
            "После подписки напиши /start"
        )
        return
    await update.message.reply_text(
        "Отправь ссылку на видео (YouTube / TikTok / Pinterest).\n"
        "Я дам выбор качества и отправлю MP4.\n"
        "⏱ Лимит: 1 видео в минуту"
    )

# ================= ПОЛУЧЕНИЕ ССЫЛКИ =================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    if not await check_sub(update, context):
        await update.message.reply_text("🔒 Подпишись: https://t.me/nikkatfun")
        return

    now = time.time()
    if uid in user_last_download and now - user_last_download[uid] < RATE_LIMIT_SECONDS:
        await update.message.reply_text("⏱ Лимит: 1 видео в минуту.")
        return

    url = update.message.text.strip()
    if not re.match(r"https?://", url):
        await update.message.reply_text("❌ Это не ссылка.")
        return

    await update.message.reply_text("🔍 Анализирую...")

    try:
        with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except:
        logging.error(traceback.format_exc())
        await update.message.reply_text("❌ Не удалось обработать ссылку.")
        return

    formats = []
    for f in info.get("formats", []):
        if f.get("vcodec") != "none" and f.get("height"):
            formats.append((f["format_id"], f'{f["height"]}p'))

    if not formats:
        await update.message.reply_text("❌ Форматы не найдены.")
        return

    uniq, seen = [], set()
    for fid, lab in sorted(formats, key=lambda x: int(x[1].replace("p",""))):
        if lab not in seen:
            seen.add(lab); uniq.append((fid, lab))
        if len(uniq) >= 6: break

    buttons = [[InlineKeyboardButton(lab, callback_data=f"dl|{fid}|{url}")] for fid, lab in uniq]
    buttons.append([InlineKeyboardButton("🔥 Максимальное качество (MP4)", callback_data=f"dl|best|{url}")])

    await update.message.reply_text("🎥 Выбери качество:", reply_markup=InlineKeyboardMarkup(buttons))

# ================= CALLBACK =================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    _, fid, url = q.data.split("|")
    uid = q.from_user.id

    async with queue_lock:
        download_queue.append((q, fid, url, uid))
        pos = len(download_queue)

    await q.edit_message_text(f"🔥 В очереди: {pos}")

# ================= ОЧЕРЕДЬ =================
async def queue_worker(app):
    while True:
        if download_queue:
            async with queue_lock:
                q, fid, url, uid = download_queue.popleft()
            try:
                await q.message.edit_text("⏬ Скачиваю: 0%")

                # БЕЗ FFMPEG: только готовый MP4 со звуком
                if fid == "best":
                    fmt = "best[ext=mp4]/best"
                else:
                    fmt = f"{fid}[ext=mp4]/best"

                ydl_opts = {
                    "format": fmt,
                    "outtmpl": f"{DOWNLOAD_PATH}/%(title)s.%(ext)s",
                    "quiet": True,
                    "noplaylist": True,
                    "concurrent_fragment_downloads": 4,
                    "http_chunk_size": 10485760,
                    "socket_timeout": 30,
                    "retries": 3,
                    "progress_hooks": [
                        make_progress_hook(app, q.message.chat_id, q.message.message_id)
                    ],
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url)
                    filename = ydl.prepare_filename(info)

                size_mb = os.path.getsize(filename) / (1024 * 1024)

                # 🔥 Быстрая и надёжная отправка — всегда как файл
                with open(filename, "rb") as f:
                    await app.bot.send_document(
                        chat_id=q.message.chat_id,
                        document=f,
                        caption="✅ Готово (MP4)",
                        disable_content_type_detection=True
                    )

                os.remove(filename)
                user_last_download[uid] = time.time()

            except:
                logging.error(traceback.format_exc())
                try:
                    await q.message.edit_text("❌ Ошибка при скачивании или отправке.")
                except:
                    pass
        await asyncio.sleep(2)

# ================= ЗАПУСК =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(callback_handler))
    asyncio.get_event_loop().create_task(queue_worker(app))
    print("🤖 Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
