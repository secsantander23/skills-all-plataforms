# Compose recomposition reference

## The model

A `@Composable` function is called by the runtime to produce a description of UI. The
result is remembered in a structure called a slot table. When something the function read
changes, the runtime re-invokes just that function, diffs the new output, and applies only
what changed to the composition.

Two costs follow:

- **Recomposition** is re-running a function and diffing its output. It is relatively cheap.
- **Recomposition of an expensive subtree** is the problem. Recomposing a big subtree
  because one text field changed is what causes dropped frames.

## What triggers recomposition

- Reading a `MutableState`, `StateFlow`, `State` delegate property, or an observable whose
  read happens inside the composable's scope.
- The parent recomposing and passing different values to the composable's parameters.
- An `AnimatedVisibility` or `AnimatedContent` transitioning.

Reading state inside a `LaunchedEffect` or `onClick` lambda does not subscribe the
composable. Only reads during composition (directly in the body, or in a called function
that is also part of the composition) create subscriptions.

## Skipping

Compose skips a composable when all of its parameters are `equals`-equal to the previous
call and none of its scoped state changed. The consequences:

- Passing a `List` or `Map` parameter: the comparison is `equals`, so a new but equal list
  still skips. A mutated list does not, because `equals` would then return false.
- Passing a lambda: the comparison is reference identity, so a new lambda each render
  forces recomposition. This is the single most common avoidable recomposition.
- Annotating a class `@Stable` or `@Immutable` lets the compiler skip even when the
  instance identity changes.

```kotlin
@Immutable
data class ProductUi(
    val id: String,
    val title: String,
    val price: String,
)

@Stable
class FormController {
    var isValid by mutableStateOf(false)
        private set
}
```

`@Immutable` means every property is a `val` of an immutable type. `@Stable` means the type
guarantees that mutations are announced through observable properties, as `ChangeNotifier`
or `mutableStateOf` do. Annotating a class that violates the contract causes the compiler
to skip recomposition when it should not, and you get stale UI.

## Common recomposition problems

### New lambda every render

```kotlin
// Bad: new function identity on every parent recomposition.
Row {
    ProductRow(onSelect = { viewModel.select(it) })
}

// Better: hoist the callback and pass the same reference down.
Row {
    ProductRow(onSelect = viewModel::select)
}
```

A method reference such as `viewModel::select` creates a bound closure; if the ViewModel
reference is stable the reference is stable too. Alternatively store it:

```kotlin
val onSelect: (String) -> Unit = { viewModel.select(it) }
```

### State read too high in the tree

```kotlin
// Recomposes the whole list when the query changes.
@Composable
fun SearchScreen(query: String) {
    val results by viewModel.results.collectAsStateWithLifecycle()
    Column {
        TextField(query = query, onQueryChange = viewModel::setQuery)
        ResultList(results)
    }
}

// Better: only the text field's displayed value depends on the query.
@Composable
fun SearchScreen() {
    Column {
        QueryField()
        ResultList()
    }
}

@Composable
private fun QueryField() {
    val query by viewModel.query.collectAsStateWithLifecycle()
    TextField(value = query, onValueChange = viewModel::setQuery)
}
```

Splitting by concern fixes this without any compiler tricks.

### Non-stable parameters

```kotlin
// Unstable: the compiler cannot prove the class's properties do not change.
class Cart {
    val items = mutableListOf<Item>()
}

// Mark it, or convert to a data class with only immutable properties.
@Stable
class Cart {
    val items: List<Item> = emptyList()
}
```

### Objects allocated in the body

```kotlin
// A new object every recomposition.
Text(modifier = Modifier.padding(8.dp))
Text(
    modifier = Modifier
        .background(brandColor)
        .clip(RoundedCornerShape(8.dp)),
)
```

`Modifier.padding(8.dp)` allocates a new modifier object per call. The diffing handles it,
but the allocation churn shows up in profiles for very hot paths. Hoist constants that are
reused:

```kotlin
private val cardShape = RoundedCornerShape(8.dp)
private val cardPadding = PaddingValues(16.dp)
```

### Reading frequently changing state in an effect

```kotlin
val items = remember(derivedItems) {
    expensiveSort(derivedItems)
}
```

`remember` caches keyed on its inputs. The lambda runs only when the key changes.

### `derivedStateOf` for high-frequency state

Scroll position changes every frame. Rebuilding the whole screen on every frame is
wasteful when only a button's visibility depends on it.

```kotlin
val listState = rememberLazyListState()

val showFab by remember {
    derivedStateOf { listState.firstVisibleItemIndex > 2 }
}

if (showFab) {
    ExtendedFloatingActionButton(onClick = { scope.launch { listState.scrollToItem(0) } }) {
        Text("Top")
    }
}
```

`derivedStateOf` recomputes its block when its dependencies change but only notifies when
the derived value itself changes.

## Effect APIs

| Effect | Fires when | Use for |
| --- | --- | --- |
| `LaunchedEffect(key1, key2)` | Once when entering composition, re-launched when a key changes, cancelled on leave | One-shot suspending work, event subscription |
| `DisposableEffect(key)` | On enter, `onDispose` on leave | Non-suspending setup and teardown |
| `SideEffect` | After every successful composition | Committing to non-Compose APIs; runs on every recomposition |
| `produceState(initial, key)` | Producer launched like `LaunchedEffect`, exposes state | Small state driven by a coroutine |
| `rememberCoroutineScope()` | Returns a scope cancelled with the composition | Event handlers that launch work |

Rules:

- `LaunchedEffect(Unit)` runs once per composition instance. Give it a key that changes to
  re-run it.
- Do not launch work directly in the composable body. Use an effect.
- Do not write to state during composition. That produces
  `Recomposer: recomposition requested while the current recomposition is in progress` or a
  "backwards write" warning in older versions. Schedule it with
  `LaunchedEffect` or `snapshotFlow`.

```kotlin
// Subscribe to a changing flow without recomposing on every intermediate value.
LaunchedEffect(query) {
    snapshotFlow { scrollState.value }
        .debounce(300)
        .collect { offset -> viewModel.onScrolled(offset) }
}
```

`snapshotFlow` bridges Compose snapshot state to a Flow.

## Stability in a real layer

A clean pattern is to model UI state as immutable data classes with `@Immutable`, hoist
into a state holder, and keep the composable stateless.

```kotlin
@Immutable
data class FeedUiState(
    val isLoading: Boolean = false,
    val items: List<FeedItem> = emptyList(),
    val error: String? = null,
)

@Stable
class FeedUiStateHolder(initial: FeedUiState = FeedUiState()) {
    var state by mutableStateOf(initial)
        private set

    fun setLoading(loading: Boolean) {
        state = state.copy(isLoading = loading)
    }
}
```

## Diagnosing

Enable "Track widget rebuilds" in Android Studio Layout Inspector or DevTools Layout
Inspector. You see which composables recomposed, how many times, and which parameter
caused it. Fix from there, not from intuition.

Other tools:

- Layout Inspector for the view hierarchy and recomposition counts.
- `Trace` and the Compose tracing categories for frame-level detail.
- Profiler for allocation and frame timing.

Release or profile builds only. Debug builds are far slower and recomposition counts differ.

## Failure modes

- **Stale UI**: a `@Stable` or `@Immutable` annotation that is not actually true.
- **Everything recomposes on every frame**: a scroll or animation state read in a high-level
  composable instead of a leaf.
- **Unnecessary recomposition from lambdas**: new function identities each render.
- **Crash with "cannot be coerced to a closed set of values"**: `remember` without a key
  inside a conditional branch, so the slot count changes. Keep `remember` calls unconditional
  and branch inside the computed value, or use keys.
- **Composition order errors**: calling a composable inside a `when` branch that changes
  shape without keys.

## Sources

- Compose performance guide: https://developer.android.com/develop/ui/compose/performance
- Compose compiler metrics: https://kotlinlang.org/docs/compiler-metrics.html
- Side effects in Compose: https://developer.android.com/develop/ui/compose/side-effects
- Stability configuration: https://developer.android.com/develop/ui/compose/performance/stability
- Compose architecture guide: https://developer.android.com/topic/architecture