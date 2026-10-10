# Flame Graphs and Linux perf

## Collection

```bash
# Record a running process for 30 seconds of wall-clock time.
sudo perf record -F 99 -g -p "$(pgrep -f 'node server.js')" -- sleep 30

# Record a command from start to exit.
sudo perf record -F 99 -g -- node server.js

# Sample every CPU across the system.
sudo perf record -a -g -- sleep 30

# Live view while you reproduce.
sudo perf top -p "$(pgrep -f 'node server.js')"

# Hardware and software counters, useful before blaming the CPU at all.
perf stat -p "$(pgrep -f 'node server.js')" -- sleep 10
perf list
```

| Flag | Meaning |
| --- | --- |
| `-F N` | Sample frequency in Hz. `-F 99` avoids lockstep with many timers. |
| `-g` | Capture call graphs (frame pointers). Required for flame graphs. |
| `-p PID` | Attach to a running process |
| `-a` | System-wide |
| `-c N` | Count events instead of sampling |
| `-- sleep N` | Record for N seconds rather than until the process exits |
| `-o FILE` | Output file (default `perf.data`) |

Frame pointers are frequently omitted from release builds of native binaries (`-fomit-frame-pointer`).
Without them, stacks are truncated and the flame graph turns into a set of disconnected
islands. Check with `perf report` output: if almost every stack is one or two frames deep,
the unwinder is failing.

## Post-processing

```bash
perf script > perf.script
perf report --stdio --sort comm,dso,symbol > perf.report.txt

git clone https://github.com/brendangregg/FlameGraph.git
perl FlameGraph/stackcollapse-perf.pl perf.script > perf.folded
perl FlameGraph/flamegraph.pl --title "server.js baseline" perf.folded > flame.svg
```

Useful `flamegraph.pl` options:

| Option | Purpose |
| --- | --- |
| `--title TEXT` | Label the graph |
| `--subtitle TEXT` | Sub-label |
| `--countname NAME` | Label for the value axis (default `samples`) |
| `--colors NAME` | Palette, e.g. `hot`, `mem`, `io` |
| `--minwidth VALUE` | Merge sub-pixel-width frames |
| `--negate` | Invert the y axis |
| `--hash` | Hash stack names to reduce SVG size |

## Reading patterns

- **One wide plateau.** A single function holds most of the width. Go read it.
- **A wide shelf near the bottom.** Shared library or framework code dominating. Often
  indicates the wrong layer is being measured.
- **A thin spike on the right edge.** Something that only happens at the end: teardown,
  flush, serialization.
- **Repeated identical stripes.** A function called in a loop from many places.
- **Deep narrow tower.** Recursion. Check whether it is intentional.
- **Broad inverted-V.** Cost spread across a call subtree; the flat top is the actual
  expensive leaf shared by several callers.

## Differential profiling

The most useful thing a profiler does is tell you what *changed*. Collect one profile
before the change and one after, fold both, and diff the folded files. Frames present in
the second and absent in the first are the regression.

```bash
perl FlameGraph/flamegraph.pl perf.folded > before.svg
perl FlameGraph/flamegraph.pl perf_after.folded > after.svg

# Firefox Profiler and Speedscope can diff two profiles interactively:
#   https://profiler.firefox.com/  and  https://speedscope.app/
```

For a noisy metric, repeat the whole collection three times and treat only deltas larger
than the run-to-run spread as real.

## Heap as a flame graph

Brendan Gregg's FlameGraph tool also renders allocation sites and call counts:

```bash
perf mem record -- ./your-program          # if perf mem is supported on your kernel/CPU
perf mem report -i perf.data.mem -o mem.svg
```

Elsewhere the same "wide equals expensive" reading applies: a wide band is a code path
allocating a lot. Fixing it usually means reusing a buffer or removing an intermediate.

## Always-on profiling

Intermittent regressions are hard to catch with manual runs. Options, roughly in order of
setup cost:

| Approach | Mechanism |
| --- | --- |
| Continuous perf plus a ring-buffer agent | Samples constantly, retains only the last N minutes |
| eBPF-based profilers (off-the-shelf products) | Sample user and kernel stacks with low overhead |
| Runtime-level continuous profilers (Pyroscope, Datadog, or similar) | Language-aware stack collection with continuous profiling UI |

Whichever you pick, the workflow is the same: capture, compare against a baseline, drill
into the widest new stack.

## Constraints and permission

If `perf record` fails with a permission error, the kernel setting is usually the cause:

```bash
cat /proc/sys/kernel/perf_event_paranoid   # read the current level
# Raise it with: sudo sysctl -w kernel.perf_event_paranoid=1
```

Containerized or cloud environments may restrict `perf_event_open` entirely. If so, use an
in-process sampling profiler instead (Node `--cpu-prof` via a signal-triggered dump, or
`py-spy`) rather than fighting the sandbox.

## Verifying before relying on this

`perf` options vary by kernel version and by distribution patches. Check
`perf record --help` on the target machine and read the wiki at
<https://perfwiki.github.io/main/>.
