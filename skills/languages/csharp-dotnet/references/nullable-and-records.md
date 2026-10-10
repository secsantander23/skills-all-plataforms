# Nullable Reference Types: Migration and Patterns

## Enabling it

```xml
<PropertyGroup>
  <TargetFramework>net8.0</TargetFramework>
  <Nullable>enable</Nullable>
  <ImplicitUsings>enable</ImplicitUsings>
  <LangVersion>latest</LangVersion>
  <AnalysisLevel>latest-recommended</AnalysisLevel>
</PropertyGroup>
```

`AnalysisLevel` turns on the recommended code-style and correctness analysers (for example
CA1062 null checks on public methods, CA1822 suggestion for `static`). They are advisory
unless promoted to errors, and you should read each one before promoting it.

Per-file opt-out for generated code:

```csharp
#nullable enable
#pragma warning disable CA1050 // declare types in namespaces
```
<!-- or -->
[ExcludeFromCodeCoverage]
[GeneratedCode("source-generator", "1.0")]
```

## Flow analysis, in practice

The compiler tracks the *flow state* of a nullable value through your code. Use that
instead of defensive checks everywhere.

```csharp
// After an is null check with return, the non-null branch is narrowed.
public int Length(string? s)
{
    if (s is null) return 0;
    return s.Length;      // s is non-null here
}

// After a truthiness check, the false branch is null-or-empty.
public string TrimOrEmpty(string? s) => (s is not null) ? s.Trim() : string.Empty;

// After calling a method annotated with NotNullWhen(false), the state carries over.
public bool TryGet(string key, [NotNullWhen(true)] out string? value)
{
    if (_store.TryGetValue(key, out var found))
    {
        value = found;
        return true;
    }
    value = null;
    return false;
}

if (dict.TryGetValue("id", out var id))
{
    Save(id);       // id is non-null here
}
```

Attributes that steer the compiler:

| Attribute | Effect |
| --- | --- |
| `[NotNullWhen(true)]` / `[NotNullWhen(false)]` | narrows on the other branch |
| `[MemberNotNull("Field")]` | asserts members are non-null when the method returns normally |
| `[MemberNotNullWhen(true, "Field")]` | same, conditional |
| `[AllowNull]` | lets a caller pass null even though the parameter is non-nullable |
| `[DisallowNull]` | forbids null on a nullable parameter |
| `[MaybeNull]` | a non-nullable return that may be null in some cases |
| `[NotNull]` | a nullable return that is non-null on success |

Use `[NotNullWhen]` and `[MaybeNull]` when writing interop wrappers; do not use them to
paper over a design where nullability is genuinely ambiguous.

## Common patterns

```csharp
// Disjunctive normal form: "both non-null" or "both null".
public sealed record Coord
{
    public Coord(int? x, int? y)
    {
        // Enforce the invariant at the boundary.
        if (x is null != y is null)
        {
            throw new ArgumentException("x and y must both be set or both be null.");
        }
        X = x;
        Y = y;
    }

    public int? X { get; }
    public int? Y { get; }
    public bool HasValue => X.HasValue;
}

// Null object: a total, no-op implementation.
public interface IAuditSink { void Record(string message); }

public sealed class NullAuditSink : IAuditSink
{
    public void Record(string message) { }
}

// Optional with a default, avoiding null checks at call sites.
public string Greet(User? user) => user is null
    ? "Hello, guest"
    : $"Hello, {user.Name}";

// Empty collection instead of null collections.
public IEnumerable<Order> Orders => _orders ?? Enumerable.Empty<Order>();
```

## Migration playbook

1. Set `<Nullable>enable</Nullable>` without warnings-as-errors. Collect the warnings.
2. Triage by category:
   - `CS8600` converting null literal to non-nullable: real, fix the type.
   - `CS8602` dereference of a possible null: add a guard or narrow the flow.
   - `CS8618` non-nullable field uninitialized: initialise it or mark it nullable.
   - `CS8603` possible null reference return: change the return type to `T?` or guarantee
     non-null.
   - `CS8619`/`CS8620` nullability of reference types in argument: signature mismatch.
3. Fix the design where the warnings cluster. A component with hundreds of `CS8602` wants
   `T?` in its signatures, not hundreds of null checks.
4. Search for `null!` and drive it to zero. Track it explicitly:

```bash
rg -n 'null!' --glob '*.cs'
rg -n '#nullable disable' --glob '*.cs'
```

5. Turn on `<TreatWarningsAsErrors>true</TreatWarningsAsErrors>` once the count is zero, or
   at least `<WarningsNotAsErrors>CS8618</WarningsNotAsErrors>` with an expiry date.

## Records, init, and required

```csharp
// Positional record with validation.
public sealed record Money
{
    public Money(decimal amount, string currency)
    {
        if (amount < 0) throw new ArgumentOutOfRangeException(nameof(amount));
        ArgumentException.ThrowIfNullOrWhiteSpace(currency);
        Amount = amount;
        Currency = currency.ToUpperInvariant();
    }

    public decimal Amount { get; }
    public string Currency { get; }
}

// Non-positional record with required members.
public sealed record CreateOrder
{
    public required string CustomerId { get; init; }
    public required IReadOnlyList<LineItem> Lines { get; init; }
    public string? Note { get; init; }
}
```

- `required` works with object initialisers only; the constructor path still exists for
  positional records.
- `init` allows assignment during construction and object initialisers, then the property
  is read-only. It is not thread-safe publication by itself; make the containing type
  immutable too.
- Records are shallowly immutable: a record with a `List<T>` component is not deeply
  immutable. Prefer `IReadOnlyList<T>` components or `ImmutableArray<T>`.

```csharp
// Deep immutability for a record with a collection component.
public sealed record Order(string Id, ImmutableArray<LineItem> Lines)
{
    public ImmutableArray<LineItem> WithLine(LineItem item) =>
        this with { Lines = Lines.Add(item) };
}
```

```csharp
public readonly record struct LineItem(string Sku, int Quantity, decimal UnitPrice)
{
    public decimal Total => UnitPrice * Quantity;
}
```

`readonly record struct` is the value-type counterpart: immutable, stack-allocated, with
structural equality. Use it for small value-like data (a money minor unit, a coordinate,
a size) where you want a `struct` layout.

## Interface variance and nullability

```csharp
// Covariant: IEnumerable<Derived> is assignable to IEnumerable<Base>.
// contravariant input: a method accepting Base accepts a caller with Derived.
public interface IRepository<out T>
{
    ValueTask<T?> FindAsync(string id, CancellationToken ct);
}

// Covariance rules forbid T appearing in a contravariant position, so
// Task<T?> as a return is fine but Task<T?> as an input parameter is not.
public interface IRepository2<in T>
{
    ValueTask AddAsync(T item, CancellationToken ct);
}
```

When an interface implementation does not match a nullability annotation, you get a
warning that is intentionally noisy: it means a caller compiled against one contract and
the implementation returns null. Fix the contract, not the warning.