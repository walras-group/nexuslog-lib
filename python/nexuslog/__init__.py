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
    """
    global _DEFAULT_LEVEL, _NAME_LEVELS, _root_logger
    _basic_config(filename, unix_ts, batch_size)
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


def trace(message: str) -> None:
    """Log a trace message to the root logger."""
    _get_root_logger().trace(message)


def debug(message: str) -> None:
    """Log a debug message to the root logger."""
    _get_root_logger().debug(message)


def info(message: str) -> None:
    """Log an info message to the root logger."""
    _get_root_logger().info(message)


def warning(message: str) -> None:
    """Log a warning message to the root logger."""
    _get_root_logger().warning(message)


def error(message: str) -> None:
    """Log an error message to the root logger."""
    _get_root_logger().error(message)


def shutdown() -> None:
    """Shutdown the root logger and flush remaining messages."""
    _get_root_logger().shutdown()
