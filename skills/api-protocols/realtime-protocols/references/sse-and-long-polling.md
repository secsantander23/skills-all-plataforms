# SSE and Long Polling Reference

Detail for `realtime-protocols`. SSE is defined in the HTML Living Standard rather than an RFC.

## Complete field grammar

From the `event-stream` parsing rules:

```text
stream        := [ bom ] *event
event         := *( comment | field ) end-of-line
comment       := colon *any-char end-of-line
field         := 1*name-char [ colon [ space ] *any-char ] end-of-line
end-of-line   := ( cr lf | cr | lf )
name-char     := %x41-5A / %x61-7A / %x30-39     ; A-Z a-z 0-9
any-char      := %x00-09 / %x0B-0FF / %x100-10FFFF ; not CR or LF
bom           := %xFEFF
```

Consequences worth internalising:

- A field with no colon is a field with an empty value. `data` on its own line is an empty `data` field.
- A single leading space after the colon is stripped; further spaces are part of the value.
- Lines end with CRLF, CR, or LF. Any of the three terminates a line.
- Only ASCII letters and digits are valid field names. `data :` with a space before the colon is not a `data` field, it is a malformed field named nothing.
- A UTF-8 BOM at the very start is consumed and ignored. Strip it anyway, since some proxies mangle it.

## Dispatch algorithm

1. Set the data buffer to the empty string and the event type buffer to empty.
2. For each line:
   - Empty line: if the data buffer is empty, reset the buffers and continue. Otherwise dispatch the event with the current event type (or `message` if unset), the data buffer, and the last event id. Reset the data buffer and event type buffer. The last event id is **not** reset.
   - Line starts with `U+003A` colon: ignore the line.
   - Line contains a colon: field name is before the colon, value is after with one leading space removed.
   - No colon: field name is the whole line, value is empty.
   - Field `event`: set the event type buffer.
   - Field `data`: append the value plus a line feed to the data buffer.
   - Field `id`: if the value contains no U+0000, set the last event id to the value.
   - Field `retry`: if the value is all ASCII digits, set the reconnection time to that value in milliseconds.
   - Any other field name: ignore.
3. Discard any trailing incomplete event at end of stream without dispatching.

The "dispatch only on blank line" and "reset data but not id" rules are the two most commonly misremembered.

## Reconnection algorithm

On network drop, or on an HTTP error other than certain statuses, the user agent waits the reconnection time (default implementation-defined if never set) and then:

1. If the ready state is `CLOSED`, become `CONNECTING`.
2. Reconnect with the last event id as the value of the `Last-Event-ID` header.
3. If that header would be an empty string, do not send the header.

Servers therefore have two documented ways to receive the cursor: the `Last-Event-ID` request header, or a `?lastEventId=` query parameter if you need non-browser clients or a client that cannot set headers.

## Proxy and infrastructure behaviours

| Hop | Default behaviour | Fix |
| --- | --- | --- |
| CDN | Buffers and may compress; may cap connection duration | Disable buffering and compression for this route; set a generous idle timeout |
| nginx | `proxy_buffering on` by default | `proxy_buffering off;` and `proxy_read_timeout` raised |
| Compression middleware | `gzip`/`br` buffers to compress | `no-transform` in `Cache-Control` signals intermediaries not to transform |
| Load balancer | Idle timeout, often 30 to 60 seconds | Raise it, and rely on a heartbeat |
| HTTP/1.1 browser limit | Six concurrent connections per origin | Multiplex one stream |

Sends `Cache-Control: no-cache, no-transform` explicitly. The `no-transform` part is what stops an intermediary applying compression to an event stream.

## Heartbeat

```text
: 2026-02-01T09:00:00Z keep-alive

```

A comment line. The server should track the time of the last successful write and close the stream if a write fails, because a TCP connection to a vanished client can stay "open" for a long time before the OS notices.

Keep-alive intervals should be comfortably shorter than the shortest idle timeout in the path. Thirty seconds is a common starting point; measure against your own infrastructure.

## Replay buffer design

```text
id source        durable store (database sequence) or broker-assigned offset
buffer           ring buffer of recent events, in memory, per stream
response to new subscriber with Last-Event-ID = N:
  - N >= oldest buffered id   -> send buffered events with id > N, then go live
  - N <  oldest buffered id   -> send event: reset, do not silently skip
  - N >  newest id            -> treat as a fresh subscriber (start live) or reject
```

Every event must be written to the live stream and, if replay matters, persisted before or as it is published. If you persist after publishing, a client that disconnects during that window reconnects with an id the store has not caught up with, and you either replay an event twice or skip one. Both are visible to the user.

An id that is durable across restarts needs a real sequence. A per-process counter resets on restart and will make clients reconnect with an id that is meaningless. Use a database sequence, a broker-assigned offset, or a monotonic store of your own.

## SSE versus long polling, measured

| Dimension | SSE | Long polling |
| --- | --- | --- |
| Direction | Server to client only | Both, with a normal request for client to server |
| Protocol overhead per event | One line of framing | A full request and response |
| Connection count | One per stream | One per in-flight poll |
| Reconnection | Automatic in the browser | Your retry loop |
| Resume support | `Last-Event-ID` built in | Yours to implement with a cursor parameter |
| Proxies | Need buffering disabled | Generally fine |
| Request headers | Cannot be customised | Fully customisable |
| Cross-origin | Not workable | Works with CORS |
| Binary | No | Yes, in the response body |

If you are already on HTTP/2 in front of the browser, the six-connection limit does not apply, and SSE's simplicity usually wins for server-to-client.

## Hybrid pattern: SSE downstream, POST upstream

```javascript
const source = new EventSource("/events");

source.addEventListener("chat.message", (event) => {
  render(JSON.parse(event.data));
});

document.querySelector("#composer").addEventListener("submit", async (event) => {
  event.preventDefault();
  const input = document.querySelector("#composer input");
  await fetch("/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text: input.value }),
  });
  input.value = "";
});
```

The server validates the `Accept` header on the GET and requires the POST on the same path with a distinct method. Authentication is identical for both, which is the practical advantage over running a WebSocket purely to receive events.

## Testing

```bash
# Raw stream with headers visible.
curl -N -D - -H 'Accept: text/event-stream' https://example.com/events

# Simulate a reconnect with a cursor.
curl -N -H 'Accept: text/event-stream' -H 'Last-Event-ID: 1724' https://example.com/events

# Verify the browser's view of the stream in DevTools: Network tab, EventStream entry,
# Messages pane. Chrome and Firefox both show the parsed event stream.
```

Automate the resume test: connect, read the first three events, disconnect, wait past the retry interval, reconnect with the captured `lastEventId`, and assert you receive event four and not events one through three.

## References

- HTML Living Standard, Server-sent events - https://html.spec.whatwg.org/multipage/server-sent-events.html
- MDN, Using server-sent events - https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events/Using_server-sent_events
- MDN, EventSource: message events - https://developer.mozilla.org/en-US/docs/Web/API/EventSource/message_event
- nginx, proxy buffering documentation - https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_buffering
- RFC 9111, HTTP Caching (why no-store or no-cache matters here) - https://www.rfc-editor.org/rfc/rfc9111
- WPT, server-sent events tests - https://wpt.fyi/results/web/server-sent-events