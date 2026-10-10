# async and the .NET Host

## The state machine, in one paragraph

An `async` method runs synchronously until it hits the first incomplete `await`. At that
point the method returns to its caller, and the rest of the method body is suspended into a
state machine. When the awaited task completes, the continuation is scheduled: either on
the current `SynchronizationContext` (if one was captured and is still current), or on the
thread pool. `.Result` and `.Wait()` block the calling thread, which is a deadlock when the
calling thread is the one the continuation needs.

```csharp
// Deadlock shape: sync-over-async on a single-threaded context (UI, or a sync test).
public string GetData() => LoadAsync().Result;

private async Task<string> LoadAsync() => await HttpClient.GetStringAsync(Url);
```

```csharp
// Fixed.
public async Task<string> GetDataAsync(CancellationToken ct)
    => await LoadAsync(ct).ConfigureAwait(false);

private async Task<string> LoadAsync(CancellationToken ct)
    => await HttpClient.GetStringAsync(Url, ct).ConfigureAwait(false);
```

In a library, `ConfigureAwait(false)` on every await is the convention: it says "I do not
need to return to the caller's context". In application code it is optional because ASP.NET
Core has no ambient context, but adding it is harmless and keeps intent clear.

## Task combinators

```csharp
// WhenAll: run concurrently, wait for all, throw on the first failure.
var all = await Task.WhenAll(a, b, c);

// WhenAny: first to complete wins (used for timeouts and hedging).
var first = await Task.WhenAny(primary, Task.Delay(TimeSpan.FromSeconds(2), ct));
if (first == primary)
{
    return await primary;
}

// Timeout wrapper.
private static async Task<T> WithTimeout<T>(Task<T> task, TimeSpan timeout, CancellationToken ct)
{
    var delay = Task.Delay(timeout, ct);
    var winner = await Task.WhenAny(task, delay);
    if (winner != task)
    {
        throw new TimeoutException($"Operation exceeded {timeout}.");
    }

    return await task;
}

// Run several operations, tolerate individual failures.
async Task<Outcome> TryAsync(Func<Task<Outcome>> op, CancellationToken ct)
{
    try
    {
        return await op();
    }
    catch (OperationCanceledException) when (ct.IsCancellationRequested)
    {
        throw;      // caller cancelled: propagate
    }
    catch (Exception ex)
    {
        return Outcome.Failed(ex.Message);
    }
}

var outcomes = await Task.WhenAll(ids.Select(i => TryAsync(() => LoadAsync(i, ct), ct)));
```

`Task.Run` moves work to the thread pool. Use it for genuinely CPU-bound work in an
application; never as a wrapper around IO that is already asynchronous.

## IHttpClientFactory

```csharp
using System.Net.Http.Json;
using Microsoft.Extensions.Options;

public sealed record GatewayOptions
{
    public const string SectionName = "Gateway";

    [Required, Url]
    public string BaseUrl { get; init; } = string.Empty;

    [Range(1, 300)]
    public int TimeoutSeconds { get; init; } = 30;
}

public sealed class HttpPaymentGateway(
    HttpClient client,
    IOptions<GatewayOptions> options,
    ILogger<HttpPaymentGateway> logger) : IPaymentGateway
{
    private readonly GatewayOptions _options = options.Value;

    public async Task<string> AuthorizeAsync(Money amount, CancellationToken ct)
    {
        ArgumentNullException.ThrowIfNull(amount);

        using var response = await client
            .PostAsJsonAsync("/authorize", amount, ct)
            .ConfigureAwait(false);

        response.EnsureSuccessStatusCode();

        var reference = await response.Content
            .ReadFromJsonAsync<string>(cancellationToken: ct)
            .ConfigureAwait(false);

        return reference
               ?? throw new InvalidOperationException("Gateway returned no reference.");
    }
}
```

Registration:

```csharp
builder.Services
    .AddOptions<GatewayOptions>()
    .Bind(builder.Configuration.GetSection(GatewayOptions.SectionName))
    .ValidateDataAnnotations()
    .ValidateOnStart();

builder.Services.AddHttpClient<IPaymentGateway, HttpPaymentGateway>((sp, client) =>
{
    var o = sp.GetRequiredService<IOptions<GatewayOptions>>().Value;
    client.BaseAddress = new Uri(o.BaseUrl, UriKind.Absolute);
    client.Timeout = TimeSpan.FromSeconds(o.TimeoutSeconds);
    client.DefaultRequestHeaders.Add("X-Client", "acme-billing");
})
.AddStandardResilienceHandler();   // retry/circuit breaker; requires Microsoft.Extensions.Http.Resilience
```

The factory pools `HttpMessageHandler` instances and rotates the `HttpClient` objects, so
you never see the socket exhaustion caused by `new HttpClient()` per request.

`ValidateOnStart` fails the host at startup rather than on the first request that reads a
bad option. Prefer it for anything that can fail fast.

## DI lifetimes

| Lifetime | Instances | Thread safe | Use for |
| --- | --- | --- | --- |
| Singleton | one per application | must be | stateless services, caches, `HttpClient`-backed factories |
| Scoped | one per request/scope | no | `DbContext`, per-request repositories |
| Transient | one per resolution | no | cheap stateless helpers |

```csharp
// Captive dependency: the singleton keeps the scoped service (and its DbContext)
// alive for the life of the application, and uses it from many threads.
builder.Services.AddSingleton<PaymentService>();   // WRONG if PaymentService depends on DbContext

// Correct: make it scoped.
builder.Services.AddScoped<PaymentService>();
```

Resolving from the root provider inside a singleton is the same problem:

```csharp
// Captive: resolve a scoped service from the root.
public sealed class BackgroundWorker(IServiceProvider root)
{
    public void Run()
    {
        using var scope = root.CreateScope();      // explicit scope fixes it
        var svc = scope.ServiceProvider.GetRequiredService<PaymentService>();
    }
}
```

## Configuration

Sources, in increasing precedence: JSON files, user secrets (development), environment
variables, command-line arguments. `WebApplication.CreateBuilder(args)` wires all of them.

```jsonc
// appsettings.json
{
  "Logging": { "LogLevel": { "Default": "Information", "Acme": "Debug" } },
  "Gateway": { "BaseUrl": "https://sandbox.example.com/", "TimeoutSeconds": 15 }
}
```

```bash
# Environment variables use __ (double underscore) for nesting.
export Acme__Billing__Gateway__TimeoutSeconds=5

# Or read a nested section directly:
string? url = builder.Configuration["Gateway:BaseUrl"];
```

Secrets: never commit them. Use user secrets in development, environment variables or a
secret manager in production, and validate presence at startup so a missing secret is a
startup failure, not a 3 a.m. page.

## Testing the host

```csharp
using Microsoft.AspNetCore.Mvc.Testing;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

public sealed class PaymentEndpointTests(WebApplicationFactory<Program> factory)
    : IClassFixture<WebApplicationFactory<Program>>
{
    private readonly HttpClient _client = factory.CreateClient();

    [Fact]
    public async Task Get_unknown_payment_returns_404()
    {
        using var response = await _client.GetAsync("/payments/missing");

        Assert.Equal(HttpStatusCode.NotFound, response.StatusCode);
    }
}

// Program must be visible to the test assembly.
[assembly: InternalsVisibleTo("Acme.Tests")]
```

Replace services in tests without touching production code:

```csharp
public sealed class TestFactory : WebApplicationFactory<Program>
{
    protected override void ConfigureWebHost(IWebHostBuilder builder)
    {
        builder.ConfigureServices(services =>
        {
            services.RemoveAll<IPaymentRepository>();
            services.AddScoped<IPaymentRepository, InMemoryPaymentRepository>();
        });
    }
}
```

`Program` must be declared with an explicit accessibility modifier (for example
`public partial class Program { }`) for `WebApplicationFactory<Program>` to reference it.
Confirm the required attribute-based alternative in the current ASP.NET Core docs for the
testing package you use.

## Checklist for async code

- No `.Result`, `.Wait()`, `GetAwaiter().GetResult()`.
- Every async method takes a `CancellationToken` and passes it down.
- `async Task<T>` everywhere except event handlers (`async void`).
- `ConfigureAwait(false)` in library code.
- Cancellation exceptions (`OperationCanceledException`) handled or allowed to propagate;
  never swallowed and never converted into a generic failure.
- Exceptions observed: `await` every task you create, or observe `task.Exception` when you
  intentionally fire and forget.