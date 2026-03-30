from loguru import logger


def setup_logger(with_debug: bool = False) -> None:
    log_path = "data/logs"
    logger.add(
        f"{log_path}/app.log", rotation="1 hour", compression="zip", level="DEBUG" if with_debug else "INFO"
    )
