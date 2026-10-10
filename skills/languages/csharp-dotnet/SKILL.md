---
name: C# and .NET
description: Covers nullable reference types, records, LINQ, async and await with the synchronization context, dependency injection built into the generic host, minimal APIs and controllers, configuration, LINQ performance traps, and xUnit testing. Use when writing or reviewing C#, when debugging a possible NullReferenceException from nullable warnings, when choosing LINQ versus loops, when wiring dependency injection or configuration, or when building ASP.NET Core endpoints and tests.
metadata:
  opencode/autoinvoke: true
---

# C# and .NET

Modern C# gives you compile-time nullability, immutable data types with structural
equality, a query language over in-memory collections, and an application host that wires
dependency injection, configuration, and logging for you. The discipline that keeps code
maintainable: model state so invalid combinations do not compile, keep `async` all the way
down without blocking, and treat LINQ as a composable expression rather than a magic
performance layer.

## When to use this

- The user is writing C# and hitting nullable reference type warnings, or debugging a
  `NullReferenceException` in code that "should not be null".
- The user asks about `record` versus `class`, init-only setters, or primary constructors.
- The user is choosing LINQ versus a loop, or debugging slow LINQ.
- The user is wiring dependency injection, `IConfiguration`, or the generic host.
- The user is building web endpoints: minimal APIs versus MVC controllers.
- The user is writing xUnit tests and needs fixtures, theory data, and async test
  patterns.
- The user is debugging an `async` method that blocks, deadlocks, or throws a
  `TaskSchedulerException`.

## Quick start

A nullable-clean, immutable domain model with a minimal API and DI:

```csharp
using System.ComponentModel.DataAnnotations;

namespace Acme.Billing;

public enum PaymentState
{
    Pending,
    Authorized,
    Captured,
    Voided,
}

public sealed record Money(decimal Amount, string Currency)
{
    public Money
    {
        if (Currency is not { Length: 3 })
        {
            throw new ArgumentException("Currency must be a three-letter code.", nameof(Currency));
        }
    }

    public static Money Zero(string currency) => new(0m, currency);

    public Money Add(Money other)
    {
        if (!string.Equals(Currency, other.Currency, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException("Cannot add amounts in different currencies.");
        }

        return this with { Amount = Amount + other.Amount };
    }
}

public sealed record Payment(string Id, Money Amount, PaymentState State)
{
    public Payment Authorized() => this with { State = PaymentState.Authorized };

    public Payment Captured() => this with
    {
        State = this.State == PaymentState.Authorized
            ? PaymentState.Captured
            : throw new InvalidOperationException(
                  $"Cannot capture from state {this.State}.")
    };
}

public interface IPaymentRepository
{
    Task<Payment?> FindAsync(string id, CancellationToken ct);

    Task SaveAsync(Payment payment, CancellationToken ct);
}

public interface IPaymentGateway
{
    Task<string> AuthorizeAsync(Money amount, CancellationToken ct);
}

public sealed class PaymentService(
    IPaymentRepository repository,
    IPaymentGateway gateway,
    ILogger<PaymentService> logger)
{
    public async Task<Payment> AuthorizeAsync(string id, CancellationToken ct)
    {
        ArgumentException.ThrowIfNullOrEmpty(id);

        var existing = await repository.FindAsync(id, ct).ConfigureAwait(false);
        if (existing is not null)
        {
            return existing;
        }

        var reference = await gateway.AuthorizeAsync(Money.Zero("USD"), ct).ConfigureAwait(false);
        var payment = new Payment(id, Money.Zero("USD"), PaymentState.Pending).Authorized();

        logger.LogInformation("Authorized {PaymentId} with reference {Reference}", id, reference);
        await repository.SaveAsync(payment, ct).ConfigureAwait(false);
        return payment;
    }
}
```

Two details worth copying: `record` positional syntax lets you add validation with a body
that takes no arguments (`public Money { ... }`), and `ConfigureAwait(false)` documents
that the continuation does not need the calling synchronization context.

## Nullable reference types

Enable them project-wide in the project file:

```xml
<PropertyGroup>
  <TargetFramework>net8.0</TargetFramework>
  <Nullable>enable</Nullable>
  <ImplicitUsings>enable</ImplicitUsings>
  <TreatWarningsAsErrors>true</TreatWarningsAsErrors>
</PropertyGroup>
```

Annotations you will use every day:

```csharp
// The compiler proves a non-null argument.
public string Name { get; init; } = string.Empty;

// "May be null" as input, non-null as output: the ??= does the narrowing.
public static string Normalize(string? input) => (input ?? string.Empty).Trim();

// Nullable value types are a different mechanism: int? is a real nullable int.
public int? Score { get; init; }

// Disjunctive normal form: "non-null or both null", the shape the compiler understands best.
public static string Describe(string? first, string? last) =>
    (first, last) switch
    {
        (not null, not null) => $"{first} {last}",
        (not null, null) => first,
        (null, not null) => last,
        _ => "anonymous",
    };

// Safe navigation: null-conditional members.
public int? Length => this.Name?.Length;
```

Migration traps when enabling nullable on an existing project:

| Situation | Handling |
| --- | --- |
| Overridable method has a non-nullable parameter that can be null from a subclass | make the base parameter `string?`, or document the contract |
| Method returns `T?` for a reference type that callers never expect to be null | tighten the return type to `T` |
| Field assigned later in a lifecycle | `= null!` as a temporary marker, then fix the design; track the remaining `null!` occurrences |
| Interface implementation mismatch | update the interface signature so both sides agree |
| `async` method lacks `ConfigureAwait` and analyzer complains | add it in libraries and applications alike for clarity |

The null-conditional operator is not a null check:

```csharp
// Does nothing for a null receiver; use it when the "absent" case is a valid no-op.
var n = maybeUser?.Profile?.Nickname;

// Guards an invariant: throw when the value must be present.
var user = userRepository.Find(id) ?? throw new InvalidOperationException($"No user {id}.");
```

`ArgumentNullException.ThrowIfNull(obj)` and `ArgumentException.ThrowIfNullOrEmpty(s)`
are the canonical argument guards.

## Records, init, and primary constructors

```csharp
// Positional record: properties, Deconstruct, Equals/GetHashCode, ToString, and `with`.
public sealed record Address(string Line1, string? Line2, string City, string PostCode);

// Non-positional record when you want validation of an existing shape.
public sealed record Customer
{
    public required string Id { get; init; }
    public required string Name { get; init; }
    public string? Email { get; init; }

    public Customer WithName(string name) => this with { Name = name };
}

// `required` forces object initialisers to set the member.
var c = new Customer { Id = "u1", Name = "Ada" };
```

Comparison of shapes:

| Need | Use |
| --- | --- |
| immutable data with structural equality | `record` (positional or `required`/`init`) |
| mutable reference type | `class` |
| identity equality regardless of contents | `class` (default `Equals`) |
| inheritance between data types | `class`; records compose rather than inherit |
| data with one optional extra field | record with a nullable component |

Record inheritance is limited: a record cannot inherit from a class, and a record cannot be
`abstract` in the way a class can while still being used as a base with generated equality
across the hierarchy. Model variant state with a discriminated union style (an enum plus
records carrying a common base record) rather than deep record hierarchies.

## LINQ

```csharp
using System.Linq;

var charged = payments
    .Where(static p => p.State == PaymentState.Captured)
    .Select(static p => p.Amount.Amount)
    .Sum();

// Deferred execution: nothing happens until enumerated.
var query = customers.Where(c => c.Name.StartsWith("A", StringComparison.Ordinal));

// Multiple enumeration re-runs the whole pipeline.
foreach (var _ in query) { }
foreach (var _ in query) { }   // runs the filter again

// Materialise once when you enumerate more than once.
var materialized = query.ToList();

// Grouping.
var byState = payments
    .GroupBy(p => p.State)
    .ToDictionary(g => g.Key, g => g.Sum(p => p.Amount.Amount));

// Lookup, for keys that repeat.
ILookup<PaymentState, Payment> lookup = payments.ToLookup(p => p.State);

// Join, and the query-expression form.
var details = from p in payments
              join c in customers on p.CustomerId equals c.Id
              where p.State == PaymentState.Captured
              select new { p.Amount, c.Name };

// Joins to null collections return empty; LeftJoin does not match nulls.
var pairs = from c in customers
            join p in payments on c.Id equals p.CustomerId into g
            from p in g.DefaultIfEmpty()
            select new { c.Name, Payment = p };

// Aggregate and immediate operators.
var firstBig = payments.FirstOrDefault(p => p.Amount.Amount > 1000m);
var total = payments.Aggregate(Money.Zero("USD"), (acc, p) => acc.Add(p.Amount));
```

`foreach` over a LINQ query re-evaluates. If the collection can change between
iterations, or you enumerate twice, call `ToList()`/`ToArray()` once.

## LINQ performance traps

```csharp
// TRAP: Select that projects, then Where. Predicate pushed into the source is faster
// because SelectMany with Select creates intermediate enumerations.
var slow = orders.Select(o => o.Customer).Where(c => c.IsActive).ToList();
var fast = orders.Where(o => o.Customer.IsActive).Select(o => o.Customer).ToList();

// TRAP: repeated enumeration of the same chain.
var active = orders.Where(o => o.Status == Status.Active);   // deferred
var a1 = active.Count();                                      // enumerates once
var a2 = active.ToList();                                     // enumerates again
var a = active.ToList();                                      // enumerate once, reuse

// TRAP: closure allocation in a hot loop over a large collection.
foreach (var order in orders)
{
    var local = threshold;      // copy out of the captured variable
    if (order.Total > local) { /* ... */ }
}

// TRAP: OrderBy on an unsorted source then Skip/Take is O(n log n); use Where first.
var recent = orders.OrderByDescending(o => o.PlacedAt).Skip(20).Take(10).ToList();

// TRAP: Any() with a predicate is fine; Count(predicate) enumerates everything,
// while TryGetNonEnumeratedCount / Count on a known-collection type can be O(1).
bool hasBad = orders.Any(o => o.Total <= 0);   // short-circuits
```

Rules:
- Keep LINQ off the true hot path unless you have measured. A plain `for` loop with a
  `List<T>` is predictable.
- `ToList()` in a loop that also calls `ToList()` on the query allocates a new list each
  time; build once.
- Do not call `AsParallel()` for small collections; the partitioning overhead dominates.
- Use `StringComparison.Ordinal` for string comparisons unless you need culture-aware
  behaviour.
- If a query is reused across requests, project it to a concrete type (`IReadOnlyList<T>`)
  once.

## async/await and the synchronization context

`await` on an incomplete `Task` captures the current synchronization context (the ASP.NET
Core request context, or a UI thread) and posts the continuation back to it. That is
correct for UI code and harmless in ASP.NET Core (which has no ambient context), but it is
a deadlock source in libraries and in tests.

```csharp
// DEADLOCK: .Result blocks the thread that the continuation needs.
var data = LoadAsync().Result;

// DEADLOCK in a UI or xUnit sync context.
var data = LoadAsync().GetAwaiter().GetResult();   // still blocks

// Correct: async all the way.
var data = await LoadAsync().ConfigureAwait(false);
```

Rules:

1. `async` all the way down. Never `.Result`, `.Wait()`, or `GetAwaiter().GetResult()`.
2. In libraries, use `ConfigureAwait(false)` so callers' contexts are not assumed.
3. Never `async void` except for event handlers; everything else is `async Task`.
4. Return `Task`/`Task<T>`; never return `IEnumerable` from an async method.
5. Pass a `CancellationToken` through every async public API and honour it.
6. `ValueTask<T>` for hot paths with few or no awaits; otherwise `Task<T>`. It is easy to
   misuse (multiple awaits on a non-cached `ValueTask` is incorrect).

```csharp
public sealed class PaymentService(IPaymentRepository repository, CancellationToken ct)
{
    public async Task<Payment> LoadAsync(string id, CancellationToken cancellationToken)
    {
        var payment = await repository.FindAsync(id, cancellationToken).ConfigureAwait(false);
        return payment ?? throw new KeyNotFoundException($"Payment {id} not found.");
    }
}
```

```csharp
// Task.WhenAll runs concurrently; the await waits for all of them.
var results = await Task.WhenAll(
    FetchAsync(urls[0], ct),
    FetchAsync(urls[1], ct),
    FetchAsync(urls[2], ct));

// WhenAny for timeouts and first-completion-wins.
var winner = await Task.WhenAny(lookupTask, Task.Delay(TimeSpan.FromSeconds(2), ct));
if (winner == lookupTask)
{
    return await lookupTask;
}

throw new TimeoutException("Lookup did not complete in time.");

// WhenAll with tasks that may fail: unwrap the first exception explicitly.
try
{
    await Task.WhenAll(tasks);
}
catch (Exception)
{
    foreach (var task in tasks.Where(t => t.IsFaulted))
    {
        _ = task.Exception;
    }
}
```

Do not create a `Task.Run` per item for IO-bound work: `HttpClient` is already
asynchronous. `Task.Run` moves CPU-bound work off the request thread; wrap it in a library
method with a clear name rather than scattering it through call sites.

## Dependency injection and the host

The generic host wires configuration, logging, DI, and lifetime management.

```csharp
using Microsoft.Extensions.Configuration;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;

var builder = WebApplication.CreateBuilder(args);   // also reads appsettings.json and env

// Configuration: JSON files, environment variables, command line, user secrets in dev.
builder.Configuration.AddEnvironmentVariables("ACME_");

// Services. Lifetimes:
//   Singleton: one instance for the app's lifetime; must be thread safe.
//   Scoped: one per request (or per scope); the default for DbContext.
//   Transient: a new instance every resolution.
builder.Services.AddSingleton<IClock, SystemClock>();
builder.Services.AddScoped<IPaymentRepository, SqlPaymentRepository>();
builder.Services.AddTransient<PaymentService>();

// Typed options with validation.
builder.Services.AddOptions<GatewayOptions>()
    .Bind(builder.Configuration.GetSection("Gateway"))
    .ValidateDataAnnotations()
    .ValidateOnStart();

builder.Services.AddHttpClient<IPaymentGateway, HttpPaymentGateway>((sp, client) =>
{
    var options = sp.GetRequiredService<Microsoft.Extensions.Options.IOptions<GatewayOptions>>().Value;
    client.BaseAddress = new Uri(options.BaseUrl);
    client.Timeout = TimeSpan.FromSeconds(options.TimeoutSeconds);
});

var app = builder.Build();

app.MapGet("/health", () => Results.Ok(new { status = "ok" }));
app.Run();
```

Constructor injection is the default; only use property injection when a framework
requires it. Services resolved from the root provider (injected into a singleton's
constructor is fine, resolved from `IServiceProvider` is a captive dependency) must be
documented.

Options:

```csharp
public sealed class GatewayOptions
{
    public const string SectionName = "Gateway";

    [Required]
    [Url]
    public string BaseUrl { get; init; } = string.Empty;

    [Range(1, 120)]
    public int TimeoutSeconds { get; init; } = 30;
}
```

```json
{
  "Logging": {
    "LogLevel": {
      "Default": "Information",
      "Acme.Billing": "Debug"
    }
  },
  "Gateway": {
    "BaseUrl": "https://sandbox.example.com/",
    "TimeoutSeconds": 15
  }
}
```

Precedence: appsettings.json, then appsettings.{Environment}.json, then user secrets
(development), then environment variables, then command-line arguments. Later sources win.

## Minimal APIs versus controllers

Minimal API: less ceremony, good for small endpoints and internal services.

```csharp
var builder = WebApplication.CreateBuilder(args);
var app = builder.Build();

// Typed results give OpenAPI metadata and clear status codes.
app.MapGet("/payments/{id}", async (string id, IPaymentRepository repo, CancellationToken ct) =>
{
    var payment = await repo.FindAsync(id, ct);
    return payment is null
        ? TypedResults.NotFound()
        : TypedResults.Ok(payment);
})
.WithName("GetPayment");

app.MapPost("/payments", async (CreatePayment body, PaymentService service, CancellationToken ct) =>
{
    ArgumentNullException.ThrowIfNull(body);
    var created = await service.CreateAsync(body, ct);
    return TypedResults.Created($"/payments/{created.Id}", created);
});

// Minimal-API filters are the clean way to add cross-cutting behaviour.
app.MapGet("/admin/stats", (StatsQuery q, IStats stats) => TypedResults.Ok(stats.Compute(q)))
   .RequireAuthorization("Admin");

app.Run();
```

MVC controllers: attribute routing, model binding with validation, filters, and content
negotiation. Choose controllers for larger surfaces where those features are used heavily.

Rules for either: keep endpoints thin and move logic into injected services; validate at
the edge; map domain exceptions to status codes in one place (an exception-handling
middleware or a filter), not in every action; and never leak exception messages to
clients.

## Testing with xUnit

```csharp
using Microsoft.Extensions.DependencyInjection;
using Xunit;

public sealed class PaymentServiceTests
{
    [Fact]
    public async Task AuthorizeAsync_creates_a_pending_then_authorized_payment()
    {
        var repository = new InMemoryPaymentRepository();
        var gateway = new FakeGateway();
        var service = new PaymentService(repository, gateway, NullLogger<PaymentService>.Instance);

        var result = await service.AuthorizeAsync("p1", CancellationToken.None);

        Assert.Equal(PaymentState.Authorized, result.State);
        await repository.VerifiedSaveAsync(result);
    }

    [Theory]
    [InlineData(0, 100)]
    [InlineData(101, 100)]
    public void Add_rejects_currency_mismatch(decimal amount, decimal other)
    {
        var left = new Money(amount, "USD");
        var right = new Money(other, "EUR");

        Assert.Throws<InvalidOperationException>(() => left.Add(right));
    }

    [Fact]
    public void Captured_rejects_pending_payments()
    {
        var pending = new Payment("p1", Money.Zero("USD"), PaymentState.Pending);
        Assert.Throws<InvalidOperationException>(() => pending.Captured());
    }

    [Fact]
    public void Money_rejects_a_bad_currency_code()
    {
        Assert.Throws<ArgumentException>(() => new Money(1m, "DOLLARS"));
    }
}
```

Conventions and helpers:

- `[Fact]` for one case, `[Theory]` with `[InlineData]`/`[MemberData]`/`[ClassData]` for
  many.
- `Assert.Throws<T>` for the synchronous case; `await Assert.ThrowsAsync<T>(() => ...)` for
  async.
- `Assert.Equal`, `Assert.Same`, `Assert.Contains`, `Assert.Collection` cover most needs.
  `Assert.Equivalent` compares whole object graphs.
- Fixtures with `IClassFixture<T>` (shared across the class, created once),
  `ICollectionFixture<T>` (shared across classes, disable parallelisation), or a
  constructor-created fixture for per-test instances.
- `IAsyncLifetime` provides `InitializeAsync`/`DisposeAsync` for async setup and teardown.
- FluentAssertions, Shouldly, and Moq/NSubstitute are widely used; check their current
  licence and API on their sites before adding a dependency.

```csharp
public sealed class DatabaseFixture : IAsyncLifetime
{
    // Any ADO.NET provider; swap the connection type for your stack.
    public DbConnection Connection { get; private set; } = null!;

    public async ValueTask InitializeAsync()
    {
        var cs = Environment.GetEnvironmentVariable("TEST_CONNECTION_STRING")
                 ?? throw new InvalidOperationException("TEST_CONNECTION_STRING is not set.");

        Connection = CreateConnection(cs);
        await Connection.OpenAsync();

        await using var command = Connection.CreateCommand();
        command.CommandText = """
            CREATE TABLE IF NOT EXISTS payments (
                id TEXT PRIMARY KEY,
                amount NUMERIC NOT NULL,
                currency TEXT NOT NULL,
                state TEXT NOT NULL
            )
            """;
        await command.ExecuteNonQueryAsync();
    }

    public async ValueTask DisposeAsync()
    {
        if (Connection is not null)
        {
            await Connection.DisposeAsync();
        }
    }
}

public sealed class RepositoryTests(DatabaseFixture fixture) : IClassFixture<DatabaseFixture>
{
    [Fact]
    public async Task SaveAsync_round_trips()
    {
        var repo = new SqlPaymentRepository(fixture.Connection);
        await repo.SaveAsync(new Payment("p1", Money.Zero("USD"), PaymentState.Pending));

        var found = await repo.FindAsync("p1");

        Assert.NotNull(found);
        Assert.Equal(PaymentState.Pending, found!.State);
    }
}
```

`IAsyncLifetime` members return `ValueTask` in current xUnit; check your xUnit version's
interface signature before writing a fixture, since it changed across major versions.

Test the public surface. If you need to reach an internal type, add
`[assembly: InternalsVisibleTo("MyProject.Tests")]`.

## Checklist

- [ ] `<Nullable>enable</Nullable>` and warnings treated as errors.
- [ ] No `null!` outside a tracked migration list.
- [ ] Domain data is a `record`; mutable services are `class`.
- [ ] Records use `required`/`init` so object initialisers must set the essentials.
- [ ] No `.Result`, `.Wait()`, or `GetAwaiter().GetResult()`.
- [ ] `ConfigureAwait(false)` in library code; `CancellationToken` threaded through every
      async public API.
- [ ] No `async void` except event handlers.
- [ ] DI lifetimes chosen deliberately; no captive dependencies.
- [ ] Configuration bound to typed options with `ValidateOnStart`.
- [ ] Queries are materialised when enumerated more than once.
- [ ] `dotnet test` runs in CI.

## Common mistakes

**`NullReferenceException` where the compiler said it could not be null.** The value was
`null!`-asserted, came from a dictionary miss, or came from a library that ignores
annotations. Check the declaration site, not the crash site.

**A deadlock in tests with `.Result`.** A sync-over-async on a captured context. Convert
the test to `async Task` and await.

**`InvalidOperationException: The collection was modified` in a LINQ query.** Something
mutated the underlying collection during enumeration. Materialise with `ToList()` first,
or snapshot.

**`ArgumentException: An item with the same key has already been added`** from `ToDictionary`
or `ToLookup`-then-`ToDictionary`. Use `ToLookup`, or supply a grouping or merge lambda.

**A singleton service capturing a scoped `DbContext`.** It keeps the context (and its
connection) alive and breaks thread safety. Register the singleton with a factory that
resolves `IServiceScopeFactory`, or make it scoped.

**`JsonException` on serialising a record with a positional constructor.** Configure the
serializer (`System.Text.Json` options) or use `[JsonConstructor]` where needed.

**Warnings not surfacing in CI.** `<TreatWarningsAsErrors>` is a project setting; a build
pipeline using `dotnet build` without it hides nullable warnings. Add
`/warnaserror` or the property, and check the CI logs.

**A `record` used where identity matters.** Record equality is structural; a mutable
entity in a set or dictionary will collide. Use a class for identity entities.

## Anti-patterns

- **`async void` outside event handlers** — exceptions cannot be caught, and the caller
  cannot await it.
- **Wrapping IO in `Task.Run`.** It adds a thread-pool hop and hides that the operation
  should have been awaited directly.
- **`.Result` anywhere**, "just for logging" included.
- **Registering one instance as a singleton for stateful, per-request data.**
- **`ToList()` inside a `foreach` that then iterates the list.** Materialise once.
- **Returning `IEnumerable<T>` from a method that re-queries a database each time**, so
  callers enumerate twice and pay twice.
- **Catching `Exception` and returning null.** It turns failures into silent wrong
  behaviour.
- **Business logic in controllers or endpoints.** Keep them as adapters over services.
- **Using `dynamic` where generics would do.**
- **Suppressing nullable warnings with `#nullable disable` around a whole file** instead
  of fixing the annotations.
- **Auto-properties on a class used as a mutable shared bag** instead of a record with
  explicit components.

## References

- Microsoft Learn
  - https://learn.microsoft.com/dotnet/
  - https://learn.microsoft.com/dotnet/csharp/
  - https://learn.microsoft.com/dotnet/csharp/language-reference/
  - https://learn.microsoft.com/dotnet/csharp/language-reference/nullable-reference-types
  - https://learn.microsoft.com/dotnet/csharp/language-reference/records
  - https://learn.microsoft.com/dotnet/standard/linq/
  - https://learn.microsoft.com/dotnet/csharp/asynchronous-programming/
  - https://learn.microsoft.com/dotnet/core/extensions/dependency-injection
  - https://learn.microsoft.com/aspnet/core/fundamentals/host/web-host
  - https://learn.microsoft.com/aspnet/core/fundamentals/minimal-apis/overview
  - https://learn.microsoft.com/aspnet/core/fundamentals/configuration/index
  - https://learn.microsoft.com/aspnet/core/fundamentals/logging/
  - https://learn.microsoft.com/dotnet/core/extensions/logging
  - https://learn.microsoft.com/dotnet/core/extensions/options
  - https://learn.microsoft.com/dotnet/core/extensions/httpclientfactory
- Testing
  - https://xunit.net/docs/getting-started
  - https://xunit.net/docs/getting-started/v3/index.html
- Runtime and CLI
  - https://learn.microsoft.com/dotnet/core/tools/dotnet
  - https://github.com/dotnet/runtime

For deeper reading:

- `references/nullable-and-records.md` - migration playbook for nullable enablement,
  record patterns, init/required, source-generated validation attributes.
- `references/async-and-dotnet-host.md` - async deep dive, task combinators, HttpClient
  resilience, DI lifetimes and testing the host.