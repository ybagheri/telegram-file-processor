import logging

from config import Paths


def get_logger(name: str):
    logger = logging.getLogger(name)

    if not logger.handlers:
        log_dir = Paths.LOGS
        log_dir.mkdir(parents=True, exist_ok=True)

        handler = logging.FileHandler(log_dir / "app.log")
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
