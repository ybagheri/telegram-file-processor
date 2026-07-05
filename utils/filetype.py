from core.logger import get_logger

logger = get_logger(__name__)

class FileTypeDetector:
    @staticmethod
    def detect(mime_type: str, extension: str) -> str:
        ext = extension.lower() if extension else ""
        
        if mime_type.startswith("video/") or ext in [".mp4", ".avi", ".mkv", ".mov"]:
            return "VIDEO"
        elif mime_type.startswith("audio/") or ext in [".mp3", ".wav", ".m4a"]:
            return "AUDIO"
        elif mime_type == "application/pdf" or ext == ".pdf":
            return "PDF"
        elif ext in [".zip", ".rar", ".7z"]:
            return "ARCHIVE"
        elif mime_type.startswith("image/"):
            return "IMAGE"
        return "UNKNOWN"
