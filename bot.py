import os
import re
import time
import asyncio
import logging
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
BOT_TOKEN = os.getenv("BOT_TOKEN")  # Токен задаётся в переменных окружения
CHANNEL_USERNAME = "@nikkatfun"
ADMIN_ID = 985545005  # ТВОЙ ID
DOWNLOAD_PATH = "downloads"
RATE_LIMIT_SECONDS = 60  # 1 видео в минуту

if not BOT_TOKEN:
    raise RuntimeError("❌ Переменная окружения BOT_TOKEN не установлена")

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

# ================= ПРОВЕРКА ПОДПИСКИ =================
async def check_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        member = await context.bot.get_chat_member(
            CHANNEL_USERNAME, update.effective_user.id
        )
        return member.status in ["member", "administrator", "creator"]
    except Exception:
        return False

# ================= START =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_sub(update, context):
        await update.message.reply_text(
            "🔒 Для работы с ботом подпишитесь на канал:\n"
            "👉 https://t.me/nikkatfun\n\n"
            "После подписки снова напишите /start"
        )
        return

    await update.message.reply_text(
        "👋 Отправь ссылку на видео:\n\n"
        "🎬 YouTube\n🎵 TikTok\n📌 Pinterest\n\n"
        "Я предложу выбор качества и отправлю файл.\n\n"
        "⏱ Лимит: 1 видео в минуту\n"
        "🔥 Очередь загрузок включена"
    )

# ================= АДМИН ПАНЕЛЬ =================
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return

    text = (
        "👑 Админ-панель\n\n"
        f"🔥 В очереди: {len(download_queue)}\n"
        f"👥 Пользователей в системе: {len(user_last_download)}\n\n"
        "Команды:\n"
        "/clearqueue — очистить очередь\n"
        "/showlog — последние логи\n"
    )
    await update.message.reply_text(text)

async def clearqueue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    download_queue.clear()
    logging.info("Админ очистил очередь")
    await update.message.reply_text("🔥 Очередь очищена.")

async def showlog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    try:
        with open("bot.log", "r", encoding="utf-8") as f:
            lines = f.readlines()[-20:]
        await update.message.reply_text("🧾 Последние логи:\n\n" + "".join(lines))
    except Exception:
        await update.message.reply_text("❌ Не удалось прочитать лог.")

# ================= ПОЛУЧЕНИЕ ССЫЛКИ =================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    if not await check_sub(update, context):
        await update.message.reply_text(
            "🔒 Подпишитесь на канал:\n👉 https://t.me/nikkatfun\n\n"
            "После этого отправьте ссылку снова."
        )
        return

    now = time.time()
    if user_id in user_last_download and now - user_last_download[user_id] < RATE_LIMIT_SECONDS:
        await update.message.reply_text("⏱ Лимит: 1 видео в минуту. Подожди немного.")
        return

    url = update.message.text.strip()
    if not re.match(r"https?://", url):
        await update.message.reply_text("❌ Это не похоже на ссылку.")
        return

    await update.message.reply_text("🔍 Анализирую ссылку...")

    ydl_opts = {"quiet": True}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        logging.error(f"Ошибка анализа ссылки: {e}")
        await update.message.reply_text("❌ Не удалось обработать ссылку.")
        return

    formats = []
    for f in info.get("formats", []):
        if f.get("vcodec") != "none" and f.get("acodec") != "none" and f.get("height"):
            formats.append((f["format_id"], f'{f["height"]}p'))

    if not formats:
        await update.message.reply_text("❌ Подходящие форматы не найдены.")
        return

    unique = list(dict(formats).items())[:5]
    buttons = []
    for fmt_id, label in unique:
        buttons.append(
            [InlineKeyboardButton(label, callback_data=f"dl|{fmt_id}|{url}")]
        )

    await update.message.reply_text(
        "🎥 Выберите качество:",
        reply_markup=InlineKeyboardMarkup(buttons),
    )

# ================= CALLBACK: ДОБАВЛЕНИЕ В ОЧЕРЕДЬ =================
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data.split("|")
    if data[0] != "dl":
        return

    fmt_id, url = data[1], data[2]
    user_id = query.from_user.id

    async with queue_lock:
        download_queue.append((query, fmt_id, url, user_id))
        position = len(download_queue)

    logging.info(f"Пользователь {user_id} добавлен в очередь. Позиция: {position}")
    await query.edit_message_text(f"🔥 Задача добавлена в очередь. Позиция: {position}")

# ================= ОБРАБОТЧИК ОЧЕРЕДИ =================
async def queue_worker(app):
    while True:
        if download_queue:
            async with queue_lock:
                query, fmt_id, url, user_id = download_queue.popleft()

            logging.info(f"Начало загрузки для пользователя {user_id}")

            try:
                await query.message.edit_text("⏬ Скачиваю видео...")

                ydl_opts = {
                    "format": fmt_id,
                    "outtmpl": f"{DOWNLOAD_PATH}/%(title)s.%(ext)s",
                    "quiet": True,
                }

                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url)
                    filename = ydl.prepare_filename(info)

                await query.message.edit_text("📤 Отправляю видео...")

                with open(filename, "rb") as f:
                    await app.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        caption="✅ Готово!",
                    )

                os.remove(filename)
                user_last_download[user_id] = time.time()
                logging.info(f"Успешно отправлено пользователю {user_id}")

            except Exception as e:
                logging.error(f"Ошибка загрузки: {e}")
                try:
                    await query.message.edit_text("❌ Ошибка при скачивании или отправке.")
                except Exception:
                    pass

        await asyncio.sleep(2)

# ================= ЗАПУСК =================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("clearqueue", clearqueue))
    app.add_handler(CommandHandler("showlog", showlog))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(callback_handler))

    loop = asyncio.get_event_loop()
    loop.create_task(queue_worker(app))

    print("🤖 Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
