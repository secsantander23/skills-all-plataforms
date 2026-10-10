# Concurrency and Build Layouts

## Choosing a concurrency model

| Workload | Model |
| --- | --- |
| CPU-bound, bounded concurrency | fixed `ExecutorService`, sized near the processor count |
| IO-bound, blocking libraries | larger fixed pool, or virtual threads where available |
| IO-bound, async libraries | a single-threaded event loop (Netty, Vert.x, or your own) |
| Fan-out/join across independent calls | `CompletableFuture` with an explicit executor |
| Short independent tasks | `parallelStream` only after measuring |

Sizing a fixed pool:

```java
int cpus = Runtime.getRuntime().availableProcessors();

// CPU-bound: enough threads to fill the cores, no more.
int cpuPool = Math.max(2, cpus);

// IO-bound: more threads than cores because threads spend time waiting.
// Size from measurement, not from a formula.
int ioPool = 32;
```

Container awareness matters: in a container, `availableProcessors()` reflects the
configured CPU limit on modern JDKs, but memory limits and CPU quotas still apply. Always
bound the pool, never use an unbounded cached pool for request handling.

## Structured task submission

```java
import java.util.concurrent.*;
import java.util.List;

record FetchResult(String url, int status, String body) {}

List<FetchResult> fetchAll(List<String> urls, Duration timeout) throws InterruptedException {
    ExecutorService pool = Executors.newFixedThreadPool(8);
    try (pool) {
        List<Future<FetchResult>> futures = new ArrayList<>(urls.size());
        for (String url : urls) {
            futures.add(pool.submit(() -> fetch(url)));
        }

        List<FetchResult> out = new ArrayList<>(urls.size());
        long deadline = System.nanoTime() + timeout.toNanos();
        for (Future<FetchResult> f : futures) {
            long remaining = deadline - System.nanoTime();
            if (remaining <= 0) {
                f.cancel(true);
                throw new TimeoutException("batch exceeded " + timeout);
            }
            try {
                out.add(f.get(remaining, TimeUnit.NANOSECONDS));
            } catch (ExecutionException e) {
                // One failure should not lose the whole batch; record it.
                out.add(new FetchResult("", -1, e.getCause().getMessage()));
            }
        }
        return out;
    }
}
```

`try (pool)` uses `ExecutorService`'s `close()`, which waits for termination. In older code
you write `pool.shutdown()` plus `awaitTermination` explicitly.

## CompletableFuture cookbook

```java
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.Executor;

Executor ioPool = Executors.newFixedThreadPool(16);

// Sequential dependency, off the completing thread.
CompletableFuture<String> result = loadUser(id)
        .thenApplyAsync(User::email, ioPool)
        .thenComposeAsync(email -> loadProfile(email), ioPool);

// Independent calls joined together.
CompletableFuture<String> order = loadOrder(id);
CompletableFuture<String> user = loadUser(id);
CompletableFuture<String> both = order.thenCombine(user,
        (o, u) -> o + " by " + u).toCompletableFuture();

// Failure handling at the right layer.
CompletableFuture<String> safe = both
        .handle((value, err) -> err == null ? value : "fallback")
        .thenCompose(fallback -> fallback.equals("fallback")
                ? CompletableFuture.failedFuture(new IllegalStateException("both failed"))
                : CompletableFuture.completedFuture(fallback))
        .exceptionally(err -> "last resort: " + err.getMessage());

// Timeouts: orTimeout and completeOnTimeout exist on CompletableFuture.
// Verify their exact signatures for your JDK.
CompletableFuture<String> bounded = both.orTimeout(5, TimeUnit.SECONDS)
                                      .exceptionally(err -> "timeout");

// Manual cancellation of a pending composition.
CompletableFuture<Void> all = CompletableFuture.allOf(order, user);
all.whenComplete((v, err) -> {
    if (err != null) {
        order.cancel(true);
        user.cancel(true);
    }
});
```

Pitfalls:

- Dependent stages run on the completing thread unless you use the `*Async` variant with an
  executor. This is the number one cause of "the HTTP client thread is blocked".
- `join()` throws `CompletionException` wrapping the cause; `get()` throws
  `ExecutionException`. Inside a `CompletableFuture` chain, prefer `join`.
- `CompletableFuture.failedFuture` and `completedStage` exist in newer JDKs; check
  availability.
- A `CompletableFuture` that nobody holds can be garbage collected while in flight in some
  designs; keep a reference.

## Virtual threads: what they change and what they do not

```java
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.List;

List<String> fetchEverything(List<String> urls) throws Exception {
    // One virtual thread per task. No pooling, no sizing.
    try (var executor = Executors.newVirtualThreadPerTaskExecutor()) {
            List<Future<String>> futures = new ArrayList<>(urls.size());
            for (String url : urls) {
                futures.add(executor.submit(() -> fetch(url)));
            }
            List<String> out = new ArrayList<>(urls.size());
            for (Future<String> f : futures) {
                out.add(f.get());
            }
            return out;
    }
}

// The plain Thread API also works.
Thread.ofVirtual().name("worker").start(() -> doWork());
```

What changes:

- Creating a virtual thread is cheap; you create one per task instead of pooling.
- Blocking the thread (a `synchronized` block, a JDBC call, a file read) unmounts it from
  its carrier platform thread, so the carrier is free for other work. Blocking IO libraries
  often work well; long CPU-bound loops do not.
- `ThreadLocal` values scoped to a virtual thread do not leak across tasks the way they can
  with a fixed pool, but a pooled platform thread can still retain values.

What does not change:

- CPU-bound work still only runs in parallel up to the processor count.
- Connection pools (JDBC, HTTP client connection limits) still bound throughput.
- `synchronized` on a hot object still contends; virtual threads do not make locks cheaper.
- Exceptions and `ThreadLocal` semantics, interruption, and structured shutdown still apply.

Availability and related work change between releases: virtual threads are permanent, but
structured concurrency and scoped values have been through preview cycles with changing
APIs. Before depending on them, check:

- https://openjdk.org/jeps/
- https://openjdk.org/projects/loom/
- the release notes for the JDK you build against.

## Maven multi-module

```text
root/
  pom.xml                 (packaging: pom, lists modules)
  billing-api/pom.xml     (packaging: jar)
  billing-impl/pom.xml
  billing-app/pom.xml
```

```xml
<!-- root/pom.xml -->
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>billing-parent</artifactId>
  <version>1.2.0</version>
  <packaging>pom</packaging>

  <modules>
    <module>billing-api</module>
    <module>billing-impl</module>
    <module>billing-app</module>
  </modules>

  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>com.example</groupId>
        <artifactId>billing-api</artifactId>
        <version>${project.version}</version>
      </dependency>
    </dependencies>
  </dependencyManagement>

  <build>
    <pluginManagement>
      <plugins>
        <plugin>
          <groupId>org.apache.maven.plugins</groupId>
          <artifactId>maven-surefire-plugin</artifactId>
          <version>3.2.5</version>
        </plugin>
      </plugins>
    </pluginManagement>
  </build>
</project>
```

Pin plugin versions in `pluginManagement`, otherwise the build resolves whatever Maven
defaults to and your CI starts failing on a plugin upgrade. Import a BOM for dependency
version management rather than repeating versions.

Commands that matter:

```bash
mvn -q verify          # compile, test, package, install to the local repo
mvn dependency:tree    # where did this transitive dependency come from
mvn -o test            # offline; fails fast instead of hanging on the network
mvn -pl billing-app -am package   # build one module plus its dependencies
```

## Gradle multi-project

```kotlin
// settings.gradle.kts
rootProject.name = "billing"
include("billing-api", "billing-impl", "billing-app")
```

```kotlin
// build.gradle.kts (root)
subprojects {
    apply(plugin = "java")

    repositories {
        mavenCentral()
    }

    java {
        toolchain {
            languageVersion = JavaLanguageVersion.of(21)
        }
    }

    tasks.withType<Test> {
        useJUnitPlatform()
        testLogging {
            events("failed")
        }
    }
}
```

```kotlin
// billing-impl/build.gradle.kts
plugins {
    java
}

dependencies {
    implementation(project(":billing-api"))
    testImplementation(platform("org.junit:junit-bom:5.10.2"))
    testImplementation("org.junit.jupiter:junit-jupiter")
}
```

`api` versus `implementation`: `api` exposes the dependency to consumers' compile
classpath, `implementation` keeps it internal. Getting this wrong produces "it compiles
here but not in the consumer" errors that are painful to diagnose. In a single project it
does not matter; in published libraries it is part of your contract.

```bash
./gradlew build
./gradlew test --tests "com.example.billing.*"
./gradlew dependencies --configuration runtimeClasspath
./gradlew --refresh-dependencies
```

Commit `gradle/wrapper/gradle-wrapper.properties` and `gradlew`/`gradlew.bat`. Use the
wrapper in CI.