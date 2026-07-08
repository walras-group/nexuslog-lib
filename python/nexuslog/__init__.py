"""Fast async logger with Rust backend.

Usage:
    import nexuslog as logging
    logging.basicConfig(level=logging.INFO)
    logging.info("Hello, world!")
"""

from ._logger import (
    PyLevel as Level,
    PyLogger as Logger,
    get_logger as _get_logger,
    basic_config as _basic_config,
)

# Standard logging level constants
TRACE = Level.Trace
DEBUG = Level.Debug
INFO = Level.Info
WARNING = Level.Warn
ERROR = Level.Error

__all__ = [
    "Level",
    "Logger",
    "basicConfig",
    "getLogger",
    "TRACE",
    "DEBUG",
    "INFO",
    "WARNING",
    "ERROR",
    "trace",
    "debug",
    "info",
    "warning",
    "error",
    "shutdown",
]

_DEFAULT_LEVEL = INFO
_NAME_LEVELS: dict[str | None, Level] = {}
_root_logger: "Logger | None" = None


def basicConfig(
    filename: str | None = None,
    level: Level = INFO,
    name_levels: dict[str | None, Level] | None = None,
    unix_ts: bool = False,
    batch_size: int | None = None,
    color: str = "auto",
) -> None:
    """Configure the root logger.

    Args:
        filename: Optional file path for log output. If None, logs to stdout.
        level: Minimum log level to record. Default is INFO.
        name_levels: Optional per-name level overrides. Exact match only.
                     When provided, it takes priority over the global level.
        unix_ts: If True, emit unix timestamps instead of formatted local time.
        batch_size: Number of log entries to batch before writing. Default is 32.
                    Set to 1 to write immediately (lower performance, no data loss on crash).
        color: Colorize output. One of:
               "auto" (default) — color only when writing to a color-capable
               terminal (honoring the NO_COLOR / FORCE_COLOR env vars); files
               and pipes stay plain.
               "off" — never emit ANSI color.
               "always" — always emit ANSI color, even to files and pipes.
    """
    if color not in ("auto", "off", "always"):
        raise ValueError(
            f"color must be 'auto', 'off', or 'always', got {color!r}"
        )
    global _DEFAULT_LEVEL, _NAME_LEVELS, _root_logger
    _basic_config(filename, unix_ts, batch_size, color)
    _DEFAULT_LEVEL = level
    _NAME_LEVELS = {} if name_levels is None else dict(name_levels)
    # Create root logger
    _root_logger = getLogger(None, level)


# `Logger` is the Rust-backed logger itself (imported above as an alias for
# PyLogger). Calls go straight into Rust with no intermediate Python frame.
# It exposes trace/debug/info/warn/warning/error/shutdown.


def getLogger(name: str | None = None, level: Level | None = None) -> Logger:
    """Get a logger that shares a writer with the default path."""
    if level is None:
        if name in _NAME_LEVELS:
            level = _NAME_LEVELS[name]
        else:
            level = _DEFAULT_LEVEL
    return _get_logger(name, level)


def _get_root_logger() -> Logger:
    """Get or create the root logger."""
    global _root_logger
    if _root_logger is None:
        _root_logger = getLogger(None, _DEFAULT_LEVEL)
    return _root_logger


def trace(message: str, *args: object) -> None:
    """Log a trace message to the root logger.

    Extra args are merged into message lazily using %-style formatting.
    """
    _get_root_logger().trace(message, *args)


def debug(message: str, *args: object) -> None:
    """Log a debug message to the root logger.

    Extra args are merged into message lazily using %-style formatting.
    """
    _get_root_logger().debug(message, *args)


def info(message: str, *args: object) -> None:
    """Log an info message to the root logger.

    Extra args are merged into message lazily using %-style formatting.
    """
    _get_root_logger().info(message, *args)


def warning(message: str, *args: object) -> None:
    """Log a warning message to the root logger.

    Extra args are merged into message lazily using %-style formatting.
    """
    _get_root_logger().warning(message, *args)


def error(message: str, *args: object) -> None:
    """Log an error message to the root logger.

    Extra args are merged into message lazily using %-style formatting.
    """
    _get_root_logger().error(message, *args)


def shutdown() -> None:
    """Shutdown the root logger and flush remaining messages."""
    _get_root_logger().shutdown()
