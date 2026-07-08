"""Benchmark comparing NexusLogger vs Python's built-in logging vs other loggers."""

import logging
import time
import tempfile
import os
import subprocess
import sys

# Number of log messages per benchmark
N_MESSAGES = 1_000_000

PLOT_PATH = os.path.join("assets", "bench.png")


def bench_picologging(log_file: str) -> float:
    """Benchmark picologging."""
    import picologging

    pico_logger = picologging.Logger("pico_bench", picologging.INFO)
    handler = picologging.FileHandler(log_file, mode="w")
    handler.setFormatter(
        picologging.Formatter("[%(asctime)s %(filename)s %(lineno)d %(levelname)s] %(message)s")
    )
    pico_logger.addHandler(handler)

    start = time.perf_counter()
    for i in range(N_MESSAGES):
        pico_logger.info("Benchmark message number %d", i)
    handler.flush()
    elapsed = time.perf_counter() - start

    handler.close()
    return elapsed


def bench_loguru(log_file: str) -> float:
    """Benchmark loguru."""
    from loguru import logger

    logger.remove()
    sink_id = logger.add(
        log_file,
        format="[{time} {file} {line} {level}] {message}",
        level="INFO",
    )

    start = time.perf_counter()
    for i in range(N_MESSAGES):
        logger.info("Benchmark message number {}", i)
    elapsed = time.perf_counter() - start

    logger.remove(sink_id)
    return elapsed


def bench_logxide(log_file: str) -> float:
    """Benchmark logxide."""
    # The Python 3.11 build auto-installs a root stream handler and ignores
    # file-based configuration, so run it in isolation and capture its output.
    code = f"""
import logxide

N_MESSAGES = {N_MESSAGES}
logger = logxide.getLogger("logxide_bench")
logger.setLevel(logxide.INFO)

for i in range(N_MESSAGES):
    logger.info("Benchmark message number %d", i)

logxide.flush()
"""
    start = time.perf_counter()
    with open(log_file, "w") as output:
        subprocess.run(
            [sys.executable, "-c", code],
            check=True,
            stdout=output,
            stderr=subprocess.DEVNULL,
        )
    elapsed = time.perf_counter() - start

    return elapsed


def bench_spdlog(log_file: str) -> float:
    """Benchmark spdlog."""
    import spdlog

    if hasattr(spdlog, "FileLogger"):
        spd_logger = spdlog.FileLogger("spdlog_bench", log_file, truncate=True)
    else:
        raise RuntimeError("spdlog.FileLogger is not available in this spdlog build")

    start = time.perf_counter()
    for i in range(N_MESSAGES):
        spd_logger.info(f"Benchmark message number {i}")
    if hasattr(spd_logger, "flush"):
        spd_logger.flush()
    elapsed = time.perf_counter() - start

    if hasattr(spdlog, "shutdown"):
        spdlog.shutdown()
    return elapsed


def plot_results(results: list[dict]) -> None:
    """Plot throughput and time to a PNG file for the README."""
    try:
        import matplotlib.pyplot as plt
    except ModuleNotFoundError as exc:
        raise RuntimeError("matplotlib is required to plot the benchmark chart") from exc

    labels = [row["name"] for row in results]
    throughput = [row["msgs_per_sec"] / 1_000_000 for row in results]
    time_s = [row["time"] for row in results]
    colors = [row["color"] for row in results]

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "axes.facecolor": "#F7F4EF",
            "figure.facecolor": "#F7F4EF",
            "axes.edgecolor": "#E2E8F0",
            "axes.labelcolor": "#1F2937",
            "xtick.color": "#6B7280",
            "ytick.color": "#1F2937",
        }
    )

    fig, axes = plt.subplots(2, 1, figsize=(10, 8), dpi=160)
    fig.suptitle(
        "Benchmark Throughput and Time",
        fontsize=18,
        fontweight="bold",
        color="#111827",
        y=0.98,
    )

    ax = axes[0]
    ax.barh(labels, throughput, color=colors, edgecolor="none")
    ax.invert_yaxis()
    ax.set_xlabel("Throughput (million msgs/sec)")
    ax.set_title("Higher is better")
    ax.grid(axis="x", color="#D7DEE6", alpha=0.7, linewidth=0.8)
    ax.set_axisbelow(True)
    for idx, value in enumerate(throughput):
        ax.text(
            value + 0.08,
            idx,
            f"{value:.3f}M",
            va="center",
            fontsize=9,
            color="#111827",
        )

    ax = axes[1]
    ax.barh(labels, time_s, color=colors, edgecolor="none")
    ax.invert_yaxis()
    ax.set_xlabel("Time (seconds)")
    ax.set_title("Lower is better")
    ax.grid(axis="x", color="#D7DEE6", alpha=0.7, linewidth=0.8)
    ax.set_axisbelow(True)
    for idx, value in enumerate(time_s):
        ax.text(
            value + 0.08,
            idx,
            f"{value:.3f}s",
            va="center",
            fontsize=9,
            color="#111827",
        )

    fig.text(
        0.5,
        0.02,
        "1,000,000 log messages per logger",
        ha="center",
        fontsize=9,
        color="#6B7280",
    )

    os.makedirs(os.path.dirname(PLOT_PATH), exist_ok=True)
    fig.tight_layout(rect=[0.06, 0.06, 0.98, 0.93])
    fig.savefig(PLOT_PATH, bbox_inches="tight")


def bench_python_logging(log_file: str) -> float:
    """Benchmark Python's built-in logging."""
    # Configure Python logging
    py_logger = logging.getLogger("python_bench")
    py_logger.setLevel(logging.INFO)
    py_logger.handlers.clear()
    handler = logging.FileHandler(log_file, mode="w")
    handler.setFormatter(
        logging.Formatter("[%(asctime)s %(filename)s %(lineno)d %(levelname)s] %(message)s")
    )
    py_logger.addHandler(handler)

    start = time.perf_counter()
    for i in range(N_MESSAGES):
        py_logger.info("Benchmark message number %d", i)
    handler.flush()
    elapsed = time.perf_counter() - start

    handler.close()
    py_logger.removeHandler(handler)
    return elapsed


def bench_rust_logger(log_file: str, unix_ts: bool) -> float:
    """Benchmark NexusLogger."""
    import nexuslog as logging

    logging.basicConfig(log_file, level=logging.Level.Info, unix_ts=unix_ts)
    log = logging.getLogger("bench")

    start = time.perf_counter()
    for i in range(N_MESSAGES):
        log.info(f"Benchmark message number {i}")
    log.shutdown()  # Ensures all messages are flushed
    elapsed = time.perf_counter() - start

    return elapsed


def _bench_rust(tmpdir: str, prefix: str, unix_ts: bool) -> tuple[float, int]:
    """Run a NexusLogger variant and return (time, total log size)."""
    log_prefix = os.path.join(tmpdir, prefix)
    elapsed = bench_rust_logger(log_prefix, unix_ts=unix_ts)
    files = [f for f in os.listdir(tmpdir) if f.startswith(prefix)]
    size = sum(os.path.getsize(os.path.join(tmpdir, f)) for f in files)
    return elapsed, size


def main():
    print(f"Benchmarking with {N_MESSAGES:,} log messages\n")
    print("-" * 60)

    # (display name, bench function, log filename, chart color). Competitors
    # that are not installed / fail to run are skipped with a note so the chart
    # can be regenerated in any environment.
    competitors = [
        ("Python logging", bench_python_logging, "python.log", "#8D99AE"),
        ("loguru", bench_loguru, "loguru.log", "#7B6D8D"),
        ("logxide", bench_logxide, "logxide.log", "#3A86FF"),
        ("picologging", bench_picologging, "pico.log", "#F45B69"),
        ("spdlog", bench_spdlog, "spdlog.log", "#FFB20F"),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        results = []
        for name, func, filename, color in competitors:
            try:
                log_file = os.path.join(tmpdir, filename)
                elapsed = func(log_file)
                size = os.path.getsize(log_file)
            except Exception as exc:  # noqa: BLE001 - skip missing/broken libs
                print(f"  (skipping {name}: {exc})")
                continue
            if size == 0:
                # The logger produced no output (e.g. no handler attached in
                # this environment); not a valid data point.
                print(f"  (skipping {name}: wrote an empty log)")
                continue
            results.append(
                {
                    "name": name,
                    "time": elapsed,
                    "msgs_per_sec": N_MESSAGES / elapsed,
                    "size": size,
                    "color": color,
                }
            )

        # NexusLogger variants (always run; the whole point of the chart).
        rust_time, rust_size = _bench_rust(tmpdir, "rust", unix_ts=False)
        rust_unix_time, rust_unix_size = _bench_rust(tmpdir, "rust_unix", unix_ts=True)
        results.append(
            {
                "name": "NexusLogger",
                "time": rust_time,
                "msgs_per_sec": N_MESSAGES / rust_time,
                "size": rust_size,
                "color": "#2D3047",
            }
        )
        results.append(
            {
                "name": "NexusLogger unix_ts",
                "time": rust_unix_time,
                "msgs_per_sec": N_MESSAGES / rust_unix_time,
                "size": rust_unix_size,
                "color": "#1B998B",
            }
        )

        # Fastest -> slowest; the chart inverts the y-axis so the fastest bar
        # lands on top, and the printed table iterates in reverse.
        results.sort(key=lambda row: row["time"])

        # Results
        print(f"{'Logger':<20} {'Time (s)':<12} {'Msgs/sec':<15} {'Log size':<12}")
        print("-" * 60)
        for row in reversed(results):
            print(
                f"{row['name']:<20} {row['time']:<12.3f} "
                f"{row['msgs_per_sec']:<15,.0f} {row['size']:,} bytes"
            )

        print("-" * 60)
        print()
        for row in results:
            if row["name"].startswith("NexusLogger"):
                continue
            print(
                f"NexusLogger is {row['time'] / rust_time:.2f}x faster than {row['name']}"
            )
        for row in results:
            if row["name"].startswith("NexusLogger"):
                continue
            print(
                f"NexusLogger unix_ts is {row['time'] / rust_unix_time:.2f}x "
                f"faster than {row['name']}"
            )

        plot_results(results)
        print(f"\nSaved benchmark chart to {PLOT_PATH}")


if __name__ == "__main__":
    main()
