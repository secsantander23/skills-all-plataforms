# Error Design, Interfaces, and Testing

## Error design

Three patterns, and when each is right:

| Pattern | Use |
| --- | --- |
| Sentinel (`var ErrX = errors.New(...)`) | caller branches on a condition, no data needed |
| Wrapped chain (`fmt.Errorf("...: %w", err)`) | add context while preserving the chain |
| Typed error (`type XError struct{...}` with `Error()`) | caller needs structured fields |

```go
package store

import (
	"errors"
	"fmt"
)

var (
	ErrNotFound    = errors.New("record not found")
	ErrDuplicate   = errors.New("record already exists")
)

// ValidationError carries per-field problems.
type ValidationError struct {
	Fields map[string]string
}

func (e *ValidationError) Error() string {
	keys := make([]string, 0, len(e.Fields))
	for k := range e.Fields {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	parts := make([]string, 0, len(keys))
	for _, k := range keys {
		parts = append(parts, fmt.Sprintf("%s: %s", k, e.Fields[k]))
	}
	return "invalid: " + strings.Join(parts, ", ")
}

func (e *ValidationError) Has(field string) bool {
	_, ok := e.Fields[field]
	return ok
}

func Create(ctx context.Context, u User) (int64, error) {
	if errs := u.Validate(); len(errs) > 0 {
		return 0, &ValidationError{Fields: errs}
	}
	// ... insert; map the driver's unique-violation to the sentinel
	if isUniqueViolation(err) {
		return 0, fmt.Errorf("create user %q: %w", u.Email, ErrDuplicate)
	}
	return id, nil
}
```

Caller:

```go
_, err := store.Create(ctx, u)

var ve *store.ValidationError
switch {
case err == nil:
case errors.Is(err, store.ErrDuplicate):
	return 409, "already registered"
case errors.As(err, &ve):
	return 400, ve.Fields
default:
	return 500, "internal error"
}
```

Rules:

- Error strings are lowercase, no trailing punctuation, no "failed to" (the caller adds
  the verb). `errors.New("record not found")`, not `errors.New("Failed to find record!")`.
- One `errors.Is` check per error, at the layer that decides the response.
- Do not `fmt.Errorf` without `%w` when you want the chain preserved. Use `%v` when you
  deliberately want the context without the unwrap link (a log line, not a decision input).
- Wrap once per layer. Wrapping at every function produces `a: b: c: d: underlying` noise.
- Sentinel values must not be re-created per call; they are compared by identity.

## Interface design

```go
// Consumer-defined, minimal.
type Clock interface {
	Now() time.Time
}

type RateLimiter interface {
	Allow(key string, at time.Time) bool
}

type Service struct {
	clock  Clock
	limits RateLimiter
}

func NewService(clock Clock, limits RateLimiter) *Service {
	return &Service{clock: clock, limits: limits}
}
```

- Accept interfaces, return concrete types. A constructor returning `*Service` rather than
  `Service` lets the caller add methods later without breaking them.
- Keep interfaces small (one or two methods). Every method is a burden on every
  implementation, including test doubles.
- Declare the interface where it is consumed. A producer that exports an interface
  interface couples it to its own evolution.
- Compile-time assertion: `var _ Clock = (*fakeClock)(nil)`.

## Receiver rules

```go
type Counter struct {
	mu sync.Mutex
	n  int64
}

func (c *Counter) Inc() { c.mu.Lock(); c.n++; c.mu.Unlock() } // pointer: holds a mutex

type Point struct{ X, Y int }

func (p Point) Norm() float64 { return math.Hypot(p.X, p.Y) } // value: small, immutable
```

- A type containing a `sync.Mutex`, `sync.WaitGroup`, `atomic` value, slice, or map uses
  pointer receivers; copying the struct copies the lock.
- `go vet` reports `copylocks`. Run it in CI.
- Do not mix receiver kinds on one type. It is legal but surprising: a value receiver means
  only the addressable copy sees the mutation.

## Table-driven tests

```go
func TestParseTTL(t *testing.T) {
	tests := []struct {
		name    string
		input   string
		want    time.Duration
		wantErr error
	}{
		{name: "seconds", input: "30s", want: 30 * time.Second},
		{name: "minutes", input: "5m", want: 5 * time.Minute},
		{name: "empty", input: "", wantErr: ErrEmptyTTL},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := ParseTTL(tc.input)
			if !errors.Is(err, tc.wantErr) {
				t.Fatalf("ParseTTL(%q) error = %v, want %v", tc.input, err, tc.wantErr)
			}
			if got != tc.want {
				t.Fatalf("ParseTTL(%q) = %v, want %v", tc.input, got, tc.want)
			}
		})
	}
}
```

`t.Parallel()` inside subtests requires the range variable to be captured correctly; copy
it with `tc := tc` if your Go version predates the per-iteration change, and confirm the
current rules before removing that.

## Fakes for interfaces

```go
type fakeClock struct {
	mu  sync.Mutex
	now time.Time
}

func (f *fakeClock) Now() time.Time {
	f.mu.Lock()
	defer f.mu.Unlock()
	return f.now
}

func (f *fakeClock) Advance(d time.Duration) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.now = f.now.Add(d)
}

var _ Clock = (*fakeClock)(nil)
```

Write the fake in the *test* package so production never compiles it. Give it the minimal
behaviour the test needs; a full fake that mirrors the implementation is a second
implementation to maintain.

## Benchmarks

```go
func BenchmarkHash(b *testing.B) {
	data := make([]byte, 1024)
	b.SetBytes(int64(len(data)))
	b.ReportAllocs()
	b.ResetTimer()

	for i := 0; i < b.N; i++ {
		_ = sha256.Sum256(data)
	}
}
```

Run with `go test -bench=. -benchmem -run=^$`. Compare against a baseline
(`-count=6` plus a stats tool) before claiming a change is faster. `ReportAllocs` matters:
an allocation often explains a time regression more directly than the timings do.

## Test helpers and goroutine safety

```go
func waitFor(t *testing.T, timeout time.Duration, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for !cond() {
		if time.Now().After(deadline) {
			t.Fatal("condition not met before timeout")
		}
		time.Sleep(time.Millisecond)
	}
}
```

A test that only passes when the goroutines happen to interleave a certain way is a flaky
test. Synchronise explicitly (channels, `WaitGroup`) instead of sleeping for a guessed
duration. If you must poll, poll with a deadline and fail with a clear message.