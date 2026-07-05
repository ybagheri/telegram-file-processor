import logging
from pathlib import Path
from config import Config

def get_logger(name: str):
    logger = logging.getLogger(name)
    if not logger.handlers:
        log_dir = Config.PATHS["logs"]
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_dir / "app.log")
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger
