"""Logging utilities for vid-cleaner."""

import logging
import sys
from enum import Enum
from pathlib import Path

from loguru import logger
from rich.markup import escape

from .console import console


class LogLevel(Enum):
    """Log levels for vid-cleaner."""

    INFO = 0
    DEBUG = 1
    TRACE = 2
    WARNING = 3
    ERROR = 4


def log_formatter(record: dict) -> str:
    """Format log records for output with styling based on log level.

    This function takes a log record dictionary and returns a formatted string using the Rich
    library's syntax for text styling. It assigns different colors and icons to log messages
    based on their severity level (e.g., DEBUG, INFO, ERROR). For DEBUG and TRACE levels, it
    includes additional information like the logger name, function, and line number.

    Args:
        record: A dictionary containing log record information. Expected keys include 'level',
                'message', 'name', 'function', and 'line'. The 'level' key should have a 'name'
                attribute indicating the log level as a string.

    Returns:
        A formatted and styled string representing the log record, ready to be printed or
        displayed. The string includes styling directives that are compatible with the Rich
        library.
    """
    color_map = {
        "TRACE": "turquoise2",
        "DEBUG": "cyan",
        "INFO": "bold",
        "SUCCESS": "bold green",
        "WARNING": "bold yellow",
        "ERROR": "bold red",
        "CRITICAL": "bold white on red",
    }
    line_start_map = {
        "INFO": "",
        "DEBUG": "🐞 ",
        "TRACE": "🔧 ",
        "WARNING": "⚠️ ",
        "SUCCESS": "✅ ",
        "ERROR": "❌ ",
        "CRITICAL": "💀 ",
        "EXCEPTION": "",
    }

    name = record["level"].name
    lvl_color = color_map.get(name, "cyan")
    line_start = line_start_map.get(name, f"{name: <8} | ")
    log_message = escape(record["message"])

    msg = f"[{lvl_color}]{line_start}{log_message}[/{lvl_color}]"
    debug = f"[#c5c5c5]({record['name']}:{record['function']}:{record['line']})[/#c5c5c5]"

    return f"{msg} {debug}" if name in {"DEBUG", "TRACE"} else msg


def instantiate_logger(
    verbosity: int, log_file: Path, log_to_file: bool
) -> None:  # pragma: no cover
    """Initialize and configure the logging system for the application.

    This function sets up the Loguru logger with a specified verbosity level and optionally
    directs the log output to both the console and a file. It supports different verbosity
    levels for controlling the amount of log information produced and can also capture logs
    from installed libraries when verbosity is high enough.

    Args:
        verbosity: Controls the amount of detail in the logs. Levels are as follows:
            - 0 for INFO and above,
            - 1 for DEBUG and above,
            - 2 for TRACE and above,
            - Greater than 2 includes DEBUG logs from installed libraries.
        log_file: The file path where logs should be written if `log_to_file` is True.
        log_to_file: A boolean indicating whether logs should also be saved to the file specified
                     by `log_file`.
    """
    level = verbosity if verbosity < 3 else 2  # noqa: PLR2004

    logger.remove()
    logger.add(
        console.print,
        level=LogLevel(level).name,
        colorize=True,
        format=log_formatter,  # type: ignore [arg-type]
    )
    if log_to_file:
        logger.add(
            log_file,
            level=LogLevel(level).name,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message} ({name})",
            rotation="50 MB",
            retention=2,
            compression="zip",
        )

    if verbosity > 2:  # noqa: PLR2004
        # Intercept standard sh logs and redirect to Loguru
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
