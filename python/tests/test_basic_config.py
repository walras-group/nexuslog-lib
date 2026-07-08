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


def test_color_always_emits_ansi_to_file(tmp_path) -> None:
    # `color='always'` colorizes even a file destination, so we can exercise
    # the full colored render path end-to-end without a real TTY.
    path = tmp_path / "nexuslog_color.log"
    logging.basicConfig(filename=str(path), color="always", batch_size=1)
    logger = logging.getLogger("colored")

    logger.warning("disk %d%% full", 90)
    logger.shutdown()

    contents = _read_file(str(path))
    assert "\x1b[33mwarn\x1b[0m" in contents  # level value in yellow
    assert "\x1b[2mtime=\x1b[0m" in contents  # dimmed key
    assert "\x1b[2mmsg=\x1b[0m" in contents
    assert "disk 90% full" in contents  # body itself stays plain


def test_color_off_and_auto_file_have_no_ansi(tmp_path) -> None:
    # Default `auto` writing to a file, and explicit `off`, must never emit ANSI.
    for mode in ("off", "auto"):
        path = tmp_path / f"nexuslog_{mode}.log"
        logging.basicConfig(filename=str(path), color=mode, batch_size=1)
        logger = logging.getLogger(f"plain-{mode}")
        logger.warning("plain-line")
        logger.shutdown()

        contents = _read_file(str(path))
        assert "plain-line" in contents
        assert "\x1b[" not in contents


def test_color_invalid_value_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        logging.basicConfig(color="rainbow")
