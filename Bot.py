import os
import tempfile
import logging
import asyncio
import aiohttp
from urllib.parse import urlparse
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ========== CONFIG ==========
BOT_TOKEN = "8423471671:AAFP91OMl_C1gy-A4konZXDS-6hJKf2mElQ"   # from @BotFather
# =============================

# Enable logging
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# Store file paths per user (in a real deployment use a database)
user_files = {}          # user_id -> list of file paths

# ---------- file helpers ----------
def is_safe_url(url: str) -> bool:
    """Basic check to prevent local file access etc."""
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https")

# ---------- search engine ----------
FIELD_MAP = {
    "id": "id",
    "first_name": "first_name",
    "last_name": "last_name",
    "phone": "phone",
    "username": "username",
    "all": "all",
}

def extract_field(line: str, field: str) -> str | None:
    """Extract a specific field from a JSON data line. Returns string value or None."""
    if field == "all":
        return None
    try:
        import json
        outer = json.loads(line)
        # Try inner "message" JSON string
        if "message" in outer and isinstance(outer["message"], str):
            inner = json.loads(outer["message"])
            if field == "id" and "id" in inner:
                return str(inner["id"])
            if field == "first_name" and inner.get("first_name"):
                return inner["first_name"]
            if field == "last_name" and inner.get("last_name"):
                return inner["last_name"]
            if field == "username" and inner.get("username"):
                return inner["username"]
            if field == "phone" and inner.get("phone"):
                return inner["phone"]
        # Fallback to top-level
        if field == "id" and "id" in outer:
            return str(outer["id"])
        if field == "phone" and "phone" in outer:
            return outer["phone"]
    except (json.JSONDecodeError, TypeError):
        pass
    return None

def line_matches(line: str, query: str, field: str) -> bool:
    """True if the line contains the query in the given field (or whole line)."""
    if field == "all":
        return query in line.lower()
    value = extract_field(line, field)
    if value is None:
        return False
    # For id/phone – substring match; for names – case‑insensitive substring
    if field in ("id", "phone"):
        return query in value
    return query in value.lower()

# ---------- bot commands ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 **Telegram Contacts Log Search Bot**\n\n"
        "Send me one or more `.log` files (as document) or a direct download link.\n"
        "Then use:\n"
        "`/search field query` – e.g. `/search first_name رزمیار`\n"
        "`/files` – list stored files\n"
        "`/clear` – remove all your files\n\n"
        "Supported fields: `id`, `first_name`, `last_name`, `phone`, `username`, `all`",
        parse_mode="Markdown"
    )

async def files_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    files = user_files.get(uid, [])
    if not files:
        await update.message.reply_text("You haven't uploaded any files yet.")
        return
    msg = "**Your files:**\n" + "\n".join(f"• `{os.path.basename(f)}`" for f in files)
    await update.message.reply_text(msg, parse_mode="Markdown")

async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in user_files:
        # delete temp files
        for f in user_files[uid]:
            try:
                os.remove(f)
            except OSError:
                pass
        del user_files[uid]
    await update.message.reply_text("All your files have been removed.")

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    files = user_files.get(uid, [])
    if not files:
        await update.message.reply_text("Send me a file or a link first, then search.")
        return

    # Parse arguments: /search field query
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "Usage: `/search field query`\n"
            "Example: `/search first_name رزمیار`\n"
            "Field can be: id, first_name, last_name, phone, username, all",
            parse_mode="Markdown"
        )
        return

    field = args[0].lower()
    query = " ".join(args[1:]).lower()  # join remaining words, then lowercase (Persian stays same)

    if field not in FIELD_MAP:
        await update.message.reply_text(
            f"Unknown field '{field}'. Choose from: {', '.join(FIELD_MAP.keys())}")
        return

    await update.message.reply_text("🔍 Searching through your files…")
    status_msg = await update.message.reply_text("0 matches found so far…")

    # Use a temporary file to collect results (to avoid memory issues with many matches)
    fd, out_path = tempfile.mkstemp(suffix=".txt", prefix="search_")
    total_matches = 0

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            for file_path in files:
                file_name = os.path.basename(file_path)
                index_line = None
                line_count = 0
                try:
                    with open(file_path, "r", encoding="utf-8") as f:
                        for raw_line in f:
                            line = raw_line.strip()
                            if not line:
                                continue
                            line_count += 1
                            if line.startswith('{"index":'):
                                index_line = line
                                continue
                            if index_line and line_matches(line, query, field):
                                out.write(f"── File: {file_name} ──\n")
                                out.write(index_line + "\n")
                                out.write(line + "\n\n")
                                total_matches += 1
                                # Update status every 50 matches
                                if total_matches % 50 == 0:
                                    try:
                                        await status_msg.edit_text(f"{total_matches} matches found…")
                                    except:
                                        pass
                            # Reset index_line regardless
                            index_line = None
                except Exception as e:
                    out.write(f"⚠️ Error reading {file_name}: {str(e)}\n\n")

        # Send the result file
        if total_matches == 0:
            await status_msg.edit_text("No matches found.")
            os.remove(out_path)
            return

        await status_msg.edit_text(f"✅ Search complete. {total_matches} matches found. Sending file…")
        with open(out_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename="search_results.txt",
                caption=f"Search results for field='{field}' query='{query}': {total_matches} matches."
            )
    finally:
        # Cleanup
        if os.path.exists(out_path):
            try:
                os.remove(out_path)
            except:
                pass

# ---------- handle file uploads & links ----------
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    document = update.message.document
    if not document.file_name:
        await update.message.reply_text("Unnamed file, ignoring.")
        return

    # Check extension
    if not document.file_name.lower().endswith(('.log', '.txt', '.jsonl', '.json')):
        await update.message.reply_text("Please send a .log, .txt, or .jsonl file.")
        return

    # Download the file to a temp directory
    await update.message.reply_text("⬇️ Downloading file…")
    file = await context.bot.get_file(document.file_id)
    tmp_path = tempfile.mktemp(suffix="_" + document.file_name)
    await file.download_to_drive(tmp_path)

    # Store for the user
    if uid not in user_files:
        user_files[uid] = []
    user_files[uid].append(tmp_path)

    file_size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
    await update.message.reply_text(
        f"✅ File `{document.file_name}` saved ({file_size_mb:.1f} MB). "
        f"Total files: {len(user_files[uid])}.\n"
        f"Now use /search field query.",
        parse_mode="Markdown"
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """If the message is a URL, try to download it."""
    text = update.message.text.strip()
    if not text.startswith(("http://", "https://")):
        # Not a URL – ignore (maybe the user is just chatting)
        return

    if not is_safe_url(text):
        await update.message.reply_text("Unsupported URL scheme.")
        return

    uid = update.effective_user.id
    await update.message.reply_text("⬇️ Downloading from link… (this may take a while)")
    try:
        # Stream download to temp file
        tmp_path = tempfile.mktemp(suffix=".log")
        async with aiohttp.ClientSession() as session:
            async with session.get(text) as resp:
                if resp.status != 200:
                    await update.message.reply_text(f"Download failed: HTTP {resp.status}")
                    return
                with open(tmp_path, "wb") as f:
                    while True:
                        chunk = await resp.content.read(1024 * 1024)  # 1 MB chunks
                        if not chunk:
                            break
                        f.write(chunk)

        # Store
        if uid not in user_files:
            user_files[uid] = []
        user_files[uid].append(tmp_path)
        file_size_mb = os.path.getsize(tmp_path) / (1024 * 1024)
        await update.message.reply_text(
            f"✅ Downloaded file saved ({file_size_mb:.1f} MB). Total files: {len(user_files[uid])}.\n"
            "Use /search field query."
        )
    except Exception as e:
        await update.message.reply_text(f"Download error: {str(e)}")

# ---------- main ----------
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("files", files_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("Bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
