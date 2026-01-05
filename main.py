import os
import time
import sqlite3
import subprocess
from dotenv import load_dotenv

from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters
)

# ================== CONFIG ==================
load_dotenv()
TOKEN = os.getenv("TOKEN")

CHANNEL = "@nikkatfun"
ADMINS = [123456789]  # ← ВСТАВЬ СВОЙ TELEGRAM ID
COOLDOWN = 60

# ================== DATABASE ==================
db = sqlite3.connect("database.db", check_same_thread=False)
cur = db.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    last_time INTEGER DEFAULT 0,
    downloads INTEGER DEFAULT 0
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

# ================== TEXT ==================
START_TEXT = (
    "🔥 *NikKat Downloader*\n\n"
    "📥 *Поддержка:*\n"
    "• YouTube — видео\n"
    "• TikTok — без водяных знаков\n"
    "• Pinterest — фото / видео\n"
    "• Яндекс Музыка — mp3\n\n"
    "⏱ *Лимит:* 1 загрузка / минута\n"
    "📥 Очередь включена\n\n"
    "📌 *Как пользоваться:*\n"
    "1️⃣ Подпишись на @nikkatfun\n"
    "2️⃣ Отправь ссылку\n"
    "3️⃣ Получи файл"
)

# ================== KEYBOARDS ==================
def sub_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Проверить подписку", callback_data="check_sub")],
        [InlineKeyboardButton("📢 Подписаться", url="https://t.me/nikkatfun")]
    ])

# ================== UTILS ==================
async def check_sub(bot, user_id):
    try:
        m = await bot.get_chat_member(CHANNEL, user_id)
        return m.status in ("member", "administrator", "creator")
    except Exception as e:
        print("SUB CHECK ERROR:", e)
        return False

# ================== START ==================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id

    cur.execute("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (uid,))
    db.commit()

    if not await check_sub(context.bot, uid):
        await update.message.reply_text(
            "❗ Для использования подпишись на канал:",
            reply_markup=sub_keyboard()
        )
        return

    await update.message.reply_text(
        START_TEXT,
        parse_mode="Markdown"
    )

# ================== CHECK SUB BUTTON ==================
async def check_sub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id

    await query.answer()

    if await check_sub(context.bot, uid):
        await query.message.edit_text(
            START_TEXT,
            parse_mode="Markdown"
        )
    else:
        await query.answer("❌ Ты ещё не подписан", show_alert=True)

# ================== ADMIN ==================
async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return

    cur.execute("SELECT COUNT(*) FROM users")
    users = cur.fetchone()[0]

    cur.execute("SELECT SUM(downloads) FROM users")
    downloads = cur.fetchone()[0] or 0

    await update.message.reply_text(
        f"👑 *Админка*\n\n"
        f"👥 Пользователей: {users}\n"
        f"📥 Загрузок: {downloads}",
        parse_mode="Markdown"
    )

# ================== HANDLE LINKS ==================
async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    url = update.message.text

    if not await check_sub(context.bot, uid):
        await update.message.reply_text(
            "❌ Сначала подпишись",
            reply_markup=sub_keyboard()
        )
        return

    cur.execute("SELECT last_time FROM users WHERE user_id=?", (uid,))
    last = cur.fetchone()[0]
    now = int(time.time())

    if now - last < COOLDOWN:
        await update.message.reply_text("⏱ Подожди минуту")
        return

    cur.execute("INSERT INTO queue (user_id, url) VALUES (?, ?)", (uid, url))
    cur.execute(
        "UPDATE users SET last_time=?, downloads=downloads+1 WHERE user_id=?",
        (now, uid)
    )
    db.commit()

    await update.message.reply_text("📥 Ссылка добавлена в очередь")

# ================== QUEUE WORKER ==================
async def process_queue(context: ContextTypes.DEFAULT_TYPE):
    cur.execute("SELECT id, user_id, url FROM queue LIMIT 1")
    task = cur.fetchone()

    if not task:
        return

    qid, uid, url = task
    fname = "media"

    try:
        if "music.yandex" in url:
            subprocess.run(
                ["yt-dlp", "-x", "--audio-format", "mp3", f"ytsearch:{url}", "-o", fname],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            await context.bot.send_audio(uid, audio=open(fname + ".mp3", "rb"))
            os.remove(fname + ".mp3")
        else:
            subprocess.run(
                ["yt-dlp", "-f", "mp4", "-o", fname + ".mp4", url],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            await context.bot.send_video(uid, video=open(fname + ".mp4", "rb"))
            os.remove(fname + ".mp4")
    except Exception as e:
        print("DOWNLOAD ERROR:", e)

    cur.execute("DELETE FROM queue WHERE id=?", (qid,))
    db.commit()

# ================== RUN ==================
app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("admin", admin))
app.add_handler(CallbackQueryHandler(check_sub_callback, pattern="^check_sub$"))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))

app.job_queue.run_repeating(process_queue, interval=3, first=3)

print("✅ Bot started")
app.run_polling()
