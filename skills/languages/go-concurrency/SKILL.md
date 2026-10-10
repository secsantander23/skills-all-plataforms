---
name: Go Concurrency and Idioms
description: Covers goroutines and channels, select, context cancellation, the sync package (WaitGroup, Mutex, RWMutex, Once, atomic), data races and the race detector, worker pools, error handling with errors.Is and errors.As plus wrapping, interfaces and receivers, and defer, panic, and recover discipline. Use when writing concurrent Go, debugging a race detector report or a goroutine leak, choosing between channels and mutexes, propagating cancellation, or structuring Go APIs and error paths.
metadata:
  opencode/autoinvoke: true
---

# Go Concurrency and Idioms

Go's concurrency model is deliberately small: goroutines, channels, `select`, `context`,
and a handful of synchronisation primitives in `sync`. The discipline that makes code
correct is: goroutines have no inherent lifetime, so every one you start must have a clear
owner that knows how it stops; and anything shared between goroutines is a data race
until a happens-before relationship proves otherwise.

## When to use this

- The user is writing concurrent Go and needs to choose channels versus a mutex.
- The user got a race detector report (`WARNING: DATA RACE`) or a "concurrent map writes"
  panic.
- The user is seeing a goroutine leak, a deadlock ("all goroutines are asleep - deadlock!"),
  or a context that never cancels.
- The user is building a bounded worker pool or a request fan-out with cancellation.
- The user is designing an API and needs guidance on interfaces, receivers, and error
  types.
- The user is asking about `defer`, `panic`/`recover`, or cleanup ordering.
- The user wants to know when to use `context.Background` versus a passed-in context.

## Quick start

A bounded worker pool with cancellation and error propagation:

```go
package main

import (
	"context"
	"errors"
	"fmt"
	"sync"
)

var ErrSkipped = errors.New("record skipped")

type Record struct {
	ID   int
	Name string
}

type Result struct {
	ID   int
	Err  error
}

// Transform runs fn over each item with at most workers goroutines.
// The first error is returned, and cancellation propagates to in-flight work.
func Transform(ctx context.Context, items []Record, workers int,
	fn func(context.Context, Record) (Result, error)) ([]Result, error) {
	if workers < 1 {
		workers = 1
	}

	work := make(chan Record)
	out := make(chan Result)

	var wg sync.WaitGroup
	wg.Add(workers)

	for i := 0; i < workers; i++ {
		go func() {
			defer wg.Done()
			for item := range work {
				res, err := fn(ctx, item)
				if err != nil {
					res = Result{ID: item.ID, Err: err}
				}
				select {
				case out <- res:
				case <-ctx.Done():
					return
				}
			}
		}()
	}

	go func() {
		defer close(work)
		defer close(out)
		for _, item := range items {
			select {
			case work <- item:
			case <-ctx.Done():
				return
			}
		}
	}()

	results := make([]Result, 0, len(items))
	for r := range out {
		if r.Err != nil && errors.Is(r.Err, ErrSkipped) {
			continue
		}
		results = append(results, r)
	}

	wg.Wait() // workers exit once `work` closes
	return results, ctx.Err()
}

func main() {
	items := []Record{{ID: 1, Name: "a"}, {ID: 2, Name: "b"}}
	res, err := Transform(context.Background(), items, 2,
		func(ctx context.Context, r Record) (Result, error) {
			return Result{ID: r.ID}, nil
		})
	if err != nil {
		fmt.Println("cancelled:", err)
	}
	fmt.Println(res)
}
```

Four properties worth noticing: the producer closes `work`, the workers close nothing and
exit on `range`, `out` is closed only after the producer returns, and every send has a
`ctx.Done()` arm so cancellation cannot deadlock the drain.

## Goroutines and channels

A goroutine is a function scheduled on a shared thread pool. It starts as soon as `go f()`
is evaluated; there is no thread affinity and no priority control.

Unbuffered channels synchronise. Buffered channels decouple by a fixed amount and are what
give a pipeline its shape.

```go
ch := make(chan int)        // synchronous handoff
bch := make(chan int, 10)   // up to 10 sends without a receiver
close(ch)                   // signals "no more values"; only the sender closes
v, ok := <-ch               // ok == false after close and drain
```

Rules:

- Close from the sender side only. Closing a channel twice panics.
- Receivers must handle the closed case: `for v := range ch` terminates on close; a bare
  `<-ch` without an `ok` check returns zero values forever.
- A zero-value channel (`var ch chan int`) is `nil`: sends block forever, receives block
  forever. Useful in `select` for "never ready", dangerous by accident.
- `len(ch)` and `cap(ch)` are instantaneous samples, not a synchronisation mechanism.
- A send on a channel nobody receives leaks both the goroutine and its data. Every send
  belongs in a `select` with a `<-ctx.Done()` arm if the goroutine can be cancelled.

`select` picks uniformly at random among ready cases, so it is not priority-ordered:

```go
select {
case v := <-results:
    use(v)
case <-ctx.Done():
    return ctx.Err()
case <-time.After(2 * time.Second):
    return ErrTimeout
}
```

## Context: cancellation, deadlines, values

`context.Context` is the standard way to cancel work, bound a deadline, and carry
request-scoped values. Rules from the official docs:

- Pass `ctx` as the **first parameter** of any function that does I/O or may block, and
  store it in a struct field rather than a package-level variable.
- Do not store contexts in a struct field as a general-purpose bag; store them in
  request-scoped structs where the lifetime matches the request.
- `context.Background()` is the root; use it only in `main`, tests, and top-level
  `init`-style setup. `context.TODO()` marks a gap that should be filled later.
- Cancel a context you created: `ctx, cancel := context.WithTimeout(parent, d);
  defer cancel()`. Leaking a cancel function leaks the timer and, for
  `WithCancel`, the parent entry.
- `WithValue` is for request-scoped metadata (trace ids, auth subjects) with a key type
  private to your package, never for optional parameters.

```go
type ctxKey struct{}

// In package a:
func WithRequestID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, ctxKey{}, id)
}
func RequestID(ctx context.Context) (string, bool) {
	v, ok := ctx.Value(ctxKey{}).(string)
	return v, ok
}
```

Error mapping that callers rely on:

```go
if err := fn(ctx); err != nil {
	if errors.Is(err, context.DeadlineExceeded) {
		return 503, "upstream timeout"
	}
	if errors.Is(err, context.Canceled) {
		// The client went away. Usually not worth retrying or alerting on.
		return 499, "client closed request"
	}
	return 500, "internal error"
}
```

A `context.Context` value must not be stored in a struct field by value as a way to extend
its lifetime; that defeats the parent's cancellation. Use `context.WithoutCancel` when you
genuinely need to detach a context from cancellation (for example, for a cleanup task
that must run), and check it is available in your Go version.

## sync primitives

| Primitive | Use |
| --- | --- |
| `sync.WaitGroup` | wait for a known set of goroutines to finish |
| `sync.Mutex` | exclusive access to a value |
| `sync.RWMutex` | many readers, one writer (often `Mutex` is faster; measure) |
| `sync.Once` | run an expensive initialisation exactly once, safely |
| `sync.Map` | a map optimised for read-heavy, append-mostly workloads |
| `atomic` package | lock-free single-value operations (`atomic.Int64`, `atomic.Pointer[T]`) |
| `sync.Cond` | rare; almost always replaceable by a channel |
| `errgroup.Group` | a goroutine set that cancels on first error (external package) |

```go
var (
	once     sync.Once
	registry map[string]*Entry
)

func initRegistry() {
	once.Do(func() {
		registry = make(map[string]*Entry)
		for _, name := range defaultNames {
			registry[name] = newEntry(name)
		}
	})
}

func Lookup(name string) (*Entry, bool) {
	initRegistry()
	e, ok := registry[name]   // safe: map is only written once before any read
	return e, ok
}
```

WaitGroup discipline: `Add` before `go`, `Done` with `defer` inside the goroutine, and
never call `Add` concurrently with `Wait` when the counter can pass through zero. Passing
a `*sync.WaitGroup` by value is a compile error (it must not be copied); the vet tool
catches accidental copies.

```go
var wg sync.WaitGroup
results := make([]int, len(ids))

for i, id := range ids {
	wg.Add(1)
	go func(i, id int) {
		defer wg.Done()
		v, err := lookup(id)
		if err != nil {
			return // results[i] stays zero; or record the error
		}
		results[i] = v
	}(i, id) // Go versions before 1.22 loop vars are shared; passing them is always safe
}

wg.Wait()
```

## Data races and the race detector

A data race is two goroutines accessing the same memory, at least one writing, without a
happens-before relationship. The race detector is a dynamic (not static) check: it finds
races on the code paths your tests actually execute.

```bash
go build -race ./...
go test -race ./...
go run -race ./cmd/server
```

The report is precise. Read it top-down:

```text
WARNING: DATA RACE
Write at 0x00c0000b4010 by goroutine 8:
  main.(*Cache).Put()
      /src/cache.go:42 +0x44

Previous read at 0x00c0000b4010 by goroutine 7:
  main.(*Cache).Get()
      /src/cache.go:30 +0x3c
```

Two stacks, one address, no synchronisation edge between them. Fix by adding the
happens-before edge: a mutex, a channel send/receive, `atomic`, or `sync.Once`.

Common sources of races:

- A map written by one goroutine while another reads or iterates it. Go's runtime detects
  this in some cases and panics with "concurrent map writes" / "concurrent map read and
  map write", but a plain read/write race on a slice element is silent.
- `append` to a slice from two goroutines: the append may reallocate, and both goroutines
  write the header.
- A struct field written in one goroutine and read in another with no lock.
- Accessing a captured loop variable that another goroutine modifies.
- A lazily-initialised map or slice with no `sync.Once`.

`sync.Map` is a specialised type: it optimises for (a) entries written once and read many
times, or (b) disjoint key sets across goroutines. For a plain counter, an
`atomic.Int64` is faster and clearer. For a general map with uniform access, a
`Mutex` around a `map` is usually the simplest correct answer.

```go
type Cache struct {
	mu sync.RWMutex
	m  map[string][]byte
}

func (c *Cache) Get(k string) ([]byte, bool) {
	c.mu.RLock()
	v, ok := c.m[k]
	c.mu.RUnlock()
	return v, ok
}

func (c *Cache) Put(k string, v []byte) {
	c.mu.Lock()
	c.m[k] = v
	c.mu.Unlock()
}
```

Design rule: **do not return internal slices or pointers** from a method that holds a lock.
The caller now holds a reference to memory you are about to mutate, and the race detector
will point at your lock as if it were the bug.

## Error handling

Go errors are values. Wrap with `%w` so callers can inspect the chain.

```go
var (
	ErrNotFound   = errors.New("not found")
	ErrConflict   = errors.New("conflict")
	ErrUnavailable = errors.New("temporarily unavailable")
)

type FieldError struct {
	Field string
	Issue string
}

func (e *FieldError) Error() string {
	return fmt.Sprintf("field %q: %s", e.Field, e.Issue)
}

func LoadUser(ctx context.Context, id string) (*User, error) {
	u, err := store.Get(ctx, id)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, fmt.Errorf("load user %s: %w", id, ErrNotFound)
		}
		return nil, fmt.Errorf("load user %s: %w", id, err)
	}
	if u.Email == "" {
		return nil, fmt.Errorf("load user %s: %w", id, &FieldError{Field: "email", Issue: "required"})
	}
	return u, nil
}
```

Caller side:

```go
_, err := LoadUser(ctx, "u1")
switch {
case err == nil:
	// ok
case errors.Is(err, ErrNotFound):
	return 404
case errors.Is(err, context.DeadlineExceeded):
	return 504
default:
	var fe *FieldError
	if errors.As(err, &fe) {
		return 400, fmt.Sprintf("%s: %s", fe.Field, fe.Issue)
	}
	return 500
}
```

Rules:

- `errors.Is` compares against a chain; `errors.As` extracts a typed error. Use `errors.As`
  with a pointer to the target type: `var fe *FieldError; errors.As(err, &fe)`.
- Wrap with `%w` only when the caller benefits. `%v` formats without creating an unwrap
  link, which is right for logging-only wraps that should not be matched.
- Sentinel errors support `==` and `Is`. Typed error structs carry data.
- Never compare error strings in code; use sentinels or types.
- Return errors, not panics, for expected failures.
- An error message is lowercase and has no punctuation, because callers prepend context:
  `fmt.Errorf("load user %s: %w", id, err)`, not `"Failed to load user!"`.

## Interfaces and receivers

Small, consumer-defined interfaces are the Go idiom. Define the interface in the package
that *uses* it, not the one that implements it.

```go
// In package a: the consumer states what it needs.
type UserStore interface {
	Get(ctx context.Context, id string) (*User, error)
	Put(ctx context.Context, u *User) error
}

// In package b: the implementation satisfies it structurally.
type SQLUserStore struct{ db *sql.DB }

func (s *SQLUserStore) Get(ctx context.Context, id string) (*User, error) { /* ... */ }
func (s *SQLUserStore) Put(ctx context.Context, u *User) error { /* ... */ }

// Somewhere: a compile-time assertion the implementation matches.
var _ UserStore = (*SQLUserStore)(nil)
```

Receiver choice:

| Receiver | Use |
| --- | --- |
| value `(s Store)` | small immutable value types; `time.Time`, `strings.Builder` use this |
| pointer `(*Store)` | any type holding a slice, map, mutex, or mutable fields |

Mixing value and pointer receivers on one type is legal but confusing; pick one. A type
with a `sync.Mutex` must use pointer receivers, since a copied mutex is a bug (and `go vet`
flags it).

## defer, panic, recover

`defer` runs at function return, LIFO, including on panic. Arguments are evaluated at the
`defer` statement, not at return.

```go
func Process(ctx context.Context, files []string) (err error) {
	fh, err := os.Open(files[0])
	if err != nil {
		return fmt.Errorf("open %s: %w", files[0], err)
	}
	defer fh.Close()          // err captured only if you name the return value

	defer func() {
		if cerr := fh.Close(); cerr != nil && err == nil {
			err = fmt.Errorf("close %s: %w", files[0], cerr)
		}
	}()
	return nil
}
```

Rules:

- Keep `defer` bodies cheap; they run on every path including the error path.
- `recover()` only works when called directly inside a deferred function of the panicking
  goroutine. You cannot recover a panic from a panic in a *different* goroutine; that
  crashes the process by design.
- Recover at a boundary, log, and convert to an error. Do not resume execution deep inside
  a stack with half-initialised state.
- Never use panic for control flow in a library. Use it for programmer errors (a nil
  interface where one is required) and for unrecoverable states, per the package docs.

```go
func serve(listener net.Listener) error {
	for {
		conn, err := listener.Accept()
		if err != nil {
			var ne net.Error
			if errors.As(err, &ne) && ne.Timeout() {
				continue
			}
			return fmt.Errorf("accept: %w", err)
		}
		go handle(conn)
	}
}

// At the top-level boundary:
func main() {
	if err := run(); err != nil {
		slog.Error("fatal", "err", err)
		os.Exit(1)
	}
}
```

## Checklist

- [ ] Every `go` statement has a documented owner and a termination condition.
- [ ] Every send on a channel that a goroutine waits on has a `ctx.Done()` arm in a
      `select`.
- [ ] Context is the first parameter of blocking functions; created contexts are cancelled
      with `defer cancel()`.
- [ ] Shared mutable state is protected by a mutex, an atomic, or a channel; nothing
      relies on "it probably runs on one core".
- [ ] `go test -race ./...` runs in CI.
- [ ] Locked methods do not return internal slices, maps, or pointers.
- [ ] Errors are wrapped with `%w` where callers need to branch, and typed error structs
      are extracted with `errors.As`.
- [ ] Interfaces are declared at the point of use and stay small.
- [ ] `go vet ./...` is clean (it catches copied locks, printf mistakes, and more).
- [ ] `defer` bodies do no expensive work; resources are closed on every path.

## Common mistakes

**"fatal error: all goroutines are asleep - deadlock!"** - every goroutine is blocked
forever. Usual causes: an unbuffered channel with no sender, a `select` with no reachable
arm, or a mutex held while waiting on something that needs that mutex. Reproduce with a
small program and read the goroutine dump printed with the panic.

**Goroutine leak** - goroutines blocked on `<-ch` where the sender already returned, usually
because an error path closed without draining. Fix with context cancellation and by
having the *sender* close the channel. `go.uber.org/goleak` in tests is a practical
detector.

**"concurrent map writes"** - a plain `map` mutated from two goroutines. Add a
`sync.RWMutex` or use `sync.Map` where its access pattern fits.

**A race detector report where both stacks are in library code.** The bug is often that
you returned a slice from a locked method and the caller mutated it. Return a copy, or
return an immutable type.

**`WaitGroup` misuse: `Add` inside the goroutine.** The counter can hit zero before `Add`
runs, so `Wait` returns early. Call `wg.Add(1)` before `go`.

**A `defer` that reads the loop variable at return time.**

```go
for _, f := range files {
	defer os.Remove(f)                // correct: f is evaluated NOW
	defer func() { os.Remove(f) }()   // wrong: the closure reads f at RETURN time
}
```

`defer f(x)` evaluates its arguments where the `defer` statement appears. `defer f(func(){...})`
evaluates the argument too, but the closure body runs later and reads the loop variable as
it is then. Go releases change what the loop variable is, so do not rely on either
behaviour across versions; copy to a local and defer that:

```go
for _, f := range files {
	path := f
	defer func() { os.Remove(path) }()
}
```

**`ctx` cancelled in a `defer` before the returned error is checked.**

```go
ctx, cancel := context.WithTimeout(parent, time.Second)
defer cancel()
return fn(ctx)   // fn returns a ctx.Err() derived from the cancelled context
```

This is correct as written, but if you store `ctx` anywhere for later use after the
function returns, it is already cancelled.

**`time.After` in a hot loop.** Each call allocates a timer that lives until it fires.
Use a single `time.Timer` with `Reset` in a loop, or a ticker.

**Returning `nil` for a typed error.**

```go
func get(id string) (*User, error) {
	if id == "" {
		return nil, nil   // caller dereferences a nil *User and panics
	}
}
```

## Anti-patterns

- **Sharing a variable between goroutines "because they probably won't collide".** The race
  detector will eventually prove otherwise.
- **A goroutine per request with no limit.** Use a bounded pool or a semaphore; an
  unbounded fan-out is a denial-of-service vector.
- **Using a buffered channel to hide a deadlock.** It moves the hang to the next
  unbuffered send.
- **Locking around `defer Unlock()` in a function with early returns that return a
  reference to the protected data.**
- **`interface{}` with six methods where two small interfaces would do.**
- **Panic as "throw" for expected errors**, forcing every caller to recover.
- **Comparing `err.Error()` to a string.**
- **Passing a `sync.Mutex` by value** (struct copies). Always use a pointer.
- **Blocking forever in a goroutine with no context**, which pins memory for the process
  lifetime.
- **`time.Sleep` for synchronisation** instead of a channel or `WaitGroup`.

## References

- The Go Programming Language Specification and blog
  - https://go.dev/ref/spec
  - https://go.dev/blog/context
  - https://go.dev/blog/laws-of-reflection
  - https://go.dev/blog/error-handling-and-go
  - https://go.dev/blog/defer-panic-and-recover
  - https://go.dev/blog/code-review-comments
- Standard library documentation
  - https://pkg.go.dev/context
  - https://pkg.go.dev/sync
  - https://pkg.go.dev/sync/atomic
  - https://pkg.go.dev/errors
  - https://pkg.go.dev/os#File.Close
- Tooling
  - https://pkg.go.dev/cmd/go
  - https://pkg.go.dev/cmd/vet
  - https://go.dev/doc/articles/race_detector
  - https://pkg.go.dev/cmd/go#hdr-Compile_and_run_test_programs_with_the_race_detector
- Frequently asked questions on concurrency
  - https://go.dev/wiki/Concurrency

For deeper reading:

- `references/concurrency-patterns.md` - errgroup, fan-out/fan-in, semaphores, graceful
  shutdown, singleflight, pipelines, leak detection.
- `references/errors-and-api.md` - error design, sentinel versus typed errors, interface
  design, receiver rules, testing and benchmarking patterns.