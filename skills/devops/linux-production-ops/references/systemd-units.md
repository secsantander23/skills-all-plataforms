# Unit File and Hardening Reference

## Directory precedence

Unit files are read from several directories. Later directories win.

```
/etc/systemd/system/                  <- admin overrides and drop-ins
/run/systemd/system/                   <- runtime (reboot only), from systemd-run --runtime
/usr/local/lib/systemd/system/
/usr/lib/systemd/system/               <- distribution packages
/lib/systemd/system/
```

Within a unit, drop-ins win over the main file:

```
/etc/systemd/system/<unit>.d/*.conf
/etc/systemd/system/<unit>.service.d/override.conf
```

```bash
systemctl cat nginx                      # show merged view including drop-ins
systemctl edit nginx                      # create/edit a drop-in interactively
systemctl revert nginx                    # remove drop-ins and re-enable
systemctl show nginx -p FragmentPath -p DropInPaths
```

## Minimal service unit

```ini
[Unit]
Description=Minimal example
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/example --config /etc/example.yaml
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
```

## Service types

| Type | Use when | Ready when |
|---|---|---|
| `simple` | ExecStart is the long-running process, no forking | Process started |
| `exec` | Same, but only ExecStart's process is tracked and logged | Process started |
| `forking` | Program daemonizes; PID file in `PIDFile=` | Parent exits |
| `oneshot` | Short task; pair with `RemainAfterExit=yes` | ExecStart exits |
| `notify` | App calls `sd_notify(READY=1)` | App notifies |
| `idle` | Rare; defer until no jobs remain | Job queue drains |

Prefer `notify` for anything with real initialization, because it removes the
guessing between "started" and "serving". `systemd-notify` and language SDKs exist
for this; check whether your runtime supports it before choosing.

## Template units

```
/etc/systemd/system/worker@.service
```

```ini
[Unit]
Description=Worker %i

[Service]
Type=notify
Environment=WORKER_ID=%i
ExecStart=/usr/local/bin/worker --id %i
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now worker@1.service worker@2.service worker@3.service
```

Specifiers in unit files: `%i` instance name, `%n` full unit name, `%p` prefix,
`%N` unescaped name, `%u` user name, `%h` host name, `%t` runtime directory. Run
`systemd-analyze --help` for the full list.

## Hardening directives

Grouped by what they defend against. Verify the score after changes with
`systemd-analyze security <unit>`.

### Privilege and escalation

```ini
NoNewPrivileges=yes              # no setuid binary can gain privileges
RestrictSUIDSGID=yes              # block creating setuid/setgid files
LockPersonality=yes               # block personality() changes
CapabilityBoundingSet=            # drop all capabilities
AmbientCapabilities=              # grant only what is needed
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
```

### Filesystem

```ini
ProtectSystem=strict              # entire filesystem read-only except /dev,/proc,/sys
ReadWritePaths=/var/lib/example /var/log/example
ReadOnlyPaths=/etc
ProtectHome=yes
PrivateTmp=yes                    # private /tmp and /var/tmp
PrivateDevices=yes                # minimal /dev
RootDirectory=/                   # chroot-like, must contain the binary
InaccessiblePaths=/var/lib/secrets
```

### Kernel and process

```ini
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ProtectKernelLogs=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
MemoryDenyWriteExecute=yes        # blocks JIT runtimes; know the tradeoff
ProtectProc=invisible
ProcSubset=pid
UMask=0027
```

### Resource controls

```ini
LimitNOFILE=65535
LimitNPROC=4096
TasksMax=4096
MemoryMax=1G
MemoryHigh=768M
CPUQuota=200%                     # 200% = two cores worth of time
CPUWeight=100                     # relative weight, cgroup v2
IOWeight=100
```

`CPUQuota` throttles; `CPUWeight` prioritizes. Using a quota on a latency-sensitive
service produces throttling stalls that look like mysterious latency spikes. Measure
before choosing.

### Watchdog

```ini
Type=notify
WatchdogSec=30s
NotifyAccess=main
```

The application must call `sd_notify(0, "WATCHDOG=1")` more often than
`WatchdogSec`, or systemd restarts it. This catches wedged processes that are alive
but no longer serving, which an HTTP liveness probe handles awkwardly.

## Resource limits in containers (systemd in a container)

```ini
[Service]
Delegate=yes
MemoryMax=512M
CPUQuota=200%
TasksMax=512
```

`Delegate=yes` is required for systemd to manage cgroup subtrees inside a container
image. Verify the container runtime's cgroup version matches; cgroup v2 is the
current default on modern hosts.

## Timer patterns

```ini
# On a calendar, catch up after downtime
[Timer]
OnCalendar=*-*-* 03:15:00
Persistent=true

# Spacing from the last activation
[Timer]
OnUnitActiveSec=15m
AccuracySec=30s

# Delay from boot, then every interval
[Timer]
OnBootSec=5min
OnUnitActiveSec=1h

# Randomized start across a fleet
[Timer]
OnCalendar=*-*-* 02:00:00
RandomizedDelaySec=45m
```

```bash
systemd-analyze calendar 'Mon..Fri 09:00 Europe/Berlin'
systemd-analyze calendar '*-*-* 02:00:00' --iterations=5
```

## Dependency declarations

| Directive | Meaning |
|---|---|
| `Requires=` | Start this unit; stop this one if it stops |
| `Wants=` | Start this unit; failure does not propagate |
| `BindsTo=` | Stronger `Requires`, also on unexpected disappearance |
| `After=` / `Before=` | Ordering only, no dependency |
| `PartOf=` | Stop and restart propagate from the named unit |
| `PropagatesReloadTo=` | Reload propagates |

`After=` alone does not create a dependency. A unit that only has `After=db.service`
starts before `db.service` if it is enabled without it. Combine `Requires=` (or
`Wants=`) with `After=`.

## Useful commands

```bash
systemd-analyze verify <unit files>      # syntax and reference errors
systemd-analyze security <unit>          # exposure score and list of settings
systemd-analyze blame                    # which units slowed down boot
systemd-analyze critical-chain           # what boot is waiting on
systemd-analyze dot <units>              # dependency graph
systemctl list-dependencies <unit>
systemctl list-units --failed
systemctl list-jobs                       # queued jobs
systemctl show <unit> -p MemoryCurrent -p TasksCurrent -p IOReadBytes
systemd-run --unit=<name> --wait --pty --collect /path/to/cmd
```

## Failed-service quick reference

| `Result=` | Typical cause |
|---|---|
| `exit-code` | Application error; read the journal |
| `signal` | Killed by a signal, often OOM or a manual `kill` |
| `timeout` | `TimeoutStartSec` or `TimeoutStopSec` exceeded |
| `oom-kill` | Memory limit or memory pressure |
| `start-limit-hit` | Restarted too fast; `NRestarts` exceeded `StartLimitBurst` |
| `protocol` | Service never signalled readiness for `Type=notify` |
| `core-dump` | Crashed; inspect with `coredumpctl` |

```bash
coredumpctl list
coredumpctl info <executable> <id>
coredumpctl debug <executable> <id>
```