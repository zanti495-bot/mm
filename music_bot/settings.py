import os

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
MAX_DURATION_SECONDS = 20 * 60
MAX_AUDIO_SIZE_BYTES = 49 * 1024 * 1024
DOWNLOAD_WORKERS = 2
MAX_PENDING_DOWNLOADS = 50

try:
    ADMIN_IDS = {
        int(value.strip())
        for value in os.getenv("ADMIN_IDS", "").split(",")
        if value.strip()
    }
except ValueError as error:
    raise RuntimeError(
        "ADMIN_IDS в .env должен содержать числовые Telegram ID."
    ) from error
