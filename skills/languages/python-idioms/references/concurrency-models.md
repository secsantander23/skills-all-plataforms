# Concurrency Models in Depth

## What the GIL actually restricts

CPython serialises execution of Python bytecode across threads within one process. The lock
is released when a thread blocks in C code that releases it, which covers most file I/O and
socket waits. NumPy and other compiled numeric libraries release the GIL around their inner
loops, so a threaded BLAS-backed workload scales in a way a pure-Python loop does not.

The GIL is an implementation property of CPython, not a language guarantee. Other
implementations (including the free-threaded build) change this. Do not build an
architecture assumption on "the GIL makes threads fine" without measuring, and check the
current CPython documentation for the state of free-threaded builds before relying on
either model.

## Decision procedure

```text
Is the work blocking IO that a library exposes asynchronously?
    yes -> asyncio
Is the work blocking IO through a library with no async API?
    yes -> threading / ThreadPoolExecutor
Is the work pure-Python CPU?
    yes -> multiprocessing / ProcessPoolExecutor
Is the work numeric and dominated by compiled kernels?
    yes -> threading (the GIL is released inside the kernels)
Is it a mix?
    yes -> asyncio for IO, offload CPU chunks to a process pool
```

## asyncio mechanics

```python
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def fetch(session, url: str, semaphore: asyncio.Semaphore) -> bytes:
    async with semaphore:                       # bounded concurrency
        async with session.get(url) as response:
            response.raise_for_status()
            return await response.read()


async def main(urls: list[str]) -> list[bytes]:
    import aiohttp  # or any async HTTP client

    semaphore = asyncio.Semaphore(10)
    async with aiohttp.ClientSession() as session:
        tasks = [fetch(session, url, semaphore) for url in urls]
        # return_exceptions=True -> collect outcomes instead of raising on the first error
        results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in results if isinstance(r, bytes)]
```

Rules that prevent the classic async bugs:

- `asyncio.run(main())` once per process, at the entry point. Not inside library code.
- A coroutine only runs when awaited or scheduled. A forgotten `await` on a coroutine
  produces "coroutine was never awaited" in the logs and does nothing.
- `asyncio.create_task` returns a `Task`; keep a reference or the task may be garbage
  collected before it finishes.
- Structured concurrency helpers (task groups) exist in newer Python. Check availability
  for your minimum version before using them.
- Shutdown: a `finally` that awaits cleanup, and handlers installed with
  `loop.add_signal_handler` on POSIX or `signal.signal` elsewhere.

## Timeouts

```python
import asyncio

try:
    result = await asyncio.wait_for(work(), timeout=5.0)
except TimeoutError:
    logger.warning("work timed out")
```

`asyncio.timeout` is the modern context-manager form; check availability for your minimum
version. Both wrap the operation so that cancellation is delivered at the next await point.
A synchronous blocking call inside the timeout window is not interrupted.

## Thread pools

```python
from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, as_completed


def run_all(tasks: list[Callable[[], None]]) -> dict[str, Exception | None]:
    results: dict[str, Exception | None] = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures: dict[Future[None], str] = {
            pool.submit(task): name for name, task in enumerate(tasks)  # type: ignore[misc]
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                future.result()
                results[name] = None
            except Exception as err:
                results[name] = err
    return results
```

Notes:

- `with ThreadPoolExecutor(...)` waits for submitted work on exit. Prefer it over manual
  `shutdown`.
- `as_completed` gives completion order; iterate the original list and call `.result()`
  if you need submission order.
- A thread cannot be killed. Long-running tasks must check a cancellation flag themselves.
- Threads share memory, so shared mutable state needs a lock. If you are adding many
  locks, the design is usually wrong: partition the data or use a queue.

```python
import threading

lock = threading.Lock()
with lock:
    shared.append(item)

rlock = threading.RLock()      # reentrant, for functions that call each other
with rlock:
    helper()                    # helper also takes rlock

counter_lock = threading.Lock()
def safe_increment(counter: list[int]) -> None:
    with counter_lock:
        counter[0] += 1         # not a plain `counter[0] += 1` outside the lock
```

A `queue.Queue` is usually better than a hand-rolled lock for producer/consumer work: it
provides blocking get with a timeout, and it removes the need to hand-roll condition
variables.

## Process pools

```python
from concurrent.futures import ProcessPoolExecutor


def process_chunk(chunk: list[int]) -> list[int]:
    return [x * 2 for x in chunk]


with ProcessPoolExecutor(max_workers=4) as pool:
    doubled = list(pool.map(process_chunk, chunks))
```

Hard constraints you must design around:

- **Pickling.** Arguments and return values cross a process boundary through `pickle`.
  Functions must be importable at module level (not closures or lambdas), and objects
  must be picklable. Open file handles, sockets, and database connections are not.
- **Startup cost.** A process pool is expensive to create. Do not create one per request.
- **Windows and `spawn`.** The default start method on some platforms spawns fresh
  interpreters, so everything in `main` must be guarded by
  `if __name__ == "__main__":`. This is good practice everywhere, including Linux.
- **Data volume.** Sending large results costs. Have workers write to disk or a database
  and return references, not payloads.

## Debugging concurrency

- `faulthandler` dumps tracebacks of all threads when a fault occurs or on a timer:
  `python -X faulthandler app.py`, `faulthandler.dump_traceback_later(30, exit=True)`.
- `asyncio` slow-callback warnings appear when a coroutine blocks the loop; treat them as
  failures in tests.
- Deterministic testing: inject a `Clock` interface rather than calling `time.sleep`, so
  tests can control time. Likewise inject the scheduler for retry/backoff tests.
- Reproduce with a stress loop rather than once: run the test many times, or with many
  more workers than cores, to expose races.

## Realistic failure modes

- **Thread-safe but not atomic.** `x += 1` is read, add, write. Two threads can interleave
  and lose an update. Hold the lock across all three.
- **Deadlock from inconsistent lock ordering.** Two threads locking A then B and B then A.
  Define a global lock order, or use one lock.
- **A `deque` used as an unbounded queue in a producer/consumer design** grows without
  limit and exhausts memory under load. Use `queue.Queue(maxsize=N)`.
- **GIL assumption failure.** Pure-Python CPU work in threads does not scale; it also adds
  context-switch overhead that makes it slower than sequential execution.
- **`asyncio.sleep(0)` used as a spin.** It yields to the loop without advancing real
  time, so a poll loop using it saturates a core. Use a real interval, or an event.

## Verify before relying on this

Executor defaults, free-threaded CPython behaviour, task-group availability, and
`asyncio.timeout` all depend on the Python version. Check the current documentation for
your minimum supported version:

- https://docs.python.org/3/library/asyncio.html
- https://docs.python.org/3/library/concurrent.futures.html
- https://docs.python.org/3/library/threading.html
- https://docs.python.org/3/library/multiprocessing.html