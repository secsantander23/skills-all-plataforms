# Load Balancing and Session Affinity

## Layer 4 versus layer 7

| | Layer 4 (transport) | Layer 7 (application) |
| --- | --- | --- |
| Decision unit | Connection / flow | Request |
| Sees HTTP | No | Yes |
| Can route by path, host, cookie | No | Yes |
| Can terminate TLS | No (passes through) | Yes |
| Can rewrite headers, cache, compress | No | Yes |
| Per-request load balancing | No, one flow per connection | Yes |
| Overhead | Minimal | Parses HTTP, costs more CPU |

Use layer 4 when you want minimal overhead and pass-through TLS, or when long-lived
connections mean per-request decisions do not matter. Use layer 7 when you need routing by
host or path, TLS termination, caching, or per-request balancing.

## Health checking

**Passive checks** observe real requests: `max_fails` failures within `fail_timeout` marks
an upstream as down for `fail_timeout`. Available in open source nginx.

```nginx
upstream api {
    server 10.0.1.11:8080 max_fails=3 fail_timeout=30s;
    server 10.0.1.12:8080 max_fails=3 fail_timeout=30s backup;
    keepalive 32;
}
```

**Active checks** probe an endpoint on an interval. In open source nginx this is an NGINX
Plus feature (`health_check` in an upstream); in other proxies and load balancers
(HAProxy, Envoy, cloud load balancers) it is standard.

Rules for active checks:

- The check must be cheaper than the traffic it protects, or it becomes the load.
- Check interval must be much shorter than the timeout you are protecting. If the check
  interval exceeds the client timeout, the client has already failed.
- The check endpoint must not touch the database unless you intend to detect database
  failure too. A readiness check that depends on every dependency will flap under partial
  degradation.
- Separate liveness from readiness. Liveness failing means "restart me". Readiness failing
  means "remove me from the load balancer" — it should not restart anything.

```nginx
upstream api {
    server 10.0.1.11:8080 max_fails=2 fail_timeout=10s;
    keepalive 16;
    # Plus-only: health_check interval=5000 rise=2 fall=3 timeout=2000 type=http;
    #           health_check mandatory persistent;
}
```

## Draining

When an instance is going away, stop sending it new requests before stopping it:

1. Flip readiness to failing, so the balancer removes it.
2. Wait at least one health-check interval plus the balancer's failure threshold.
3. Signal the process to finish in-flight requests.
4. Stop accepting new connections.
5. Exit.

```go
// Drain then exit.
func (s *Server) Shutdown(ctx context.Context) {
    s.ready.Store(false)                 // balancer stops sending traffic
    time.Sleep(2 * time.Second)          // at least one health check interval
    s.http.Shutdown(ctx)                 // finish in-flight requests
}
```

Skipping the wait is the usual cause of intermittent 502s during a deploy: the balancer
still sends requests to an instance that has already stopped listening.

## Consistent hashing

Consistent hashing maps keys to points on a ring; each node owns a segment. Adding or
removing a node moves only its own segment, not the whole keyspace. nginx uses ketama
hashing for `hash ... consistent`.

```nginx
upstream cache_nodes {
    hash $cookie_sessionid consistent;
    server cache-1.internal:11211;
    server cache-2.internal:11211;
    server cache-3.internal:11211;
}
```

What it is good for:

- Session affinity without pinning to a specific server, so membership changes move few
  sessions.
- Shard routing for a key that maps one-to-one to a partition.
- Caching that needs locality.

What it is not good for: load balancing. Consistent hashing gives *distribution* of keys,
not *balance of load*, because key popularity is not uniform.

Hash-tag syntax in Redis Cluster (`{user42}`) forces related keys onto the same slot, which
is the same idea applied to the key space rather than the node set.

## Weighted and priority pools

```nginx
upstream mixed {
    # More capacity to the bigger pool.
    server big-1.internal:8080 weight=3;
    server big-2.internal:8080 weight=3;
    # Backup takes traffic only when the primaries are all down.
    server small.internal:8080 backup;
    # Down takes no traffic at all.
    server old.internal:8080 down;
}
```

Weights express capacity relative to each other, not absolute traffic. Weights need
revisiting as instances change; a stale weight silently under- or over-uses a pool.

## Real IP and forwarding

Behind a proxy or load balancer, the source address your app sees is the proxy's. Use the
`PROXY protocol` when you control both ends and need the true client address preserved
end-to-end, or trusted forwarding headers when you do not.

```nginx
# Receiving the PROXY protocol (open source nginx supports this on listen).
server {
    listen 8080 proxy_protocol;
    set_real_ip_from 10.0.0.0/8;
    real_ip_header proxy_protocol;
    ...
}

# Forwarding to an upstream that speaks it is an NGINX Plus feature
# (proxy_protocol on; inside an upstream server block). Do not assume it is available in
# open source nginx; confirm against your build's documentation.
```

If you use `X-Forwarded-For` instead, treat it as untrusted input from the public internet.
Accept it only from addresses you control, and only as the *last* value your own proxy
appended, never the whole comma-separated chain blindly. Otherwise a client can spoof its
address and bypass IP-based access control.

```nginx
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Real-IP $remote_addr;
```

## Connection reuse

```nginx
upstream api {
    least_conn;
    server 10.0.1.11:8080;
    server 10.0.1.12:8080;
    keepalive 32;
}

location / {
    proxy_pass http://api;
    proxy_http_version 1.1;          # required for upstream keepalive
    proxy_set_header Connection "";   # clear the default close header
}
```

Omitting `proxy_http_version 1.1` and `Connection ""` is the most common reason upstream
keepalive silently does not work. Tune `keepalive` in the upstream to roughly the expected
concurrent requests per instance; more idle connections than you need hold file descriptors
and memory for nothing.

For HTTP/2 clients, one downstream connection multiplexes many requests, so per-connection
pooling matters less and per-stream flow control becomes the backpressure point.

## Balancing at the application layer

Application-level balancing (a service registry, DNS round robin, or a mesh) changes the
failure modes rather than removing them:

- **DNS round robin** is cheap and needs no infrastructure, but caches delay change and a
  single-record failure affects everything. Also, resolvers may return a subset.
- **Service registries with health-aware endpoints** handle dynamic membership better than
  DNS, at the cost of another component to operate.
- **Service meshes** give consistent balancing, retries, and timeouts per service, at the
  cost of a proxy hop and substantial configuration surface.

Prefer the simplest mechanism that meets your failure requirements. A mesh is a good answer
when you have many services; it is overkill for three.

## Capacity signals for autoscaling

| Signal | Leads or lags CPU? | Notes |
| --- | --- | --- |
| Requests per second | Leads | Ignores request cost entirely |
| Queue depth | Leads | The earliest reliable sign of saturation |
| Concurrency limit utilization | Leads | Directly reflects the bound you set |
| p95/p99 latency | Lags CPU | Good confirmation, poor trigger |
| CPU utilization | Lags | Last to saturate in a queueing system |
| Memory | Context-dependent | Fine for leaking services, poor for steady services |

Scale on a leading signal, confirm with a lagging one. Scaling on p99 alone usually means
you scale after users already feel it.

## Verifying before relying on this

Directive availability depends on your nginx build and version; several load-balancing
features are NGINX Plus only. Confirm at <https://nginx.org/en/docs/> and check
`nginx -V` for the modules your build includes.
