"""Micro-benchmark isolating NexusLog library cost."""
import time, tempfile, os, statistics
import nexuslog as logging

N = 1_000_000


def run(unix_ts, preformatted):
    tmp = tempfile.mkdtemp()
    logging.basicConfig(os.path.join(tmp, "b"), level=logging.INFO, unix_ts=unix_ts)
    log = logging.getLogger("bench")
    if preformatted:
        msgs = [f"Benchmark message number {i}" for i in range(N)]
        start = time.perf_counter()
        for m in msgs:
            log.info(m)
        log.shutdown()
        return time.perf_counter() - start
    else:
        start = time.perf_counter()
        for i in range(N):
            log.info(f"Benchmark message number {i}")
        log.shutdown()
        return time.perf_counter() - start


def bench(label, **kw):
    times = [run(**kw) for _ in range(5)]
    best = min(times)
    print(f"{label:28} best={best:.4f}s  {N/best:>12,.0f} msg/s  (median {statistics.median(times):.4f})")
    return best


if __name__ == "__main__":
    print(f"N={N:,}")
    bench("formatted, preformatted", unix_ts=False, preformatted=True)
    bench("formatted, fstring-in-loop", unix_ts=False, preformatted=False)
    bench("unix_ts, preformatted", unix_ts=True, preformatted=True)
    bench("unix_ts, fstring-in-loop", unix_ts=True, preformatted=False)
