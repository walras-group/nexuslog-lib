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


def _read_json_lines(path: str) -> list:
    import json

    return [
        json.loads(line)
        for line in _read_file(path).splitlines()
        if line.strip()
    ]


def test_json_format_emits_valid_ndjson(tmp_path) -> None:
    path = tmp_path / "nexuslog_json.log"
    logging.basicConfig(filename=str(path), format="json", batch_size=1)
    logging.getLogger("svc").warning("disk %d%% full", 90)
    logging.getLogger("svc").shutdown()

    objs = _read_json_lines(str(path))
    assert len(objs) == 1
    obj = objs[0]
    assert obj["level"] == "warn"
    assert obj["name"] == "svc"
    assert obj["msg"] == "disk 90% full"
    # `time` is an ISO-8601 string parseable by datetime.
    import datetime

    datetime.datetime.fromisoformat(obj["time"])


def test_json_format_escapes_special_chars(tmp_path) -> None:
    path = tmp_path / "nexuslog_json_esc.log"
    logging.basicConfig(filename=str(path), format="json", batch_size=1)
    msg = 'quote " backslash \\ newline \n tab \t end'
    logging.getLogger("esc").error(msg)
    logging.getLogger("esc").shutdown()

    objs = _read_json_lines(str(path))
    assert len(objs) == 1
    # Round-trips back to the exact original message.
    assert objs[0]["msg"] == msg


def test_json_format_unix_ts_is_numeric(tmp_path) -> None:
    path = tmp_path / "nexuslog_json_unix.log"
    logging.basicConfig(filename=str(path), format="json", unix_ts=True, batch_size=1)
    logging.getLogger("u").info("hi")
    logging.getLogger("u").shutdown()

    objs = _read_json_lines(str(path))
    assert len(objs) == 1
    assert isinstance(objs[0]["time"], (int, float))


def test_json_format_omits_name_when_none(tmp_path) -> None:
    path = tmp_path / "nexuslog_json_noname.log"
    logging.basicConfig(filename=str(path), format="json", batch_size=1)
    root = logging.getLogger(None)
    root.info("no-name")
    root.shutdown()

    objs = _read_json_lines(str(path))
    assert len(objs) == 1
    assert "name" not in objs[0]
    assert objs[0]["msg"] == "no-name"


def test_format_invalid_value_raises() -> None:
    import pytest

    with pytest.raises(ValueError):
        logging.basicConfig(format="xml")
