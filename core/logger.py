import logging
import sys
from pathlib import Path

from config import Paths


def _entrypoint_log_name() -> str:
    """bot.py and worker.py are two separate long-running processes; give
    each its own log file instead of interleaving both into one app.log."""

    script = Path(sys.argv[0]).stem if sys.argv and sys.argv[0] else ""

    if script in ("bot", "worker"):
        return f"{script}.log"

    return "app.log"


def get_logger(name: str):
    logger = logging.getLogger(name)

    if not logger.handlers:
        log_dir = Paths.LOGS
        log_dir.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler(log_dir / _entrypoint_log_name())
        handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(handler)

        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        logger.addHandler(console)

        logger.setLevel(logging.INFO)

    return logger
