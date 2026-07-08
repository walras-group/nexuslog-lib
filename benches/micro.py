"""Micro-benchmark isolating NexusLog library cost."""
import time, tempfile, os, statistics
import nexuslog as logging

N = 1_000_000


def run(unix_ts, mode):
    tmp = tempfile.mkdtemp()
    logging.basicConfig(os.path.join(tmp, "b"), level=logging.INFO, unix_ts=unix_ts)
    log = logging.getLogger("bench")
    if mode == "preformatted":
        msgs = [f"Benchmark message number {i}" for i in range(N)]
        start = time.perf_counter()
        for m in msgs:
            log.info(m)
    elif mode == "fstring":
        start = time.perf_counter()
        for i in range(N):
            log.info(f"Benchmark message number {i}")
    elif mode == "lazy-int":
        start = time.perf_counter()
        for i in range(N):
            log.info("Benchmark message number %d", i)
    elif mode == "fstring-3arg":
        start = time.perf_counter()
        for i in range(N):
            log.info(f"price={1.5} qty={i} side={'buy'}")
    elif mode == "lazy-3arg":
        start = time.perf_counter()
        for i in range(N):
            log.info("price=%s qty=%s side=%s", 1.5, i, "buy")
    elif mode == "fstring-disabled":
        start = time.perf_counter()
        for i in range(N):
            log.debug(f"Benchmark message number {i}")
    elif mode == "lazy-disabled":
        start = time.perf_counter()
        for i in range(N):
            log.debug("Benchmark message number %d", i)
    else:
        raise ValueError(mode)
    log.shutdown()
    return time.perf_counter() - start


def bench(label, **kw):
    times = [run(**kw) for _ in range(5)]
    best = min(times)
    print(f"{label:28} best={best:.4f}s  {N/best:>12,.0f} msg/s  (median {statistics.median(times):.4f})")
    return best


if __name__ == "__main__":
    print(f"N={N:,}")
    bench("formatted, preformatted", unix_ts=False, mode="preformatted")
    bench("formatted, fstring-in-loop", unix_ts=False, mode="fstring")
    bench("formatted, lazy %d args", unix_ts=False, mode="lazy-int")
    bench("formatted, fstring 3-arg", unix_ts=False, mode="fstring-3arg")
    bench("formatted, lazy 3x%s args", unix_ts=False, mode="lazy-3arg")
    bench("disabled, fstring", unix_ts=False, mode="fstring-disabled")
    bench("disabled, lazy args", unix_ts=False, mode="lazy-disabled")
    bench("unix_ts, preformatted", unix_ts=True, mode="preformatted")
    bench("unix_ts, fstring-in-loop", unix_ts=True, mode="fstring")
