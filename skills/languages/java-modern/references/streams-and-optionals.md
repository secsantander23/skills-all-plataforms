# Streams and Optional in Depth

## Terminal operations

| Operation | Returns | Notes |
| --- | --- | --- |
| `findFirst` | `Optional<T>` | order-dependent; short-circuits |
| `findAny` | `Optional<T>` | for parallel streams; may pick any element |
| `anyMatch` / `allMatch` / `noneMatch` | `boolean` | short-circuit |
| `count` | `long` | counts elements; often `.count()` beats `.collect(counting())` for clarity |
| `reduce(identity, op)` | `T` | no identity: `Optional<T>` |
| `collect(collector)` | varies | the general case |
| `min` / `max` | `Optional<T>` | needs a comparator |
| `toList()` | `List<T>` | Java 16+; unmodifiable |
| `forEach` / `forEachOrdered` | `void` | side effects |

`forEachOrdered` preserves encounter order even on a parallel stream, at a cost.

## Collectors

```java
import java.util.*;
import java.util.stream.Collectors;

record Sale(String region, String rep, BigDecimal amount, LocalDate day) {}

// Group and aggregate.
Map<String, BigDecimal> byRegion = sales.stream()
        .collect(Collectors.groupingBy(
                Sale::region,
                Collectors.reducing(BigDecimal.ZERO, Sale::amount, BigDecimal::add)));

// Multi-level grouping.
Map<String, Map<String, BigDecimal>> byRegionAndRep = sales.stream()
        .collect(Collectors.groupingBy(Sale::region,
                Collectors.groupingBy(Sale::rep,
                        Collectors.reducing(BigDecimal.ZERO, Sale::amount, BigDecimal::add))));

// toMap requires a merge function if keys can repeat.
Map<String, BigDecimal> repTotals = sales.stream()
        .collect(Collectors.toMap(Sale::rep, Sale::amount, BigDecimal::add));

// toMap with a default supplier and an explicitly mutable result.
Map<String, BigDecimal> modifiable = sales.stream()
        .collect(Collectors.toMap(
                Sale::rep, Sale::amount, (a, b) -> a,
                () -> new TreeMap<String, BigDecimal>(Comparator.reverseOrder())));

// Partitioning.
Map<Boolean, List<Sale>> bigAndSmall = sales.stream()
        .collect(Collectors.partitioningBy(s -> s.amount().signum() > 0));

// Summary statistics for numbers (IntStream/DoubleStream/LongStream).
DoubleSummaryStatistics stats = sales.stream()
        .map(Sale::amount)
        .mapToDouble(BigDecimal::doubleValue)
        .summaryStatistics();

// Summing in a specific way: joining with a collector.
String csv = sales.stream()
        .map(s -> s.rep() + "," + s.amount())
        .collect(Collectors.joining("\n", "rep,amount\n", ""));

// Tee-ing: feed two collectors with one pass.
Map<Boolean, List<Sale>> collectBoth = sales.stream()
        .collect(Collectors.collectingAndThen(
                Collectors.toList(),
                list -> {
                    Map<Boolean, List<Sale>> out = new HashMap<>();
                    list.forEach(s -> out
                            .computeIfAbsent(s.amount().signum() > 0, k -> new ArrayList<>())
                            .add(s));
                    return out;
                }));
```

`collectingAndThen` is the correct way to combine two reductions in one pass. Do not call
`collect` twice on the same stream: it is single-use, and re-creating the stream means
re-running the source work.

## FlatMap and stateful operations

```java
// Two levels.
List<String> words = sentences.stream()
        .flatMap(s -> Arrays.stream(s.split("\\s+")))
        .filter(w -> !w.isEmpty())
        .toList();

// Flattening maps.
Map<String, String> flat = Stream.of(Map.of("a", "1"), Map.of("b", "2"))
        .flatMap(m -> m.entrySet().stream())
        .collect(Collectors.toMap(Map.Entry::getKey, Map.Entry::getValue));

// flatMap with a null guard: Optional must not be a stream element.
List<String> names = users.stream()
        .map(User::nickname)
        .flatMap(Optional::stream)          // flattens Optional<T> -> Stream<T>, skips empty
        .toList();
```

`flatMap` on a `null` element throws `NullPointerException`; the stream pipeline does not
tolerate nulls. Use `Objects::nonNull` filters or `Optional.stream()`.

## Sorting and comparators

```java
sales.stream()
     .sorted(Comparator.comparing(Sale::region)
                      .thenComparing(Sale::day)
                      .thenComparing(Sale::amount, Comparator.reverseOrder()))
     .toList();

Comparator<Sale> byAmountThenRegion =
        Comparator.comparing(Sale::amount).thenComparing(Sale::region);
sales.sort(byAmountThenRegion);   // in-place; List.sort
```

`String.CASE_INSENSITIVE_ORDER` and `Comparator.naturalOrder()` handle the common cases.
Never write a comparator that returns `0` for different keys: sort is not stable with such
a comparator, and results become unpredictable.

## Performance notes

- `stream()` is cheap; the overhead is per-element lambda dispatch and boxing of primitives.
  For hot paths over primitives, use `IntStream`/`LongStream`/`DoubleStream` and avoid
  boxing.
- Streams on `ArrayList` with a sequential pipeline usually match a plain loop. They may be
  slower for tiny loops because of setup and megamorphic call sites.
- `parallel()` only pays when: the source is large and splittable (`ArrayList`, arrays),
  per-element work is substantial and CPU-bound, and the pipeline is stateless. Otherwise
  it adds overhead.
- `parallel()` uses the common `ForkJoinPool` shared by the whole JVM. Blocking inside it
  starves unrelated code in the same JVM.
- `LinkedList` is a poor stream source; its splittability is poor and traversal through the
  iterator is slower than indexing.
- Pre-size collectors when the size is known: `collect(toList())` on a known-size source
  still grows the list.

## Optional combinators

```java
Optional<String> findName(long id) {
    return Optional.ofNullable(db.get(id)).map(User::name);
}

// map: Optional<T> -> Optional<R>
// flatMap: Optional<T> -> Optional<R> where the mapper returns Optional<R>
Optional<String> ownerName = order
        .flatMap(o -> findName(o.customerId()));   // Optional<Order> -> Optional<String>

Optional<User> active = user.filter(u -> u.active());

// or: eagerly evaluates the default; orElseGet: lazily.
String name = findName(id).orElse("unknown");
String lazy = findName(id).orElseGet(() -> expensiveDefault());

// ifPresentOrElse for the branch.
findName(id).ifPresentOrElse(
        n -> log.info("user {}", n),
        () -> log.info("no user for {}", id));

// Optional<Void> for a "present but no value" result.
Optional<Void> deleted = deleteIfExists(id);
deleted.ifPresent(v -> log.info("deleted {}", id));

// Nesting Optional<Optional<T>> is a design smell; flatten with flatMap instead.
```

Anti-patterns:

- `Optional` as a field (breaks equality and serialisation assumptions).
- `Optional` as a parameter (the caller must decide what "absent" means).
- `Optional.orElse(new ExpensiveThing())` (eager allocation on the happy path).
- `map(o -> o.getName().orElse("x"))` producing `Optional<Optional<String>>`.
- `if (optional.isPresent()) { optional.get(); }` instead of `ifPresent`.
- Streams returning `Optional` where `null` was clearer in a private helper.

## Records, patterns, and switch

```java
sealed interface Shape permits Circle, Rect, Point {}

record Circle(double radius) implements Shape {}
record Rect(double width, double height) implements Shape {}
record Point(double x, double y) implements Shape {}

double area(Shape shape) {
    // Java 21 pattern matching for switch, exhaustively checked over a sealed type.
    return switch (shape) {
        case Circle c -> Math.PI * c.radius() * c.radius();
        case Rect r -> r.width() * r.height();
        case Point p -> 0.0;
    };
}
```

Record deconstruction patterns and guarded patterns appeared after the initial switch
pattern support and went through preview stages. Check the current JDK release notes and
the JEP index for what is standard in your target release rather than assuming.

Without switch patterns, the equivalent `instanceof` chain is:

```java
if (shape instanceof Circle c && c.radius() > 0) {
    return Math.PI * c.radius() * c.radius();
} else if (shape instanceof Rect r) {
    return r.width() * r.height();
} else {
    return 0.0;   // Point, or a non-sealed subtype
}
```

Adding a permitted subtype without updating an exhaustive `switch` is a compile error;
with the `instanceof` chain, a `default` branch means you lose that check. That difference
is the main argument for sealed types plus switch patterns.