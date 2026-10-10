---
name: Modern Java
description: Covers records, sealed interfaces, the Stream API, Optional discipline, var and generics, concurrency with executors and CompletableFuture, virtual threads and when they help, module system basics, Maven and Gradle build files, and JUnit 5 testing. Use when writing or reviewing Java, when choosing between records and classes, when refactoring imperative loops into streams, when sizing thread pools, or when setting up a new Maven or Gradle project.
metadata:
  opencode/autoinvoke: true
---

# Modern Java

Modern Java reduces boilerplate without hiding the machine: records remove field/accessor
noise, sealed types make exhaustive dispatch possible, streams express transformation
without `Iterator` scaffolding, and virtual threads (where available) make thread-per-task
a cheap strategy again. The discipline underneath all of them is the same: model state so
the compiler can check transitions, push IO off the platform threads, and keep side effects
at the edges.

## When to use this

- The user is writing a `record`, a `sealed` type hierarchy, or asking whether to use one.
- The user is refactoring loops into the Stream API or debugging a stream that is
  accidentally sequential or quadratic.
- The user is using `Optional` and wants to know the right way.
- The user is configuring a thread pool, using `CompletableFuture`, or deciding whether
  virtual threads help their workload.
- The user is setting up `pom.xml` or `build.gradle` and needs the minimal correct shape.
- The user is writing JUnit 5 tests and needs the parameterised and assertion idioms.
- The user is facing a `NullPointerException` and wants a null-safety pattern.

## Quick start

A sealed result type, exhaustive handling, and a record that owns its invariants:

```java
package com.example.orders;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.Objects;
import java.util.Optional;

sealed interface Payment permits Payment.Charged, Payment.Declined, Payment.Expired {

    String reference();

    record Charged(String reference, BigDecimal amount, Instant at) implements Payment {
        public Charged {
            Objects.requireNonNull(reference, "reference");
            Objects.requireNonNull(amount, "amount");
            Objects.requireNonNull(at, "at");
            if (reference.isBlank()) {
                throw new IllegalArgumentException("reference must not be blank");
            }
        }
    }

    record Declined(String reference, String reason) implements Payment {
        public Declined {
            Objects.requireNonNull(reason, "reason");
        }
    }

    record Expired(String reference, Instant expiredAt) implements Payment {}
}

// OrderTotals lives in its own file in a real project; it is shown here
// alongside the type it operates on.
final class OrderTotals {

    private OrderTotals() {
        throw new AssertionError("utility class");
    }

    public static BigDecimal of(List<Payment> payments) {
        return payments.stream()
                .filter(Payment.Charged.class::isInstance)
                .map(Payment.Charged.class::cast)
                .map(Payment.Charged::amount)
                .reduce(BigDecimal.ZERO, BigDecimal::add);
    }

    public static String describe(Payment payment) {
        if (payment instanceof Payment.Charged charged) {
            return "charged " + charged.amount() + " at " + charged.at();
        } else if (payment instanceof Payment.Declined declined) {
            return "declined: " + declined.reason();
        } else {
            Payment.Expired expired = (Payment.Expired) payment;
            return "expired at " + expired.expiredAt();
        }
    }

    public static Optional<String> firstFailure(List<Payment> payments) {
        return payments.stream()
                .filter(p -> p instanceof Payment.Declined)
                .map(p -> ((Payment.Declined) p).reason())
                .findFirst();
    }
}
```

Note the compact constructor in each record: it runs as part of construction, so invariants
hold for every instance including deserialised ones. `sealed` plus `instanceof` gives
exhaustive handling without a `switch`, and `switch` patterns over a sealed hierarchy are
available in recent JDKs (verify for your target).

## Records and sealed types

| Need | Use |
| --- | --- |
| immutable data holder with value equality | `record` |
| a closed hierarchy where callers must handle every variant | `sealed interface` / `sealed class` |
| a behaviour with mutable state | a normal class |
| a singleton with no data | an `enum`, or an empty record |

```java
// Nested records and enums are implicitly static and final.
public record Money(BigDecimal amount, String currency) {
    public Money {
        Objects.requireNonNull(amount);
        Objects.requireNonNull(currency);
        if (amount.signum() < 0) {
            throw new IllegalArgumentException("amount must be non-negative");
        }
    }

    public Money plus(Money other) {
        if (!currency.equals(other.currency)) {
            throw new IllegalArgumentException("currency mismatch");
        }
        return new Money(amount.add(other.amount), currency);
    }
}
```

Records give you: `equals`/`hashCode` on all components, `toString`, accessor methods
named exactly like the component (no `get` prefix), and a canonical constructor. They are
final and their fields are final, so they are thread-safe if their components are.

Records do **not** give you: `equals` semantics you can override independently of
components, extension, or mutable state. The moment you need those, use a class.

```java
// A sealed hierarchy with a shared default behaviour and a permitted subclass list.
public sealed interface Event permits OrderPlaced, OrderShipped, OrderCancelled {

    String id();

    Instant occurredAt();

    default Event withOccurredAt(Instant when) {
        return switch (this) {
            case OrderPlaced e -> new OrderPlaced(e.id(), when);
            case OrderShipped e -> new OrderShipped(e.id(), when);
            case OrderCancelled e -> new OrderCancelled(e.id(), when, e.reason());
        };
    }

    record OrderPlaced(String id, Instant occurredAt) implements Event {
        public OrderPlaced {
            Objects.requireNonNull(id);
            Objects.requireNonNull(occurredAt);
        }
    }

    record OrderShipped(String id, Instant occurredAt) implements Event {
        public OrderShipped {
            Objects.requireNonNull(id);
            Objects.requireNonNull(occurredAt);
        }
    }

    record OrderCancelled(String id, Instant occurredAt, String reason) implements Event {
        public OrderCancelled {
            Objects.requireNonNull(id);
            Objects.requireNonNull(occurredAt);
            Objects.requireNonNull(reason);
        }
    }
}
```

`sealed` requires the permitted subclasses to be in the same module (or package) and to
declare themselves `final`, `sealed`, or `non-sealed`. Sealed types plus pattern matching
give you compile-time exhaustiveness: adding a permitted subtype breaks the switch at
compile time.

Use `non-sealed` only when you genuinely need a third party to extend, and document that
your `switch` must then have a `default`.

## Streams: when and how

A stream is a pipeline over a source. Rules that keep streams readable and fast:

1. Streams are for transforming and aggregating collections. Keep side effects out of them.
2. Streams are **single-use** and **lazy**: nothing runs until a terminal operation, and a
   terminal operation consumes the stream.
3. Streams are sequential unless you call `parallel()`. `parallel()` is sequential when
   the source is not splittable, when the pipeline has blocking operations, or when the
   work per element is tiny.
4. `Collectors.toList()` returns a mutable list; `toUnmodifiableList()` returns one that
   rejects mutation. Choose deliberately.

```java
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

record Order(String id, String customerId, BigDecimal total, Status status, Instant placedAt) {}

enum Status { NEW, PAID, SHIPPED, CANCELLED }

// Group by customer and sum.
Map<String, BigDecimal> byCustomer = orders.stream()
        .filter(o -> o.status() != Status.CANCELLED)
        .collect(Collectors.groupingBy(
                Order::customerId,
                Collectors.reducing(BigDecimal.ZERO, Order::total, BigDecimal::add)));

// Count with a Map<Status, Long>.
Map<Status, Long> counts = orders.stream()
        .collect(Collectors.groupingBy(Order::status, Collectors.counting()));

// Distinct customer ids, sorted, as an immutable list.
List<String> customers = orders.stream()
        .map(Order::customerId)
        .distinct()
        .sorted()
        .toList();

// Side effects belong here, not in the pipeline.
Map<String, BigDecimal> mutable = orders.stream()
        .collect(Collectors.toMap(Order::id, Order::total, (a, b) -> a, () -> new HashMap<>()));

// Flatten nested collections.
record TaggedOrder(String id, List<String> tags) {}

Set<String> allTags = List.of(new TaggedOrder("o1", List.of("a", "b")),
                              new TaggedOrder("o2", List.of("b", "c")))
        .stream()
        .flatMap(o -> o.tags().stream())
        .collect(Collectors.toSet());

// toMap blows up on duplicate keys unless you supply a merge function.
// Use the four-argument form above whenever keys can repeat.
```

Parallel streams and their traps:

```java
// Usually a pessimisation: the work per element is trivial and the source is small.
orders.parallelStream().map(Order::id).toList();

// Only worth measuring: substantial CPU work per element, large source, no shared state.
orders.parallelStream()
        .map(Order::total)
        .filter(amount -> amount.signum() > 0)
        .reduce(BigDecimal.ZERO, BigDecimal::add);
```

- `forEach` with a mutating collector on a parallel stream needs a **concurrent**
  collector (`Collectors.toConcurrentMap`) or a concurrent collection.
- Blocking operations inside a parallel stream occupy the common `ForkJoinPool`, which is
  shared by the whole JVM. Never block in a parallel stream.
- Order is not preserved in a parallel stream for unordered operations. If you call
  `toList()` on a parallel stream, the size is right but the *encounter order* is not
  guaranteed; sort explicitly if order matters.
- Measure before and after. `parallel()` is a hint, not a speedup.

A plain `for` loop is better than a stream when: the body is three statements, the loop
mutates external state, you need early `break`/`continue`, or the code is on a hot path
where the lambda allocation matters. Streams compose; loops compose the same way. Do not
convert a loop that reads fine.

## Optional discipline

`Optional` is a *return type*, not a field type and not a parameter type.

```java
public Optional<User> findUser(String id) {
    return users.get(id);
}

public String displayNameOr(String id, String fallback) {
    return findUser(id).map(User::displayName).orElse(fallback);
}

public String requireName(String id) {
    return findUser(id).map(User::displayName).orElseThrow(
            () -> new EntityNotFound("user " + id));
}
```

Use `map`, `flatMap`, `filter`, `ifPresentOrElse`, `orElseGet`. Do not call
`Optional.get()`; the compiler forces you to write `orElse`/`orElseThrow` instead, which
is the point. `orElse(default)` evaluates the default eagerly; use `orElseGet(supplier)`
when constructing the default costs something.

```java
// Not recommended: a field of type Optional<User>.
class Cached {
    private Optional<User> user = Optional.empty();   // serialisation and equality pain
}

// Prefer a nullable field with a null-tolerant accessor, or an explicit empty subtype.
```

## var and generics

`var` is for local variables when the type is obvious from the right-hand side. It is not
available for fields, parameters, or return types.

```java
var users = new HashMap<String, User>();       // good: the type is on the constructor
var orderId = 42L;                            // fine but low value
User u = find(id).orElseThrow();              // keep the explicit type when it matters
```

Rules for `var`:
- Good: `var list = new ArrayList<Order>();`, `var entry = map.entry(key);`, with `try`
  or `try`-with-resources where the type is the point.
- Bad: `var x = getSomething();` when the return type is `Object` or a generic inferred as
  `Object`; you lose the type.
- Chains: `var result = list.stream().filter(...).collect(...)` infers the collected type,
  which is often not what you want. Spell it out.

Generics: use wildcards on parameters for flexibility, and exact types on returns for
usefulness.

```java
// Wildcards: callers may pass any subtype, and you never return a mutable alias.
static double total(Collection<? extends Order> orders) {
    return orders.stream().mapToDouble(Order::amount).sum();
}

// Copy into a concrete type before mutating, or it fails at compile time.
static List<Order> sortedByAmount(List<Order> in) {
    List<Order> copy = new ArrayList<>(in);
    copy.sort(Comparator.comparing(Order::amount));
    return List.copyOf(copy);
}
```

## Concurrency

### Platform threads and executors

An `ExecutorService` with a fixed pool is the default for CPU-bound or moderate workloads.
Size it deliberately: for CPU-bound work, roughly the number of available processors; for
IO-bound work, more, bounded by what the downstream can absorb.

```java
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.Callable;

ExecutorService pool = Executors.newFixedThreadPool(
        Math.max(2, Runtime.getRuntime().availableProcessors()));

try (var shutdownPool = pool) {          // ExecutorService implements AutoCloseable
    pool.submit(() -> doWork());
    pool.shutdown();                     // no new tasks accepted
    if (!pool.awaitTermination(30, TimeUnit.SECONDS)) {
        pool.shutdownNow();              // interrupt running tasks
    }
}
```

An unbounded cached or fixed-at-max pool is a denial-of-service vector against yourself.
Always bound concurrency at a level the system can absorb, and always shut the pool down.

Never create an executor per request. Executors own threads and, in older JDKs, can leak
them via their `ThreadLocal` support; a shared, bounded pool is the safe pattern.

### CompletableFuture

```java
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ExecutionException;
import java.util.List;

CompletableFuture<User> user = loadUser(id);
CompletableFuture<List<Order>> orders = loadOrders(id);

record Dashboard(User user, List<Order> orders) {}

CompletableFuture<Dashboard> both = user.thenCombine(orders, Dashboard::new);

CompletableFuture<String> name = user
        .thenApply(User::displayName)
        .thenCompose(n -> lookupProfile(n))          // thenCompose for a nested future
        .exceptionally(err -> "anonymous: " + err.getMessage())
        .toCompletableFuture();

try {
    Dashboard d = both.get(5, TimeUnit.SECONDS);
} catch (TimeoutException e) {
    // The future is still running; cancel it.
    both.cancel(true);
} catch (ExecutionException e) {
    throw new IllegalStateException(e.getCause());
}

// allOf waits for all; then join each.
CompletableFuture<Void> all = CompletableFuture.allOf(
        loadUser(id), loadOrders(id), loadPrefs(id));
all.thenRun(() -> notifyReady()).join();
```

Rules:
- `thenApply` for synchronous transformation, `thenCompose` for a transformation that
  returns another future (otherwise you get `CompletableFuture<CompletableFuture<T>>`).
- A `CompletableFuture` runs its dependent stages on the **completing thread's** pool,
  unless you supply an explicit `Executor` to the `*Async` variants. That is the classic
  source of "my callback ran on an HTTP worker thread and blocked it".
- Always pass an explicit executor to the `*Async` methods when the body might block.
- `supplyAsync` vs `runAsync`: `supplyAsync` takes a `Supplier<T>` (returns a value),
  `runAsync` takes a `Runnable` (returns nothing).
- Failures complete the future exceptionally. Chain `exceptionally` or `handle` at the
  point of failure, not at the top of the pipeline.
- Cancellation is cooperative: `cancel(true)` interrupts the worker; a task that ignores
  interrupts keeps running.

### Virtual threads

Virtual threads (Project Loom) are lightweight, scheduled threads managed by the JVM rather
than the operating system. You create them with the same `Thread` API or through
`Executors.newVirtualThreadPerTaskExecutor()`, and they are *not* pooled: create one per
task.

```java
import java.util.concurrent.Executors;

try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
    List<java.util.concurrent.Future<String>> futures = urls.stream()
            .map(url -> executor.submit(() -> fetch(url)))
            .toList();
    for (var future : futures) {
        System.out.println(future.get());
    }
}   // the executor's close() waits for the tasks
```

They help when a workload is **IO-bound and blocking** with high concurrency and little
CPU per task: thousands of simultaneous blocking calls where platform threads would be too
expensive to pool. They do not help CPU-bound work, where the useful parallelism is still
the number of processors, and they do not remove the need for pooling in the libraries you
call (a blocking call on a virtual thread still consumes a platform thread in some
situations, and connection pools still limit throughput).

Availability matters: virtual threads shipped as a feature after being previewed, and they
are permanent, but related features such as structured concurrency and scoped values have
moved through preview stages with names and APIs changing between them. Check the current
JDK release notes and the JEP index before depending on anything beyond the basic virtual
thread API, and do not assume a preview feature is available in your target release.

## Null safety patterns

There is no built-in null safety. The reliable strategies:

| Strategy | How | Cost |
| --- | --- | --- |
| Nullability annotations + static analysis | annotate parameters/fields, run the checker in the build | build-time dependency |
| `Optional` returns | the method says "may be absent" | allocations; field usage discouraged |
| Null object pattern | a default value that behaves like the real one | must be total |
| Null-aware utilities | `Objects.requireNonNullElse`, `getOrDefault`, maps that reject nulls | cheap |
| `Objects.requireNonNull` at boundaries | fail fast on programmer error | none |

```java
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.Map.Entry;

public final class Config {
    private final Map<String, String> values;

    private Config(Map<String, String> values) {
        this.values = Map.copyOf(values);   // rejects null keys and null values
    }

    public static Config of(Map<String, String> raw) {
        Objects.requireNonNull(raw, "raw config");
        return new Config(raw);
    }

    public String require(String key) {
        return Optional.ofNullable(values.get(key))
                .orElseThrow(() -> new IllegalArgumentException("missing config key: " + key));
    }

    public String orDefault(String key, String fallback) {
        return values.getOrDefault(key, fallback);
    }
}
```

`Map.copyOf`, `Set.copyOf`, and `List.copyOf` reject null elements, which turns a
null-dereference bug into an immediate, local failure. `List.of` and `Map.of` do the same
at construction. That is the cheapest null-safety tool in the language.

## Modules

The module system (`module-info.java`) gives you explicit dependencies, strong encapsulation
of `internal` packages, and reliable classpath-free deployment.

```java
// src/main/java/module-info.java
module com.example.billing {
    requires java.sql;                 // another module
    requires transitive java.logging; // also visible to modules that require us
    requires static com.fasterxml.jackson.databind; // optional at runtime

    exports com.example.billing.api;         // public API, reflectively accessible
    exports com.example.billing.spi to com.example.web; // qualified export

    opens com.example.billing.model;         // deep reflection (DI frameworks need this)

    uses com.example.billing.spi.PaymentPlugin;
    provides com.example.billing.spi.PaymentPlugin
            with com.example.billing.card.CardPlugin;
}
```

Rules worth remembering:

- The default access level is *everything public to nobody*: an exported package is public
  API; a package that is not exported is private to the module even if its classes are
  `public`.
- `requires transitive` exposes a dependency to consumers; use it sparingly, because it is
  part of your API contract.
- `opens` is for reflection (dependency injection, serialization, JSON mapping). It is not
  the same as `exports`.
- Split packages (the same package in two modules) are not allowed, which is why
  multi-release and legacy layouts sometimes need `--patch-module` or an automatic module
  (a plain jar on the module path with no `module-info`).
- Most applications do not need modules. Adopt them for libraries you publish and for
  applications where you want enforced boundaries.

## Build tools

### Maven

```xml
<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
  <modelVersion>4.0.0</modelVersion>

  <groupId>com.example</groupId>
  <artifactId>billing</artifactId>
  <version>1.2.0</version>
  <packaging>jar</packaging>

  <properties>
    <maven.compiler.release>21</maven.compiler.release>
    <project.build.sourceEncoding>UTF-8</project.build.sourceEncoding>
    <junit.version>5.10.2</junit.version>
  </properties>

  <dependencies>
    <dependency>
      <groupId>org.junit.jupiter</groupId>
      <artifactId>junit-jupiter</artifactId>
      <version>${junit.version}</version>
      <scope>test</scope>
    </dependency>
  </dependencies>

  <build>
    <plugins>
      <plugin>
        <groupId>org.apache.maven.plugins</groupId>
        <artifactId>maven-surefire-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>
```

`maven.compiler.release` is the important property: it sets both the language level and the
API surface, so code cannot accidentally use a newer API than it declares. Common commands:
`mvn verify`, `mvn -q test`, `mvn dependency:tree`, `mvn -o` (offline). Verify plugin
versions against the Maven documentation; pinning nothing means your build silently
changes under you.

### Gradle

```kotlin
// build.gradle.kts
plugins {
    java
}

group = "com.example"
version = "1.2.0"

java {
    toolchain {
        languageVersion = JavaLanguageVersion.of(21)
    }
}

repositories {
    mavenCentral()
}

dependencies {
    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher")
}

tasks.test {
    useJUnitPlatform()
}
```

Common commands: `./gradlew build`, `./gradlew test`, `./gradlew dependencies`. Use the
wrapper (`gradlew`) so everyone uses the same Gradle version. The `toolchain` block
downloads and uses a matching JDK rather than whatever is on the path, which is the
single most valuable line in a Gradle build file.

Common mistakes: setting `sourceCompatibility` without `targetCompatibility`; mixing `api`
and `implementation` inconsistently so consumers see different classes than you compile
against; and using the system JDK in CI while developers use the toolchain.

## Testing with JUnit 5

```java
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Nested;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.MethodSource;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import java.util.stream.Stream;

class OrderTotalsTest {

    private List<Payment> payments;

    @BeforeEach
    void setUp() {
        payments = List.of(
                new Payment.Charged("r1", new BigDecimal("10.00"), Instant.EPOCH),
                new Payment.Declined("r2", "insufficient funds"),
                new Payment.Charged("r3", new BigDecimal("5.50"), Instant.EPOCH));
    }

    @Test
    @DisplayName("sums only charged payments")
    void sumsCharged() {
        assertEquals(new BigDecimal("15.50"), OrderTotals.of(payments));
    }

    @Test
    void recordInvariantsHold() {
        assertThrows(IllegalArgumentException.class,
                () -> new Payment.Charged("", new BigDecimal("1"), Instant.EPOCH));
    }

    @ParameterizedTest
    @CsvSource({
            "10.00, 3.00, 13.00",
            "0.00,  0.00, 0.00",
            "7.25,  2.75, 10.00",
    })
    void moneyAddition(String a, String b, String expected) {
        Money left = new Money(new BigDecimal(a), "EUR");
        Money right = new Money(new BigDecimal(b), "EUR");
        assertEquals(new BigDecimal(expected), left.plus(right).amount());
    }

    static Stream<Payment> chargedOnly() {
        return Stream.of(
                new Payment.Charged("r1", BigDecimal.ONE, Instant.EPOCH),
                new Payment.Declined("r2", "nope"));
    }

    @ParameterizedTest
    @MethodSource("chargedOnly")
    void describeNeverThrows(Payment payment) {
        assertTrue(OrderTotals.describe(payment).startsWith("charged")
                || OrderTotals.describe(payment).startsWith("declined"));
    }

    @Nested
    class FailurePaths {
        @Test
        void emptyListSumsToZero() {
            assertEquals(BigDecimal.ZERO, OrderTotals.of(List.of()));
        }
    }
}
```

Idioms:
- `@Nested` classes group related tests and share setup naturally.
- `@MethodSource` feeds a stream of typed arguments; `@CsvSource` is for scalars.
- `@DisplayName` documents intent in the test report.
- Assert on behaviour through public methods, not on private fields.
- For time, inject a `Clock` so tests do not depend on wall-clock time.
- Concurrency tests should assert on latches and timeouts, never `Thread.sleep`.
- Run the build's test task (`mvn verify`, `./gradlew test`) in CI; a green IDE run is not
  a build.

## Checklist

- [ ] Data holders are `record`s with invariants enforced in the compact constructor.
- [ ] Closed hierarchies are `sealed` with every variant handled exhaustively.
- [ ] No `Optional` fields or parameters; `Optional` only as a return type.
- [ ] No `Optional.get()`; `orElseGet` for expensive defaults.
- [ ] Streams contain no side effects; side effects happen in the terminal collector or
      after.
- [ ] Streams are only `parallel()` where a measurement shows it wins.
- [ ] Thread pools are shared, bounded, and shut down.
- [ ] `CompletableFuture` callbacks that may block use the `*Async` variants with an
      explicit executor.
- [ ] Immutable collections (`List.of`, `Map.copyOf`) are used where nothing should mutate.
- [ ] `Objects.requireNonNull` at public API boundaries.
- [ ] `mvn verify` / `./gradlew build` runs in CI.

## Common mistakes

**`NullPointerException` deep in a chain.** A library returned `null` where you assumed a
value, or an unboxing of a null wrapper. Make the null case explicit with `Optional` or a
null object, and validate at the boundary.

**`IllegalStateException: No value present`** from `Optional.get()`. Use `orElseThrow`
with a message that identifies the missing key.

**`UnsupportedOperationException` on a stream result.** `toList()`/`Collectors.toList()`
may return an unmodifiable list; build a mutable `ArrayList` explicitly when you need to
mutate it.

**`ConcurrentModificationException`.** You mutated a collection inside a stream pipeline
(or `forEach`) while it was being traversed. Collect into a new list, or use a concurrent
structure and understand the guarantees.

**`OutOfMemoryError: Java heap space` from `Collectors.toList()` on a huge stream.** The
terminal collector materialises everything. Use `findFirst`, `limit`, or iterate instead.

**A `CompletableFuture` callback blocking an HTTP worker thread.** Dependent stages run on
the completing thread. Use `thenApplyAsync(body, executor)`.

**Tasks silently not running after `shutdown()`.** `shutdown()` stops accepting new tasks;
already-submitted tasks still run. `shutdownNow()` interrupts.

**The build fails on a clean machine with `release version X not supported`.** `release`
was not set, so the compiler used the toolchain's default. Set
`maven.compiler.release` or the Gradle toolchain explicitly.

**Tests pass locally and fail in CI only.** Timezone, locale, filesystem case-sensitivity,
and parallelism differ. Pin the timezone and locale in tests, and do not rely on
`parallel()` ordering.

## Anti-patterns

- **A record used where identity matters.** Records give value equality; two instances with
  the same data are `equals`. Use a class when identity or mutability is required.
- **Deeply nested streams.** Four stages of `flatMap` is a loop wearing a disguise. Prefer
  a `for` loop or an explicit intermediate collection.
- **`Collectors.toMap` with a duplicate-key merge that silently keeps the first.** Always
  supply the merge function you actually want.
- **Blocking inside `parallelStream`**, which saturates the shared common pool.
- **Creating an `ExecutorService` in a method that is called per request.**
- **`catch (Exception ignored)`** at a layer that cannot decide anything, hiding bugs.
- **Returning `null` from a public method to signal "not found"** in an API where callers
  forget the null check.
- **Field-level `Optional`.**
- **Stringly-typed enums**; use the `Enum` type so the compiler and IDE can help.
- **Static mutable state** to avoid passing a dependency. It makes tests order-dependent.
- **Overriding `equals`/`hashCode` inconsistently** (overriding one but not the other,
  or using a mutable field in `hashCode`).

## References

- Java SE documentation and specifications
  - https://docs.oracle.com/en/java/javase/
  - https://docs.oracle.com/javase/specs/
  - https://docs.oracle.com/java/technologies/
- OpenJDK projects and JEPs (virtual threads and related proposals)
  - https://openjdk.org/
  - https://openjdk.org/projects/loom/
  - https://openjdk.org/jeps/
  - https://openjdk.org/jeps/444 (virtual threads)
  - https://openjdk.org/jeps/0 (JEP status index; use it to check whether a feature is
    preview, candidate, or permanent in your target release)
- Concurrency utilities
  - https://docs.oracle.com/en/java/javase/  (see `java.util.concurrent` in the API docs
    for your JDK)
- Build tools
  - https://maven.apache.org/guides/introduction/introduction-to-the-lifecycle.html
  - https://maven.apache.org/guides/introduction/introduction-to-the-pom.html
  - https://docs.gradle.org/current/userguide/
- Testing
  - https://junit.org/junit5/docs/current/user-guide/

For deeper reading:

- `references/streams-and-optionals.md` - collector reference, grouping and partitioning,
  performance characteristics, Optional combinators, anti-pattern catalogue.
- `references/concurrency-and-builds.md` - executors versus virtual threads decision,
  `CompletableFuture` composition cookbook, Maven and Gradle multi-module layouts.