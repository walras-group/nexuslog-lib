import pathlib

import nexuslog as logging


def _read_file(path: str) -> str:
    # The writer appends a _YYYYMMDD suffix to the configured filename, so
    # read every log file next to the configured path.
    parent = pathlib.Path(path).parent
    return "".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in sorted(parent.glob("*.log"))
    )


def test_name_levels_override_default(tmp_path) -> None:
    path = tmp_path / "nexuslog_test.log"
    logging.basicConfig(
        filename=str(path),
        level=logging.INFO,
        name_levels={"special": logging.DEBUG},
        batch_size=1,
    )
    logger_special = logging.getLogger("special")
    logger_other = logging.getLogger("other")

    logger_special.debug("special-debug")
    logger_other.debug("other-debug")
    logger_special.shutdown()

    contents = _read_file(str(path))
    assert "special-debug" in contents
    assert "other-debug" not in contents


def test_explicit_level_overrides_name_levels(tmp_path) -> None:
    path = tmp_path / "nexuslog_test.log"
    logging.basicConfig(
        filename=str(path),
        level=logging.INFO,
        name_levels={"special": logging.DEBUG},
        batch_size=1,
    )
    logger = logging.getLogger("special", level=logging.WARNING)

    logger.debug("explicit-debug")
    logger.warning("explicit-warn")
    logger.shutdown()

    contents = _read_file(str(path))
    assert "explicit-warn" in contents
    assert "explicit-debug" not in contents
