"""Logging utilities for video_transcode."""

import logging
import sys
from pathlib import Path

from loguru import logger

from video_transcode.constants import LogLevel


def instantiate_logger(
    verbosity: int, log_file: Path, log_to_file: bool
) -> None:  # pragma: no cover
    """Instantiate the Loguru logger for video_transcode.

    Args:
        verbosity (int): Verbosity level
        log_file (Path): Path to log file
        log_to_file (bool): Whether to log to file
    """
    logger.remove()
    logger.add(
        sys.stdout,
        level=LogLevel(verbosity).name,
        colorize=True,
        format="<level>{level: <8}</level> | <level>{message}</level> <fg #c5c5c5>({name}:{function}:{line})</fg #c5c5c5>"
        if LogLevel(verbosity) in {LogLevel.DEBUG, LogLevel.TRACE}
        else "<level>{level: <8}</level> | <level>{message}</level>",
        enqueue=True,
    )
    if log_to_file:
        logger.add(
            log_file,
            level=LogLevel(verbosity).name,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message} ({name})",
            rotation="50 MB",
            retention=2,
            compression="zip",
            enqueue=True,
        )

    # Intercept standard sh logs and redirect to Loguru
    if LogLevel(verbosity) in {LogLevel.DEBUG, LogLevel.TRACE}:
        logging.getLogger("sh").setLevel(level="INFO")
        logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)


class InterceptHandler(logging.Handler):  # pragma: no cover
    """Intercepts standard logging and redirects to Loguru.

    This class is a logging handler that intercepts standard logging messages and redirects them to Loguru, a third-party logging library. When a logging message is emitted, this handler determines the corresponding Loguru level for the message and logs it using the Loguru logger.

    Methods:
        emit: Intercepts standard logging and redirects to Loguru.

    Examples:
    To use the InterceptHandler with the Python logging module:
    ```
    import logging
    from logging import StreamHandler

    from loguru import logger

    # Create a new InterceptHandler and add it to the Python logging module.
    intercept_handler = InterceptHandler()
    logging.basicConfig(handlers=[StreamHandler(), intercept_handler], level=logging.INFO)

    # Log a message using the Python logging module.
    logging.info("This message will be intercepted by the InterceptHandler and logged using Loguru.")
    ```
    """

    @staticmethod
    def emit(record):  # type: ignore [no-untyped-def]
        """Intercepts standard logging and redirects to Loguru.

        This method is called by the Python logging module when a logging message is emitted. It intercepts the message and redirects it to Loguru, a third-party logging library. The method determines the corresponding Loguru level for the message and logs it using the Loguru logger.

        Args:
            record: A logging.LogRecord object representing the logging message.

        """
        # Get corresponding Loguru level if it exists.
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # Find caller from where originated the logged message.
        frame, depth = sys._getframe(6), 6
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())
