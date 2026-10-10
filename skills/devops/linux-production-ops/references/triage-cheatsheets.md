# Host Triage Cheatsheets

Copy-pasteable command blocks, grouped by symptom. Substitute your service and
paths. Confirm a tool exists with `command -v <tool>` first.

## "The host is slow"

```bash
uptime                                   # load average vs core count
nproc
vmstat 1 5                               # r / si-so / wa / st
iostat -xz 1 5                           # sysstat: %util, aqu-sz, r_await, r/s
free -h
cat /proc/pressure/{cpu,memory,io}
ps -eo pid,ppid,user,%cpu,%mem,rss,etime,comm --sort=-%cpu | head -20
ps -eo pid,ppid,user,%cpu,%mem,rss,etime,comm --sort=-%mem | head -20
```

Decision:

- Load high, `r` high, `st` low: CPU-bound application.
- `st` high on a VM: hypervisor oversubscription, escalate with evidence.
- `wa` high: storage saturation; `iostat -xz 1 5` to find the device.
- `si`/`so` non-zero: memory pressure; check `memory.events` and the cgroup limit.
- Everything idle but users report slowness: check network and storage latency, not
  CPU. `ping`, `mtr`, and disk latency (`fio --name=randread --rw=randread
  --bs=4k --iodepth=32 --runtime=10 --time_based --direct=1 --filename=/srv/test`
  on a safe filesystem) tell more.

## "The service is down"

```bash
systemctl status <svc>
systemctl is-enabled <svc>
systemctl cat <svc>
journalctl -u <svc> -n 200 --no-pager
systemctl show <svc> -p Result -p ExecMainStatus -p ExecMainCode -p NRestarts -p SubState
systemd-analyze verify /etc/systemd/system/<svc>.service
systemd-analyze critical-chain <svc>         # what is it waiting on
```

`SubState=start` plus no progress usually means an `After=` dependency is not
satisfied. `Result=exit-code` means the app exited; read the journal.

## "Ports are not reachable"

```bash
ss -ltnp | grep -E ':(8080|443)\b'
ss -tan | grep 10.0.1.5 | head
ip -br addr
ip route
ip route get 10.0.1.5                       # which interface and source address
sudo nft list ruleset 2>/dev/null || sudo iptables -S
sudo tcpdump -i any -nn -c 50 'tcp port 8080' &
curl -sS -o /dev/null -w '%{http_code} %{time_total}\n' http://10.0.1.5:8080/
```

If nothing is listening, it is the application. If it is listening on `127.0.0.1`
only, it is a bind address problem. If it is listening and reachable locally but
not remotely, it is a firewall or security group.

## "Memory keeps growing"

```bash
free -h
ps aux --sort=-%mem | head -20
pmap -x $(pgrep -f <svc> | head -1) | sort -k3 -n | tail -25
smem -p $(pgrep -f <svc>) -H | head -20
cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.max 2>/dev/null
cat /sys/fs/cgroup/memory.events 2>/dev/null
dmesg -T | grep -iE 'oom|killed process'
```

- RSS grows and never returns: a leak in the process. Compare over hours with a
  sampler, not two samples a minute apart.
- RSS is flat but "memory used" grows: page cache or a cgroup accounting artifact,
  not a leak.
- `oom_kill` count non-zero in `memory.events`: the cgroup limit is the constraint.

## "Disk is filling up"

```bash
df -hT
df -ih
du -xhd1 /var 2>/dev/null | sort -h | tail -15
du -xhd1 /var/log 2>/dev/null | sort -h | tail -15
lsof +L1 | head -30
journalctl --disk-usage
```

Root causes in rough order of frequency: logs without rotation, deleted files held
open, container writable layers, package caches, and runaway temp files.

```bash
# Package caches
apt-get clean                       # Debian
dnf clean all                       # RHEL family

# Old kernels, if you actually own multiple (never remove the running one)
dnf remove --oldinstallonly --setopt installonly_limit=2 kernel
```

## "DNS is flaky"

```bash
resolvectl status
resolvectl flush-caches
cat /etc/resolv.conf
dig +short app.acme.example
dig @1.1.1.1 +short app.acme.example
dig +tcp +short app.acme.example
dig app.acme.example A +noall +answer
dig acme.example NS +noall +answer
```

- Local resolver fails, public resolver works: the resolver or the search/ndots
  configuration is the problem.
- Both fail: check the network path and whether outbound DNS and port 53 are
  permitted.
- Intermittent only for some names: look for a wildcard or an internal split-horizon
  zone, and for `ndots` turning short names into search-domain lookups.

## "TLS is failing"

```bash
openssl s_client -connect app.acme.example:443 -servername app.acme.example </dev/null
openssl s_client -connect app.acme.example:443 -servername app.acme.example \
  -showcerts </dev/null 2>/dev/null | openssl x509 -noout -dates -subject -issuer -ext subjectAltName
curl -v --max-time 10 https://app.acme.example/ 2>&1 | grep -iE 'subject|issuer|expire|SSL|verify'
```

The three common failures: hostname not in the certificate SAN list, an expired or
not-yet-valid certificate, and an incomplete chain (the server omitted an
intermediate, which some clients tolerate and others do not).

## "Everything got slow after a deploy"

```bash
uptime
vmstat 1 5
iostat -xz 1 5
journalctl -u <svc> --since "30 min ago" --no-pager | tail -80
systemctl show <svc> -p NRestarts
df -h /var/lib/<svc>
```

Common causes: cold caches after a restart, a new dependency connection storm, a
schema change that dropped an index, or a deploy that moved from a warm pool to cold
capacity.

## "Who was on the box"

```bash
last -a | head -40
last -n 50 <user>
lastb -n 50 2>/dev/null
who -a
journalctl _COMM=sshd --since "7 days ago" --no-pager | grep -iE 'Accepted|Failed|Invalid'
journalctl -u sshd --since "7 days ago" --no-pager | grep -i accepted
find / -xdev -mtime -2 -type f -perm -4000 2>/dev/null      # unexpected setuid
stat /etc/passwd /etc/shadow /etc/sudoers
```

## Collection script for a degraded host

Run this before rebooting or resizing anything, then attach the output to the
incident.

```bash
#!/usr/bin/env bash
set -uo pipefail
OUT="/tmp/host-capture-$(date -u +%Y%m%dT%H%M%SZ).txt"

{
  echo "=== date/uptime ==="; date -u; uptime; who -b
  echo "=== uname ==="; uname -a
  echo "=== failed units ==="; systemctl --failed --no-pager
  echo "=== vmstat ==="; vmstat 1 5
  echo "=== free ==="; free -h
  echo "=== psi ==="; cat /proc/pressure/cpu /proc/pressure/memory /proc/pressure/io
  echo "=== df ==="; df -hT; df -ih
  echo "=== top cpu ==="; ps -eo pid,ppid,user,%cpu,%mem,rss,etime,comm --sort=-%cpu | head -25
  echo "=== top mem ==="; ps -eo pid,ppid,user,%cpu,%mem,rss,etime,comm --sort=-%mem | head -25
  echo "=== sockets ==="; ss -s; ss -tulpn
  echo "=== deleted open ==="; lsof +L1 2>/dev/null | head -40
  echo "=== journal errors ==="; journalctl -p err -b --no-pager | tail -80
  echo "=== kernel ==="; dmesg -T 2>/dev/null | tail -80
  echo "=== timers ==="; systemctl list-timers --all --no-pager
  echo "=== journal size ==="; journalctl --disk-usage
} > "$OUT" 2>&1

echo "$OUT"
```

Send the file, not the host. It contains hostnames, process arguments, and
connections; treat it as sensitive data.