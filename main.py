import os
import time
import sqlite3
import subprocess
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters
)

# ================== CONFIG ==================
load_dotenv()
TOKEN = os.getenv("TOKEN")

CHANNEL = "@nikkatfun"
ADMINS = [123456789]  # ← твой Telegram ID
COOLDOWN = 60         # 1 загрузка в минуту

# ================== DATABASE ==================
db = sqlite3.connect("database.db", check_same_thread=False)
cur = db.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    last_time INTEGER DEFAULT 0,
    downloads INTEGER DEFAULT 0,
    lang TEXT DEFAULT 'ru'
)
""")

cur.execute("""
CREATE TABLE IF NOT EXISTS queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    url TEXT
)
""")

db.commit()

# ================== TEXTS ==================
TXT = {
    "ru": {
        "start": (
            "🔥 *NikKat Downloader*\n\n"
            "📥 *Поддерживаемые сервисы:*\n"
            "• YouTube — видео\n"
            "• TikTok — без водяных знаков\n"
            "• Pinterest — фото / видео\n"
            "• Яндекс Музыка — mp3\n\n"
            "⏱ *Лимиты:*\n"
            "• 1 загрузка в минуту\n"
            "• Работает очередь\n\n"
            "📌 *Как пользоваться:*\n"
            "1️⃣ Подпишись на @nikkatfun\n"
            "2️⃣ Отправь ссылку\n"
            "3️⃣ Дождись файла\n\n"
            "💡 Просто пришли ссылку"
        ),
        "sub": "❗ Для работы подпишись на канал:",
        "cooldown": "⏱ Подожди 1 минуту",
        "queued": "📥 Ссылка добавлена в очередь"
    }
}

# ================== UTILS ==================
async def check_sub(bot, user_id):
    try:
        m = await bot.get_chat_member(CHANNEL, user_id)
        return m.status in ["member", "administrator", "creator"]
    except:
        return False

# ================== /START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    db.commit()

    if not await check_sub(context.bot, uid):
        await update.message.reply_text(
            TXT["ru"]["sub"],
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Подписаться", url="https://t.me/nikkatfun")]
            ])
        )
        return

    await update.message.reply_text(
        TXT["ru"]["start"],
        parse_mode="Markdown"
    )

# ================== ADMIN ==================
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return

    cur.execute("SELECT COUNT(*) FROM users")
    users = cur.fetchone()[0]

    cur.execute("SELECT SUM(downloads) FROM users")
    downloads = cur.fetchone()[0] or 0

    await update.message.reply_text(
        f"👑 *Админ-панель*\n\n"
        f"👥 Пользователей: {users}\n"
        f"📥 Загрузок: {downloads}",
        parse_mode="Markdown"
    )

# ================== ADD TO QUEUE ==================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    url = update.message.text

    if not await check_sub(context.bot, uid):
        await update.message.reply_text("❌ Подпишись на @nikkatfun")
        return

    cur.execute("SELECT last_time FROM users WHERE user_id=?", (uid,))
    last = cur.fetchone()[0]
    now = int(time.time())

    if now - last < COOLDOWN:
        await update.message.reply_text(TXT["ru"]["cooldown"])
        return

    cur.execute("INSERT INTO queue (user_id, url) VALUES (?, ?)", (uid, url))
    cur.execute(
        "UPDATE users SET last_time=?, downloads=downloads+1 WHERE user_id=?",
        (now, uid)
    )
    db.commit()

    print(f"[QUEUE] {uid} → {url}")
    await update.message.reply_text(TXT["ru"]["queued"])

# ================== QUEUE WORKER ==================
async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT id, user_id, url FROM queue LIMIT 1")
    task = cur.fetchone()

    if not task:
        return

    qid, uid, url = task
    filename = "media"

    try:
        if "music.yandex" in url:
            subprocess.run([
                "yt-dlp",
                "-x",
                "--audio-format", "mp3",
                f"ytsearch:{url}",
                "-o", filename
            ])
            await context.bot.send_audio(uid, audio=open(filename + ".mp3", "rb"))
            os.remove(filename + ".mp3")
        else:
            subprocess.run([
                "yt-dlp",
                "-f", "mp4",
                "-o", filename + ".mp4",
                url
            ])
            await context.bot.send_video(uid, video=open(filename + ".mp4", "rb"))
            os.remove(filename + ".mp4")

    except Exception as e:
        print("ERROR:", e)

    cur.execute("DELETE FROM queue WHERE id=?", (qid,))
    db.commit()

# ================== RUN ==================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

# официальный фоновой воркер
app.job_queue.run_repeating(process_queue, interval=3, first=3)

print("✅ Bot started")
app.run_polling()
