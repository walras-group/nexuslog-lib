"""Benchmark lazy argument formatting across logging libraries.

Each library is measured on the same 3-argument trading message in two
variants:

- enabled:  INFO call on an INFO logger -> pays formatting + write
- disabled: DEBUG call on an INFO logger -> the cost of a call that produces
  no output; with lazy formatting the args are never converted

Formatting style per library: %-style args for stdlib logging, picologging,
logxide, and NexusLog; {}-style args for loguru. py-spdlog's bindings accept
only a pre-built string, so it pays an f-string at the call site in both
variants (no lazy support).
"""

import os
import subprocess
import sys
import tempfile
import time

N = 1_000_000


def bench_stdlib(log_file: str, enabled: bool) -> float:
    import logging

    logger = logging.getLogger(f"stdlib_lazy_{enabled}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    handler = logging.FileHandler(log_file, mode="w")
    handler.setFormatter(logging.Formatter("[%(asctime)s %(levelname)s] %(message)s"))
    logger.addHandler(handler)
    call = logger.info if enabled else logger.debug

    start = time.perf_counter()
    for i in range(N):
        call("price=%s qty=%s side=%s", 1.5, i, "buy")
    handler.flush()
    elapsed = time.perf_counter() - start

    handler.close()
    logger.removeHandler(handler)
    return elapsed


def bench_picologging(log_file: str, enabled: bool) -> float:
    import picologging

    logger = picologging.Logger(f"pico_lazy_{enabled}", picologging.INFO)
    handler = picologging.FileHandler(log_file, mode="w")
    handler.setFormatter(picologging.Formatter("[%(asctime)s %(levelname)s] %(message)s"))
    logger.addHandler(handler)
    call = logger.info if enabled else logger.debug

    start = time.perf_counter()
    for i in range(N):
        call("price=%s qty=%s side=%s", 1.5, i, "buy")
    handler.flush()
    elapsed = time.perf_counter() - start

    handler.close()
    return elapsed


def bench_loguru(log_file: str, enabled: bool) -> float:
    from loguru import logger

    logger.remove()
    sink_id = logger.add(
        log_file, format="[{time} {level}] {message}", level="INFO"
    )
    call = logger.info if enabled else logger.debug

    start = time.perf_counter()
    for i in range(N):
        call("price={} qty={} side={}", 1.5, i, "buy")
    elapsed = time.perf_counter() - start

    logger.remove(sink_id)
    return elapsed


def bench_logxide(log_file: str, enabled: bool) -> float:
    # Same isolation as bench_python.py: the Python 3.11 build logs to a root
    # stream handler, so run in a subprocess, log to stdout, and report the
    # in-process elapsed time on stderr (excludes interpreter startup).
    code = f"""
import sys, time
import logxide

logger = logxide.getLogger("logxide_lazy")
logger.setLevel(logxide.INFO)
call = logger.info if {enabled} else logger.debug

start = time.perf_counter()
for i in range({N}):
    call("price=%s qty=%s side=%s", 1.5, i, "buy")
logxide.flush()
print(time.perf_counter() - start, file=sys.stderr)
"""
    with open(log_file, "w") as output:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            stdout=output,
            stderr=subprocess.PIPE,
            text=True,
        )
    return float(proc.stderr.strip().splitlines()[-1])


def bench_spdlog(log_file: str, enabled: bool) -> float:
    import spdlog

    spd = spdlog.FileLogger(f"spdlog_lazy_{enabled}", log_file, truncate=True)
    call = spd.info if enabled else spd.debug  # default level is info

    start = time.perf_counter()
    for i in range(N):
        call(f"price={1.5} qty={i} side={'buy'}")
    if hasattr(spd, "flush"):
        spd.flush()
    elapsed = time.perf_counter() - start

    if hasattr(spdlog, "shutdown"):
        spdlog.shutdown()
    return elapsed


def bench_nexuslog(log_file: str, enabled: bool) -> float:
    import nexuslog

    nexuslog.basicConfig(log_file, level=nexuslog.INFO)
    log = nexuslog.getLogger("bench")
    call = log.info if enabled else log.debug

    start = time.perf_counter()
    for i in range(N):
        call("price=%s qty=%s side=%s", 1.5, i, "buy")
    log.shutdown()
    return time.perf_counter() - start


def main() -> None:
    print(f"Lazy-format benchmark, {N:,} calls per variant")
    print('message: "price=%s qty=%s side=%s" % (1.5, i, "buy")\n')

    libraries = [
        ("Python logging", bench_stdlib, "%-args"),
        ("picologging", bench_picologging, "%-args"),
        ("loguru", bench_loguru, "{}-args"),
        ("logxide", bench_logxide, "%-args"),
        ("spdlog", bench_spdlog, "f-string (no lazy)"),
        ("NexusLog", bench_nexuslog, "%-args"),
    ]

    rows = []
    with tempfile.TemporaryDirectory() as tmpdir:
        for name, func, style in libraries:
            row = {"name": name, "style": style}
            for variant in ("enabled", "disabled"):
                log_file = os.path.join(tmpdir, f"{name}_{variant}.log")
                try:
                    row[variant] = func(log_file, variant == "enabled")
                except Exception as exc:  # noqa: BLE001 - skip missing/broken libs
                    print(f"  (skipping {name} {variant}: {exc})")
                    row[variant] = None
            rows.append(row)

    header = (
        f"{'Library':<16} {'style':<19} "
        f"{'enabled (s)':>12} {'msg/s':>12} {'ns/call':>8}   "
        f"{'disabled (s)':>12} {'msg/s':>12} {'ns/call':>8}"
    )
    print(header)
    print("-" * len(header))
    for row in sorted(rows, key=lambda r: r["enabled"] or 1e9):
        cells = [f"{row['name']:<16} {row['style']:<19}"]
        for variant in ("enabled", "disabled"):
            t = row[variant]
            if t is None:
                cells.append(f"{'-':>12} {'-':>12} {'-':>8}")
            else:
                cells.append(f"{t:>12.3f} {N / t:>12,.0f} {t / N * 1e9:>8.1f}")
        print("   ".join(cells))

    nexus = next(r for r in rows if r["name"] == "NexusLog")
    print()
    for row in rows:
        if row is nexus:
            continue
        parts = []
        for variant in ("enabled", "disabled"):
            if row[variant] and nexus[variant]:
                parts.append(f"{variant}: {row[variant] / nexus[variant]:.2f}x")
        if parts:
            print(f"NexusLog vs {row['name']:<16} {'  '.join(parts)}")


if __name__ == "__main__":
    main()
