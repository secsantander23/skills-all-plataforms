# Go Concurrency Patterns

## Fan-out, fan-in

```go
func FetchAll(ctx context.Context, urls []string) ([]Result, error) {
	type outcome struct {
		res Result
		err error
	}
	out := make(chan outcome, len(urls)) // buffer to len so sends never block on a slow drain

	var wg sync.WaitGroup
	for _, u := range urls {
		wg.Add(1)
		go func(u string) {
			defer wg.Done()
			r, err := fetch(ctx, u)
			out <- outcome{res: r, err: err}
		}(u)
	}

	wg.Wait()
	close(out)

	results := make([]Result, 0, len(urls))
	for o := range out {
		if o.err != nil {
			return nil, o.err
		}
		results = append(results, o.res)
	}
	return results, nil
}
```

The `wg.Wait()` before `close(out)` is essential: closing earlier makes later sends panic.

## errgroup: cancellation on first error

`golang.org/x/sync/errgroup` is the standard external helper. It propagates the first
non-nil error and cancels a derived context, which removes most of the manual plumbing.

```go
import "golang.org/x/sync/errgroup"

func Process(ctx context.Context, ids []string) error {
	g, ctx := errgroup.WithContext(ctx)
	sem := make(chan struct{}, 8) // bounded concurrency

	for _, id := range ids {
		g.Go(func() error {
			sem <- struct{}{}
			defer func() { <-sem }()
			return handle(ctx, id)
		})
	}
	return g.Wait() // nil if all succeeded; first error otherwise
}
```

With `SetLimit` available in recent versions of the package, the semaphore is unnecessary.
Check the package documentation for your version rather than assuming.

## Bounded worker pool

```go
type Job func(ctx context.Context) error

// Run executes jobs with at most workers concurrent tasks, stopping at the first error.
func Run(ctx context.Context, workers int, jobs []Job) error {
	if workers < 1 {
		workers = 1
	}
	work := make(chan Job)
	g, ctx := errgroup.WithContext(ctx)

	for i := 0; i < workers; i++ {
		g.Go(func() error {
			for j := range work {
				if err := j(ctx); err != nil {
					return fmt.Errorf("job: %w", err)
				}
			}
			return nil
		})
	}

	g.Go(func() error {
		defer close(work)
		for _, j := range jobs {
			select {
			case work <- j:
			case <-ctx.Done():
				return ctx.Err()
			}
		}
		return nil
	})

	return g.Wait()
}
```

## Semaphores as concurrency limits

Two ways, both real:

```go
import "golang.org/x/sync/semaphore"

var limiter = semaphore.NewWeighted(8) // units, not counts

if err := limiter.Acquire(ctx, 1); err != nil {
	return err
}
defer limiter.Release(1)
```

```go
// Or a plain buffered channel, which also works for a simple count.
var sem = make(chan struct{}, 8)

select {
case sem <- struct{}{}:
	defer func() { <-sem }()
case <-ctx.Done():
	return ctx.Err()
}
```

## Graceful shutdown

```go
func Run(ctx context.Context, srv *http.Server, worker *Worker) error {
	ctx, stop := signal.NotifyContext(ctx, os.Interrupt, syscall.SIGTERM)
	defer stop()

	errCh := make(chan error, 1)
	go func() {
		// Shutdown has its own deadline separate from the request context.
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		return worker.Shutdown(shutdownCtx)
	}()

	go func() { errCh <- srv.ListenAndServe() }()

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		sctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		if err := srv.Shutdown(sctx); err != nil {
			return fmt.Errorf("shutdown: %w", err)
		}
		return worker.Shutdown(sctx)
	}
}
```

The shutdown context must not derive from the cancelled parent, or it is already dead on
arrival. `signal.NotifyContext` exists in the standard library; verify its availability for
your Go version.

## Singleflight / de-duplication

`golang.org/x/sync/singleflight` collapses concurrent identical calls into one execution:

```go
var group singleflight.Group

func LoadUser(ctx context.Context, id string) (*User, error) {
	v, err, _ := group.Do("user:"+id, func() (any, error) {
		return store.Get(ctx, id)
	})
	if err != nil {
		return nil, err
	}
	return v.(*User), nil
}
```

This is the right tool for cache-miss stampedes and for deduplicating webhook deliveries.
Note the returned value is `any`: assert it, and be aware that callers may share the same
pointer.

## Timers without leaks

```go
// Leaks a timer per iteration until each expires.
for i := 0; i < 1000; i++ {
	go func() { time.After(time.Hour); doWork() }()
}

// One reusable timer.
timer := time.NewTimer(time.Hour)
defer timer.Stop()

for _, item := range items {
	timer.Reset(time.Hour)
	select {
	case <-timer.C:
		process(item)
	case <-ctx.Done():
		return ctx.Err()
	}
}
```

## Mutex versus channel decision

| Situation | Prefer |
| --- | --- |
| Protecting a map or a struct field | `sync.Mutex`; simpler, no channel ceremony |
| Passing ownership of a value between goroutines | channel |
| Signalling completion of one unit of work | channel (or `WaitGroup` for a batch) |
| Streaming a sequence of values | channel |
| Very high-frequency counters | `atomic.Int64` |
| Read-mostly cache with disjoint writes | `sync.Map`, if the pattern fits |
| Waiting on several possible sources | `select` |

Do not use a channel where a mutex suffices: channels carry a synchronisation edge you did
not need, and a buffer of the wrong size is a deadlock waiting for a slow reader.

## Detecting leaks

```go
func TestMain(m *testing.M) {
	goleak.VerifyTestMain(m)  // external package golang.org/x/tools or uber-go/goleak
}
```

Or, without a dependency, in tests:

```go
func TestNoLeakedGoroutines(t *testing.T) {
	before := runtime.NumGoroutine()
	doWork(context.Background())
	// give goroutines a chance to exit
	deadline := time.Now().Add(2 * time.Second)
	for runtime.NumGoroutine() > before && time.Now().Before(deadline) {
		runtime.Gosched()
		time.Sleep(10 * time.Millisecond)
	}
	if after := runtime.NumGoroutine(); after > before {
		t.Fatalf("goroutine leak: %d -> %d", before, after)
	}
}
```

## Race detector scope

- `-race` only reports races on executed paths. Increase coverage with
  `-count=10` or by running tests under real concurrency.
- `-race` requires CGO and a supported platform. On a platform where it is unavailable the
  build fails rather than silently skipping; check the `go help buildconstraint` output
  for your environment.
- The detector's "Previous read/write" stack is the *other* access; both stacks are needed
  to find the missing happens-before edge.
- A clean `-race` run does not prove there are no races. It proves there are none on those
  paths.

## Verify before relying on this

`errgroup.SetLimit`, `sync.OnceFunc`, `sync.OnceValue`, and the per-loop-variable scoping
rules all depend on your Go version. Check:

- https://pkg.go.dev/golang.org/x/sync/errgroup
- https://pkg.go.dev/sync
- https://go.dev/wiki/LoopvarExperiment (historical; the scoping change shipped in a
  language release)

before depending on them.