import logging
import sys
from typing import Any

from loguru import logger


def setup_logging() -> None:
    # Remove default handlers
    logging.getLogger().handlers = []

    # Configure Loguru
    config: dict[str, Any] = {
        "handlers": [
            {
                "sink": sys.stdout,
                "format": (
                    "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
                    "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
                    "<level>{message}</level>"
                ),
                "level": "INFO",
            },
            {
                "sink": "logs/app.log",
                "format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
                "level": "INFO",
                "rotation": "500 MB",
                "retention": "10 days",
            },
            {
                "sink": "logs/error.log",
                "format": "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
                "level": "ERROR",
                "rotation": "500 MB",
                "retention": "10 days",
            },
        ]
    }

    logger.configure(**config)

    # Intercept standard logging
    class InterceptHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            # Get corresponding Loguru level if it exists
            level: str | int
            try:
                level = logger.level(record.levelname).name
            except ValueError:
                level = record.levelno

            frame = logging.currentframe()
            depth = 2
            while frame is not None and frame.f_code.co_filename == logging.__file__:
                f_back = frame.f_back
                if f_back is None:
                    break
                frame = f_back
                depth += 1

            logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)
