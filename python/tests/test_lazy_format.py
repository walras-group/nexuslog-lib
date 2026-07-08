"""Tests for lazy %-style formatting: logger.info("x=%s", x)."""

import pytest

import nexuslog as logging


def _read_logs(tmp_path) -> str:
    return "".join(p.read_text(encoding="utf-8") for p in sorted(tmp_path.glob("*.log")))


def _payloads(tmp_path) -> list[str]:
    """Extract the msg="..." payload of each logged line, in order."""
    out = []
    for line in _read_logs(tmp_path).splitlines():
        start = line.index(' msg="') + len(' msg="')
        assert line.endswith('"')
        out.append(line[start:-1])
    return out


def _make_logger(tmp_path, level=logging.INFO):
    logging.basicConfig(
        filename=str(tmp_path / "lazy.log"), level=level, batch_size=1
    )
    return logging.getLogger("lazy", level=level)


class Counting:
    def __init__(self) -> None:
        self.calls = 0

    def __str__(self) -> str:
        self.calls += 1
        return "counted"


class Poison:
    def __init__(self) -> None:
        self.calls = 0

    def __str__(self) -> str:
        self.calls += 1
        raise RuntimeError("must not be formatted")

    __repr__ = __str__


def test_fast_path_placeholders(tmp_path) -> None:
    log = _make_logger(tmp_path)
    log.info("price=%s qty=%s side=%s", 1.5, 2, "buy")
    log.info("repr=%r", "buy")
    log.info("d=%d i=%i neg=%d bool=%d", 7, 8, -9, True)
    log.info("x=%x o=%o negx=%x nego=%o", 255, 8, -255, -8)
    log.info("f=%f", 3.5)
    log.shutdown()

    payloads = _payloads(tmp_path)
    assert payloads == [
        "price=1.5 qty=2 side=buy",
        "repr='buy'",
        "d=7 i=8 neg=-9 bool=1",
        "x=ff o=10 negx=-ff nego=-10",
        "f=3.500000",
    ]


def test_percent_escape(tmp_path) -> None:
    log = _make_logger(tmp_path)
    log.info("%d%% done", 50)
    log.info("100%% sure", *())  # no placeholders but explicit empty args
    log.shutdown()
    assert _payloads(tmp_path) == ["50% done", "100%% sure"]


def test_no_args_verbatim(tmp_path) -> None:
    log = _make_logger(tmp_path)
    log.info("100% done")
    log.info("%s raw %d")
    log.shutdown()
    assert _payloads(tmp_path) == ["100% done", "%s raw %d"]


def test_disabled_level_never_formats(tmp_path) -> None:
    log = _make_logger(tmp_path, level=logging.INFO)
    poison = Poison()
    log.debug("%s", poison)  # must not raise, must not call __str__
    log.trace("%r", poison)
    log.shutdown()
    assert poison.calls == 0
    assert _payloads(tmp_path) == []


def test_enabled_level_formats_once(tmp_path) -> None:
    log = _make_logger(tmp_path)
    counting = Counting()
    log.info("%s", counting)
    log.shutdown()
    assert counting.calls == 1
    assert _payloads(tmp_path) == ["counted"]


def test_fallback_specs(tmp_path) -> None:
    log = _make_logger(tmp_path)
    log.info("%.2f", 3.14159)
    log.info("%08d", 42)
    log.info("%e", 12345.678)
    log.info("%g", 0.0001)
    log.info("%d", 10**30)
    log.info("%d", 3.7)
    log.info("%f", float("inf"))
    log.info("%f", 3)
    log.info("%X", 255)
    log.shutdown()

    assert _payloads(tmp_path) == [
        "3.14",
        "00000042",
        "1.234568e+04",
        "0.0001",
        "1000000000000000000000000000000",
        "3",
        "inf",
        "3.000000",
        "FF",
    ]


def test_mapping_quirk(tmp_path) -> None:
    log = _make_logger(tmp_path)
    log.info("%(a)s and %(b)d", {"a": "x", "b": 2})
    log.info("%s", {"a": 1})
    log.info("%s", {})
    log.shutdown()
    assert _payloads(tmp_path) == ["x and 2", "{'a': 1}", "{}"]


def test_format_errors_raise(tmp_path) -> None:
    log = _make_logger(tmp_path)
    with pytest.raises(TypeError, match="not enough arguments"):
        log.info("%s %s", 1)
    with pytest.raises(TypeError, match="not all arguments converted"):
        log.info("%s", 1, 2)
    with pytest.raises(ValueError):
        log.info("%y", 1)
    with pytest.raises(ValueError):
        log.info("100%", 1)
    with pytest.raises(RuntimeError, match="must not be formatted"):
        log.info("%s", Poison())
    log.shutdown()


def test_failed_line_does_not_corrupt_output(tmp_path) -> None:
    log = _make_logger(tmp_path)
    log.info("good %d", 1)
    with pytest.raises(TypeError):
        log.info("bad %s %s", 1)
    with pytest.raises(RuntimeError):
        log.info("bad %s then %s", 1, Poison())
    log.info("good %d", 2)
    log.shutdown()
    assert _payloads(tmp_path) == ["good 1", "good 2"]


def test_module_level_functions_forward_args(tmp_path) -> None:
    logging.basicConfig(
        filename=str(tmp_path / "lazy.log"), level=logging.INFO, batch_size=1
    )
    logging.info("root %s=%d", "n", 3)
    logging.warning("warn %r", [1])
    logging.shutdown()
    payloads = _payloads(tmp_path)
    assert "root n=3" in payloads
    assert "warn [1]" in payloads


EQUIVALENCE_CASES = [
    ("plain no percent", ()),
    ("s=%s", ("text",)),
    ("s=%s", (7,)),
    ("s=%s", (1.5,)),
    ("s=%s", (0.1,)),
    ("s=%s", (2.0,)),
    ("s=%s", (-0.0,)),
    ("s=%s", (0.0,)),
    ("s=%s", (1e15,)),
    ("s=%s", (9999999999999998.0,)),
    ("s=%s", (1e16,)),  # scientific notation -> PyObject_Str
    ("s=%s", (1e-4,)),
    ("s=%s", (1e-5,)),  # scientific notation -> PyObject_Str
    ("s=%s", (-2.675,)),
    ("s=%s", (float("nan"),)),
    ("s=%s", (float("-inf"),)),
    ("s=%s", (3.141592653589793,)),
    ("r=%r", (1.5,)),
    ("r=%r", (2.0,)),
    ("r=%r", (1e16,)),
    ("r=%r", (-0.0,)),
    ("s=%s", (None,)),
    ("s=%s", ([1, "a"],)),
    ("r=%r", ("text",)),
    ("r=%r", (None,)),
    ("r=%r", (42,)),
    ("r=%r", (-7,)),
    ("r=%r", (True,)),
    ("r=%r", (2**64,)),
    ("s=%s", (True,)),
    ("s=%s", (False,)),
    ("s=%s", (-12345,)),
    ("s=%s", (2**64,)),
    ("s=%s", (-(2**63),)),
    ("d=%d", (0,)),
    ("d=%d", (-12345,)),
    ("d=%d", (True,)),
    ("d=%d", (False,)),
    ("i=%i", (99,)),
    ("d=%d", (2**63 - 1,)),
    ("d=%d", (-(2**63),)),
    ("d=%d", (2**63,)),  # beyond i64 -> fallback
    ("d=%d", (-(2**63) - 1,)),
    ("x=%x", (0,)),
    ("x=%x", (48879,)),
    ("x=%x", (-1,)),
    ("o=%o", (511,)),
    ("o=%o", (-511,)),
    ("f=%f", (0.0,)),
    ("f=%f", (-0.0,)),
    ("f=%f", (1e-7,)),
    ("f=%f", (1e21,)),
    ("f=%f", (2.675,)),
    ("f=%f", (float("nan"),)),
    ("%s %d %x %o %f %r", ("a", 1, 2, 3, 4.5, b"b")),
    ("pct=100%%", ()),
    ("%d%%%s", (5, "x")),
    ("prec=%.3f", (2.0 / 3.0,)),
    ("width=%6d", (42,)),
    ("e=%e", (0.000123,)),
    ("g=%g", (123456789.0,)),
    ("unicode 日志=%s", ("值",)),
]


def test_equivalence_with_python_percent(tmp_path) -> None:
    log = _make_logger(tmp_path)
    expected = []
    for fmt, args in EQUIVALENCE_CASES:
        log.info(fmt, *args)
        expected.append(fmt % args if args else fmt)
    log.shutdown()
    assert _payloads(tmp_path) == expected
