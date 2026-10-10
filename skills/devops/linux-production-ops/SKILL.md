---
name: Linux Production Operations
description: Covers Linux production operations including systemd services and unit hardening, process supervision, disk inode and memory diagnosis, file descriptor limits, network troubleshooting with ss curl dig openssl, log management and rotation, cron versus systemd timers, patching and reboots, and basic forensics for a degraded host. Use when the user asks about systemd units, a service that will not start, diagnosing high memory or full disks, fd limit errors, slow or failing network calls, logrotate or journald, scheduling jobs, patching hosts, or figuring out what happened to a degraded server.
metadata:
  opencode/autoinvoke: true
---

# Linux Production Operations

Operations work is triage under time pressure. The discipline that pays off is
narrowing scope before forming a theory: is it one process, one service, one host,
one network path, or everything? Every command below is chosen to eliminate a whole
class of causes quickly.

Most of these commands exist on any modern systemd-based Linux distribution. Where
a tool is distribution-specific (`apt` versus `dnf`, or `lastb`), both are shown.

## When to use this

- A service will not start, keeps restarting, or is not healthy after "starting".
- A host is slow, swapping, out of memory, or out of disk.
- An application hits "too many open files".
- Requests time out and you need to tell DNS from TCP from TLS from the app.
- Logs are not rotating, or the journal is consuming the disk.
- A scheduled job has not run, or ran twice.
- Deciding whether a host needs a reboot after a security patch.
- A host is degraded and you need to reconstruct what happened.

## Quick start

Reading a host's state in the right order. Run these before forming any hypothesis.

```bash
# 1. Uptime and load
uptime
cat /proc/pressure/cpu /proc/pressure/memory /proc/pressure/io

# 2. What is failing right now, at what severity
journalctl -p err --since "1 hour ago" --no-pager | tail -50
systemctl --failed

# 3. Where is the system-level log
journalctl -k --since "30 min ago" --no-pager | tail -50

# 4. Resources
free -h
df -hT
df -ih                       # inodes
vmstat 1 5                   # r, si/so, wa, and steal time

# 5. Top consumers
ps aux --sort=-%mem | head -20
ps aux --sort=-%cpu | head -20
du -xhd1 / 2>/dev/null | sort -h | tail -20

# 6. Sockets
ss -s
ss -tulpn                   # listening ports and owners

# 7. Scheduled work
systemctl list-timers --all | head -30
crontab -l
```

## systemd services

```ini
# /etc/systemd/system/acme-api.service
[Unit]
Description=Acme API
Documentation=https://internal.acme.example/runbooks/api
After=network-online.target postgresql.service
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=notify                      # remove if the app cannot sd_notify
User=acme
Group=acme
WorkingDirectory=/srv/acme-api
Environment=NODE_ENV=production
EnvironmentFile=-/etc/acme/api.env    # "-" prefix: do not fail if missing
ExecStartPre=/srv/acme-api/bin/migrate.sh
ExecStart=/usr/local/bin/acme-api serve --config /etc/acme/api.yaml
ExecReload=/bin/kill -HUP $MAINPID
Restart=on-failure
RestartSec=5s
TimeoutStartSec=90s
TimeoutStopSec=45s
KillSignal=SIGTERM
KillMode=mixed
FinalKillSignal=SIGKILL

# Hardening
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
RestrictRealtime=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
CapabilityBoundingSet=
AmbientCapabilities=
ReadWritePaths=/srv/acme-api/data /var/log/acme-api
UMask=0027
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable --now acme-api.service
systemctl status acme-api.service
systemctl cat acme-api.service          # merged unit, including drop-ins
systemctl show acme-api.service -p MemoryCurrent -p TasksCurrent
systemd-analyze verify acme-api.service   # syntax check without starting
systemd-analyze security acme-api.service # hardening report, scored
```

### Key directives and why

| Directive | Effect |
|---|---|
| `Type=simple` | ExecStart is the process; ready when started |
| `Type=notify` | Service is ready when it calls `sd_notify`; enables real readiness |
| `Type=exec` | Like simple, but only the ExecStart process is considered |
| `Type=oneshot` | For short-lived work; combine with `RemainAfterExit=yes` |
| `Restart=on-failure` | Restart on nonzero exit or signal, not on clean exit or manual stop |
| `RestartSec=` | Delay before restart; also acts as a rate limit with `StartLimit*` |
| `TimeoutStartSec=` | Kill the start if it takes longer; set it or the default can be long |
| `TimeoutStopSec=` | Grace period before SIGKILL; the app must drain before it |
| `KillMode=control-group` (default) | Kill every process in the cgroup |
| `KillMode=mixed` | SIGTERM to the main process, SIGKILL to the rest at the end |
| `WatchdogSec=` | Restart if the app stops pinging; requires `sd_notify` support |
| `EnvironmentFile=` | Load `KEY=value` lines; prefix with `-` to make it optional |

`Restart=always` on a unit that exits successfully on a config error produces an
invisible restart loop. Use `on-failure`.

### Drop-ins instead of editing vendor units

```bash
systemctl edit acme-api.service
systemctl edit --runtime acme-api.service     # until next reboot
systemctl revert acme-api.service             # remove all drop-ins
systemctl cat acme-api.service
```

Never edit a unit file shipped by a package. A package upgrade overwrites it; a
drop-in survives.

### Diagnosing a failed start

```bash
systemctl status acme-api.service
journalctl -u acme-api.service -n 200 --no-pager
journalctl -u acme-api.service -b --no-pager        # since boot
journalctl -u acme-api.service --since "10 min ago" -f
systemctl show acme-api.service -p ExecMainStatus -p Result -p NRestarts
```

`Result=` is the short answer: `exit-code`, `signal`, `timeout`, `oom-kill`,
`protocol`, `start-limit-hit`. `NRestarts` above the configured `StartLimitBurst`
means systemd stopped trying, and the fix is to fix the cause, not to raise the
limit.

```bash
# Debug a unit that will not start, without touching the running system
systemd-run --unit=acme-debug --wait --pty /usr/local/bin/acme-api --version
```

## Disk and inode diagnosis

```bash
df -hT                          # space by filesystem type
df -ih                          # inode usage: 100% inodes with free space is a real outage
findmnt                         # mount table, more readable than /proc/mounts
lsblk -f                        # block devices and filesystems
du -xhd1 / 2>/dev/null | sort -h | tail -20   # -x stays on one filesystem
du -xhd1 /var 2>/dev/null | sort -h | tail
lsof +L1                        # deleted files still held open
lsof /var/lib/postgresql         # who is using that path
fuser -vm /var/lib/postgresql     # PIDs and mount point using a filesystem
ncdu -x /                        # interactive, if you can get a shell
```

`df` and `du` disagreeing is the classic signal: **deleted files still held open by a
running process.** The blocks are not freed until every descriptor is closed.

```bash
# Find and free them (service-specific, so understand the impact first)
lsof +L1 | grep -i deleted
systemctl restart postgresql     # frees the handles, at the cost of a restart
# or, targeted:
lsof +L1 | awk '/deleted/{print $1}' | sort -u
```

For containers, the writable layer and the log files are the usual culprits:

```bash
docker system df -v
journalctl --disk-usage
du -sh /var/lib/docker/* 2>/dev/null | sort -h | tail
```

## Memory diagnosis

```bash
free -h
cat /proc/meminfo
vmstat 1 5
ps aux --sort=-%mem | head -20
pmap -x $(pgrep -f acme-api | head -1) | tail -20
smem -p $(pgrep -f acme-api)      # if installed: proportional set size, better than RSS
cat /proc/pressure/memory
dmesg -T | grep -iE 'oom|killed process'
journalctl -k --since "2 hours ago" --no-pager | grep -i oom
```

How to read `vmstat`:

| Field | Meaning when elevated |
|---|---|
| `r` | Run queue longer than CPU count means CPU-bound |
| `si` / `so` | Swap in/out activity; sustained means memory pressure |
| `wa` | Waiting on I/O: slow disks or a saturated device |
| `st` | Steal time: hypervisor taking CPU (shared/virtualized host) |
| `b` | Processes blocked on I/O |

```bash
# Memory pressure indicator, if available
cat /proc/pressure/memory
# cgroup view for containers and services
cat /sys/fs/cgroup/memory.current
cat /sys/fs/cgroup/memory.max
cat /sys/fs/cgroup/memory.events
```

`memory.events` counters for `oom` and `oom_kill` tell you whether a cgroup limit,
not the host, is doing the killing.

**Real gotchas:**

- Linux uses free memory for the page cache. "Low free memory" with healthy `si`/`so`
  and normal application latency is not a problem.
- A process killed by the OOM killer appears as `Killed` in `dmesg`, and its exit
  status from a shell is `137`.
- A container memory limit produces `OOMKilled` in the container runtime even though
  the host has plenty of free memory. Check both.

## File descriptor limits

```bash
ulimit -Sn                        # soft limit
ulimit -Hn                        # hard limit
cat /proc/$(pgrep -f acme-api)/limits | grep -i 'open files'
ls /proc/$(pgrep -f acme-api)/fd | wc -l
cat /proc/sys/fs/file-max          # system-wide ceiling
ss -s                             # socket summary; TIME-WAIT counts toward fds too
```

Symptom: `Too many open files`, `EMFILE`, or `Socket: too many open files` in logs.

Fixes, in order:

```ini
# systemd unit
LimitNOFILE=65535
```

```bash
# For a non-systemd process, in its startup environment
ulimit -n 65535
```

```conf
# nginx
worker_rlimit_nofile 65535;
```

```dockerfile
# container
CMD ["sh", "-c", "ulimit -n 65535 && exec /app/server"]
# or at run time: docker run --ulimit nofile=65535:65535 ...
```

Then verify the *running* process, not the shell. A limit raised after the process
started has no effect.

## Network troubleshooting

Work outward from the process to the outside world. Each step narrows the layer.

```bash
# 1. Is anything listening, and on which address
ss -tulpn
ss -ltnp 'sport = :8080'

# 2. Is the port reachable from this host
curl -sS -o /dev/null -w '%{http_code} %{time_total} %{remote_ip}\n' http://127.0.0.1:8080/healthz

# 3. Does DNS resolve
dig +short app.acme.example
dig @1.1.1.1 +short app.acme.example        # bypasses the local resolver
dig app.acme.example A +noall +answer
dig +trace app.acme.example                 # walk the delegation from the root
cat /etc/resolv.conf
resolvectl status                           # systemd-resolved view

# 4. TCP and TLS
nc -zv db.internal 5432
openssl s_client -connect app.acme.example:443 -servername app.acme.example </dev/null
openssl s_client -connect app.acme.example:443 -servername app.acme.example \
  -showcerts </dev/null 2>/dev/null | openssl x509 -noout -dates -subject -issuer

# 5. HTTP detail
curl -v --max-time 10 https://app.acme.example/healthz
curl -sS -o /dev/null -w 'dns=%{time_namelookup} conn=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}\n' https://app.acme.example/

# 6. Path quality
ping -c3 -W2 10.0.1.5
traceroute -T -p 443 app.acme.example
mtr -rwzc 10 app.acme.example
ip route get 10.0.1.5
```

Interpreting the split: if `time_namelookup` is large, it is DNS. If `time_connect`
is large, it is reachability, a firewall, or a security group. If `time_appconnect`
is large, it is TLS, usually certificate chain or handshake CPU. If
`time_starttransfer` is large, the server is slow.

Packet capture when you need ground truth:

```bash
sudo tcpdump -i any -nn -c 100 'tcp port 8080 and host 10.0.1.5'
sudo tcpdump -i any -nn -vvv -s0 'port 53 and not host 127.0.0.1'
```

Socket states:

```bash
ss -tan state time-wait | wc -l
ss -tan state established | wc -l
ss -s
```

A large TIME-WAIT count points at a chatty client or a keepalive setting, not at a
server problem.

## Logs: journald and logrotate

```bash
journalctl -u acme-api.service -f
journalctl -u acme-api.service --since "2026-01-15 09:00" --until "2026-01-15 10:00"
journalctl -u acme-api.service -p err..alert
journalctl -k --since "1 hour ago"                      # kernel ring buffer
journalctl _PID=12345                                    # by PID
journalctl _COMM=postgres
journalctl -u acme-api.service -o json-pretty | head -50
journalctl --disk-usage
journalctl --vacuum-size=2G
journalctl --rotate
journalctl --verify
journalctl -b -1 -p err                                  # previous boot
```

journald configuration lives in `/etc/systemd/journald.conf`:

```ini
[Journal]
Storage=persistent
SystemMaxUse=2G
SystemKeepFree=1G
MaxRetentionSec=30day
RateLimitIntervalSec=30s
RateLimitBurst=10000
ForwardToSyslog=no
Compress=yes
```

`Storage=persistent` writes to `/var/log/journal`, which survives reboots. That is
almost always what you want on a server. `MaxRetentionSec` bounds growth; the vacuum
runs automatically, so an explicit `journalctl --vacuum-time` is only needed after a
retention policy change.

Traditional log rotation:

```
# /etc/logrotate.d/acme-api
/var/log/acme-api/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0640 acme adm
    postrotate
        /usr/bin/killall -HUP $(cat /run/acme-api/nginx.pid 2>/dev/null) 2>/dev/null || true
    endscript
}
```

```bash
logrotate -d /etc/logrotate.d/acme-api    # debug mode, changes nothing
logrotate -f /etc/logrotate.d/acme-api    # force a rotation now
```

`copytruncate` versus `create` plus `postrotate`: `copytruncate` copies then
truncates, which loses lines written during the copy window. `create` plus a signal
is lossless and correct; use it whenever the program can reopen its log file on a
signal. Use `copytruncate` only when the program cannot.

## cron versus systemd timers

cron:

```cron
# /etc/cron.d/acme-nightly   (six fields: min hour dom mon dow user command)
17 2 * * *  acme   /srv/bin/nightly.sh >> /var/log/acme-nightly.log 2>&1
```

- `/etc/crontab` uses five fields plus the user column.
- `/etc/cron.d/*` files include the user column and must not use dots in the
  filename.
- `@reboot` runs at daemon start, which can be before your services are up.
- Environment is minimal; cron does not source your shell profile. Use absolute
  paths and set `PATH` explicitly in the crontab.
- There is no built-in "catch up if the machine was off". That is what `anacron`
  and systemd's `Persistent=true` are for.

systemd timer:

```ini
# /etc/systemd/system/acme-nightly.service
[Unit]
Description=Acme nightly reconciliation
After=network-online.target

[Service]
Type=oneshot
User=acme
WorkingDirectory=/srv/bin
ExecStart=/srv/bin/nightly.sh
# Long-running work must be bounded so a hung run does not silently overlap.
TimeoutStartSec=6h

# /etc/systemd/system/acme-nightly.timer
[Unit]
Description=Run Acme nightly reconciliation

[Timer]
OnCalendar=*-*-* 02:17:00
Persistent=true
RandomizedDelaySec=30m
AccuracySec=1m
Unit=acme-nightly.service

[Install]
WantedBy=timers.target
```

```bash
systemctl daemon-reload
systemctl enable --now acme-nightly.timer
systemctl list-timers --all
systemd-analyze calendar '*-*-* 02:17:00'      # when does this actually fire?
```

Prefer timers for anything on a modern host: they get `Persistent=true` catch-up,
`RandomizedDelaySec` to avoid a thundering herd across thousands of hosts, accurate
unit-level logging, failure alerts from `systemctl --failed`, and dependencies
expressed with `After=`/`Requires=`.

## Patching and reboots

```bash
# Debian / Ubuntu
apt-get update
apt-get -y upgrade
apt-get -y dist-upgrade          # allows removals for conflicting packages
apt-get autoremove
reboot --check                  # will a reboot be needed?
ls -l /var/run/reboot-required  # Debian sets this

# RHEL / Rocky / Alma / Fedora
dnf check-update
dnf upgrade
dnf needs-restarting -r          # from the needs-restarting package, lists stale processes
needs-restarting -s             # services needing a restart
needs-restarting -b             # reboot required

# Debian equivalent
checkrestart                   # from needrestart, if installed
```

- Reboot when a kernel, `systemd`, `glibc`, or a security-critical package was
  upgraded. Restarting only the affected services leaves a mixed-version host that
  nobody has tested.
- Rolling maintenance beats rebooting everything at once: drain the node or the
  instance from the load balancer, verify, then continue.
- Live kernel patching exists (kpatch on RHEL, gpatch, or a managed service) and
  removes the immediate reboot need for kernel CVEs. It has its own constraints;
  verify against your distribution's documentation.
- Staging the patches and rolling them canaried is a pipeline problem, not a
  package manager problem. See the `ci-cd-pipelines` skill.

## Basic forensics for a degraded host

Chronological reconstruction. Establish the timeline before theorizing.

```bash
# 1. When did it last reboot, and what happened around then
last -a | head -30
lastb -n 50 2>/dev/null          # failed logins, root only
who -b                          # last boot
uptime

# 2. Kernel messages: OOM, hardware errors, filesystem problems
dmesg -T | tail -200
dmesg -T | grep -iE 'oom|error|fail|I/O|xfs|ext4|nvme|segfault'
journalctl -k -b -p warning..alert

# 3. Service failures and restarts
systemctl --failed
systemctl list-units --state=running | wc -l
journalctl --since "2 hours ago" -p err --no-pager | tail -100

# 4. Recent file changes in sensitive locations
find /etc -mtime -1 -type f -printf '%TY-%Tm-%Td %TH:%TM %p\n' 2>/dev/null | sort
find /etc /usr/bin /usr/sbin /tmp /dev/shm -mtime -2 -type f -ls 2>/dev/null

# 5. Account and access review
last -a
getent passwd | awk -F: '$3 >= 1000 && $3 < 65534'
sudo find /etc/sudoers.d -type f -print -exec cat {} \;
stat /etc/passwd /etc/shadow

# 6. Persistence mechanisms
systemctl list-timers --all
ls -la /etc/cron.d /etc/cron.daily /var/spool/cron 2>/dev/null
crontab -l
ls -la /etc/rc.local /etc/init.d 2>/dev/null
ls -la ~/.ssh/authorized_keys
ss -tulpn                      # anything listening that you do not recognize

# 7. Process tree and long-running surprises
ps auxf
pstree -p
ps -eo pid,ppid,user,etime,%cpu,%mem,comm --sort=-%cpu | head -25
```

Preserve volatile evidence before rebooting: `dmesg`, `ss -tapn`, `ps auxf`,
`free -h`, `vmstat 1 5 > /tmp/vmstat.txt`, and the journal. A reboot destroys most
of it.

## Checklist

- Every long-running service is a systemd unit with `Restart=`, `TimeoutStartSec=`,
  and `TimeoutStopSec=` set.
- Units are hardened with `NoNewPrivileges`, `ProtectSystem`, and dropped
  capabilities, then checked with `systemd-analyze security`.
- Vendor units are overridden with `systemctl edit`, never modified.
- Disk and inode alerts at a threshold that leaves room to react.
- Deleted-but-open file runs are checked before any disk emergency.
- Memory pressure is judged from `vmstat` and PSI, not from `free` alone.
- FD limits are verified on the **running** process.
- Health endpoints exist per service and are monitored externally.
- journald is persistent with explicit retention, or logrotate is configured and
  tested in debug mode.
- Scheduled work uses systemd timers with `Persistent=true`.
- Security patching has a defined reboot policy and a canary path.
- Audit logging (auditd or equivalent) is on for hosts with a real threat model.

## Common mistakes

**Symptom: `df` says full, `du` says there is space.**
Cause: deleted files still open. Fix: `lsof +L1`, then restart the holding service.

**Symptom: `df -i` is 100% while disk space is fine.**
Cause: inode exhaustion from millions of small files, typical on mail spools, caches,
or session directories. Fix: find the directory with the highest file count and
clean it; consider a tmpfs or a different storage for churn.

**Symptom: application logs `Too many open files`.**
Cause: per-process limit, not the system limit. Fix: raise `LimitNOFILE` in the unit
(or the app's startup), then restart and verify on the running PID.

**Symptom: service enters a restart loop after a config change.**
Cause: `Restart=always` plus a failing config. Fix: use `Restart=on-failure` and fix
the config; check `systemctl show -p Result -p NRestarts`.

**Symptom: a unit "starts" but the port never listens.**
Cause: `Type=simple` with a wrapper that forks, so systemd tracks the wrong PID.
Fix: use `Type=notify` with sd_notify support, `Type=exec`, or restructure so
ExecStart is the long-running process.

**Symptom: stopping a service takes the full timeout and is SIGKILLed.**
Cause: the app does not handle SIGTERM and drain. Fix: implement graceful shutdown;
raise `TimeoutStopSec=` only as a last resort.

**Symptom: DNS works for some hosts and not others.**
Cause: `ndots` in the search list, or a search domain turning a short name into an
unexpected FQDN. Fix: use FQDNs with a trailing dot where ambiguity exists, and
compare `dig +short name` against `dig +short name.` .

**Symptom: high `wa` in `vmstat`, everything slow.**
Cause: disk or network-backed storage saturation. Fix: identify the device with
`iostat -xz 1 5` (sysstat) and check queue depth and average latency, not just
utilization.

**Symptom: high `st` (steal) on a virtual machine.**
Cause: the hypervisor is oversubscribed. Fix: this is a provider-side problem;
escalate with the `st` evidence attached.

**Symptom: cron job did not run after the host was off overnight.**
Cause: cron does not catch up. Fix: systemd timer with `Persistent=true`.

**Symptom: logs stopped after a rotation.**
Cause: the program holds the old inode and the rotation used `create` without a
signal. Fix: add a `postrotate` signal, or use `copytruncate` for programs that
cannot reopen.

**Symptom: disk filling up steadily after switching to journald.**
Cause: `Storage=auto` left the journal in memory but files elsewhere, or no
retention set. Fix: set `SystemMaxUse` and `MaxRetentionSec`, then vacuum.

**Symptom: `systemd-analyze security` flags `MemoryDenyWriteExecute` blocking a JIT.**
Cause: legitimate, for Java, .NET, or V8 runtimes. Fix: leave it off for that unit
and note why; keep it on everywhere else.

## Anti-patterns

- `kill -9` as a reflex. It prevents graceful shutdown and corrupts state in
  anything writing data.
- Running production processes directly in a terminal, or under `nohup ... &`.
- Disabling a failing health check instead of fixing the check.
- `chmod 777` to fix a permission problem, which fixes nothing and removes the
  ability to detect a misconfiguration.
- Editing `/etc/systemd/system/*.service` files shipped by packages instead of
  drop-ins.
- Deleting logs by hand to free space. Fix the retention policy instead.
- Adding `--no-check-certificate` or `curl -k` to make TLS "work". That converts a
  configuration error into a security hole.
- `sync; drop_caches` as a fix for slow I/O. It removes a legitimate cache and
  makes the next read slower.
- Running everything as root because one service needs a privileged bind port. Use
  `AmbientCapabilities=CAP_NET_BIND_SERVICE` instead.
- Disabling the firewall to isolate a network problem. Narrow the rule to a source
  range and a port, and keep the firewall on.
- Setting every `TimeoutSec` to a huge value to stop false failures. That converts a
  fast failure into a slow outage.
- Rerunning the same cron job manually "to see if it works" without reading its exit
  status or its log.

## References

- systemd.service: https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html
- systemd.unit: https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html
- systemd.exec (hardening): https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html
- systemd timers: https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html
- `systemd-analyze security`: https://www.freedesktop.org/software/systemd/man/latest/systemd-analyze.html
- journalctl: https://www.freedesktop.org/software/systemd/man/latest/journalctl.html
- journald.conf: https://www.freedesktop.org/software/systemd/man/latest/journald.conf.html
- logrotate: https://man7.org/linux/man-pages/man8/logrotate.8.html
- `ss`: https://man7.org/linux/man-pages/man8/ss.8.html
- `lsof`: https://man7.org/linux/man-pages/man8/lsof.8.html
- cgroup v2: https://www.kernel.org/doc/html/latest/admin-guide/cgroup-v2.html
- PSI (pressure stall information): https://docs.kernel.org/accounting/psi.html
- `curl` command line options: https://curl.se/docs/manpage.html
- `dig`: https://bind9.readthedocs.io/en/latest/manpages.html#dig-dns-lookup-utility
- OpenSSL `s_client`: https://docs.openssl.org/3.3/man1/openssl-s_client/
- cron(5) / crontab(5): https://man7.org/linux/man-pages/man5/crontab.5.html
- RHEL patch management: https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/9/html/managing_software_and_keeping_the_system_updated/index
- Debian `needrestart`: https://github.com/needrestart/needrestart

Verify before relying on this: distributions ship different defaults and some
tooling (`needs-restarting`, `checkrestart`, `smem`, `ncdu`, `mtr`, `iostat`) is not
installed by default. Check with `command -v <tool>` and confirm option support with
`<tool> --help` before relying on a flag. Windows and macOS hosts need WSL or a Linux
VM for this workflow; the tools described do not apply natively.