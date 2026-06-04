from dotenv import load_dotenv
import os

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "56160879-8ab47830d767f433724dacfcd")
NICHE = os.getenv("YOUTUBE_CHANNEL_NICHE", "Pet Animals Facts (Cats, Dogs, etc.)")
VIDEOS_PER_DAY = int(os.getenv("VIDEOS_PER_DAY", 1))
UPLOAD_TIME = os.getenv("UPLOAD_TIME", "09:00")
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "public")
YOUTUBE_CATEGORY_ID = os.getenv("YOUTUBE_CATEGORY_ID", "28")
VIDEO_TAGS = os.getenv("VIDEO_TAGS", "pets,cats,dogs,animals,facts,cute").split(",")
MONEY_PRINTER_URL = os.getenv("MONEY_PRINTER_URL", "http://localhost:8080")
VIDEO_LANGUAGE = os.getenv("VIDEO_LANGUAGE", "en")
VOICE_NAME = os.getenv("VOICE_NAME", "en-US-ChristopherNeural")
BGM_TYPE = os.getenv("BGM_TYPE", "random")
BGM_VOLUME = float(os.getenv("BGM_VOLUME", 0.2))
SUBTITLE_ENABLED = os.getenv("SUBTITLE_ENABLED", "true").lower() == "true"
MAX_RETRIES = int(os.getenv("MAX_RETRIES", 3))

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
GEMINI_MODEL = "gemini-2.5-flash"
GEMINI_API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"

GROQ_API_KEYS = [k.strip() for k in os.getenv("GROQ_API_KEYS", "").split(",") if k.strip()]
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
THUMBNAILS_DIR = os.path.join(BASE_DIR, "thumbnails")
OUTPUTS_DIR = os.path.join(BASE_DIR, "outputs")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
CREDENTIALS_DIR = os.path.join(BASE_DIR, "credentials")

for d in [DATA_DIR, THUMBNAILS_DIR, OUTPUTS_DIR, LOGS_DIR, CREDENTIALS_DIR]:
    os.makedirs(d, exist_ok=True)
