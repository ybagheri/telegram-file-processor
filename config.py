import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()

class Config:
    API_ID = int(os.getenv('API_ID'))
    API_HASH = os.getenv('API_HASH')
    BOT_TOKEN = os.getenv('BOT_TOKEN')
    GROUP_ID = int(os.getenv('GROUP_ID'))
    SESSION_NAME = os.getenv('SESSION_NAME')

    BASE_DIR = Path(__file__).parent
    PATHS = {
        "downloads": BASE_DIR / "downloads",
        "outputs": BASE_DIR / "outputs",
        "logs": BASE_DIR / "logs",
        "logo": BASE_DIR / "logo",
    }
    
    JOB_FOLDERS = ["input", "extracted", "output", "temp", "thumbs"]

# Ensure directories exist
for path in Config.PATHS.values():
    path.mkdir(parents=True, exist_ok=True)
