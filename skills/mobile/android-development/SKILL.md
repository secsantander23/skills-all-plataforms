---
name: Android Development
description: Covers native Android app development in Kotlin including language essentials, Jetpack Compose declarative UI and recomposition, Activity and Fragment lifecycle, ViewModel with StateFlow, architecture components such as Room and WorkManager, coroutines and Flow, the Gradle build, MVVM structure, and testing with JUnit and Compose. Use when the user asks about a Kotlin class, a composable, a recomposition issue, an Activity lifecycle question, a Gradle build failure, a Room query, a ViewModel, or a failing Android test.
metadata:
  opencode/autoinvoke: true
---

# Android Development

An Android app is an APK or AAB built by Gradle, containing DEX bytecode, resources, and a
manifest. At runtime the system creates an `Activity`, which hosts a `Window`, which hosts
views. Compose replaces the last layer with a composition driven by a recomposer.

The two toolkits coexist: Views with XML layouts plus AndroidX, and Compose with
`@Composable` functions. Both are supported. Migrate by screen, not all at once.

## When to use this

- Writing or reviewing Kotlin, an Activity, a Fragment, or a Compose screen.
- Diagnosing excessive recomposition or janky frames.
- Structuring state with ViewModel plus StateFlow.
- Building a data layer with Room, Retrofit, and repositories.
- Fixing a Gradle sync, manifest merger, or dependency resolution failure.
- Writing JUnit, coroutine, Room, or Compose UI tests.

## Quick start

```kotlin
package com.example.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            MaterialTheme {
                CounterScreen()
            }
        }
    }
}

@Composable
fun CounterScreen(modifier: Modifier = Modifier) {
    var count by remember { mutableIntStateOf(0) }

    Column(modifier = modifier.padding(16.dp)) {
        Text(text = "Count: $count", style = MaterialTheme.typography.headlineMedium)
        Button(onClick = { count++ }, modifier = Modifier.padding(top = 8.dp)) {
            Text("Increment")
        }
    }
}
```

## Kotlin essentials

- Use `data class` for values, `sealed interface` for closed state, plain `class` for
  services with a lifetime.
- Prefer `val`. Use `var` only for genuinely mutable state.
- Extension functions dispatch statically, not virtually. You cannot override one.
- `lateinit var` throws `UninitializedPropertyAccessException` if touched before
  assignment. `by lazy` is safer when you control the initialization point.
- A `suspend` function only runs when called from a coroutine.
- `runCatching` catches `CancellationException` too. Re-throw it in coroutine code.

## Compose and recomposition

A composable runs when Compose mounts it and again when state it read changes. Recomposition
re-runs the function and diffs the output.

```kotlin
@Composable
fun ProductList(
    products: List<Product>,
    onSelect: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyColumn(modifier = modifier) {
        items(items = products, key = { it.id }) { product ->
            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { onSelect(product.id) }
                    .padding(horizontal = 16.dp, vertical = 12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(text = product.title, style = MaterialTheme.typography.bodyLarge)
            }
        }
    }
}
```

Five rules prevent most recomposition problems:

1. **Annotate state classes `@Immutable` or `@Stable`.** Otherwise the compiler must assume
   any property may change and conservatively skips skipping. `@Immutable` means every
   property is a `val` of an immutable type. Annotating a class that violates the contract
   produces stale UI.
2. **Keep callback identities stable.** A `lambda` parameter is compared by reference, so a
   new lambda each render forces recomposition. Pass `viewModel::select`, not
   `{ viewModel.select(it) }`.
3. **Read state as late as possible.** A composable that reads a `MutableState` rebuilds on
   every change to it, including changes that do not affect its output.
4. **Collapse high-frequency state with `derivedStateOf`.** It notifies only when the
   derived value changes, so scroll position can change every frame without rebuilding the
   screen.
5. **Key lazy lists.** Without stable keys, Compose reuses composition by position and state
   attaches to the wrong row.

Layout and modifier rules:

- Modifier order matters. `Modifier.padding(16.dp).background(Red)` draws the background
  outside the padding; `.background(Red).padding(16.dp)` draws it only under the content.
  Modifiers apply outside-in.
- `weight`, `align`, and `matchParentSize` are `ColumnScope`, `RowScope`, or `BoxScope`
  extensions. They resolve only inside the matching container.
- Apply the `Scaffold`'s padding to its content or content will sit under the system bars.
- `Scaffold`, `TopAppBar`, `NavigationBar`, and some others are still
  `@ExperimentalMaterial3Api`. Add `@OptIn(ExperimentalMaterial3Api::class)` where asked
  rather than suppressing the warning globally.

Effect APIs:

| Effect | Fires | Use |
| --- | --- | --- |
| `LaunchedEffect(key)` | On enter, re-run on key change, cancelled on leave | Suspending one-shot work |
| `DisposableEffect(key)` | On enter, `onDispose` on leave | Setup and teardown |
| `SideEffect` | After every successful composition | Committing to non-Compose APIs |
| `produceState(initial, key)` | Producer launched like `LaunchedEffect` | Small coroutine-driven state |
| `rememberCoroutineScope()` | Returns a scope cancelled with the composition | Event handlers that launch work |

Do not launch work in the composable body, and do not write to state during composition.
Both produce recomposition warnings or crashes. Deeper material, including stability
annotations and debugging with the Layout Inspector, is in
`references/compose-recomposition.md`.

## Activity and Fragment lifecycle

```
onCreate → onStart → onResume → onPause → onStop → onRestart → onStart → ...
                                  ↘ onDestroy
```

- `onCreate` receives a `Bundle` from a previous instance if the process was killed.
- `onSaveInstanceState` runs before a possible destroy. Store small values there.
- `onDestroy` is not guaranteed on a configuration change. Never make it the only teardown.
- Collect flows only while at least STARTED.

```kotlin
class FeedViewModel(private val repository: FeedRepository) : ViewModel() {
    private val _uiState = MutableStateFlow<FeedUiState>(FeedUiState.Loading)
    val uiState: StateFlow<FeedUiState> = _uiState.asStateFlow()

    fun refresh() {
        viewModelScope.launch {
            _uiState.value = FeedUiState.Loading
            _uiState.value = try {
                FeedUiState.Ready(repository.fetch())
            } catch (error: IOException) {
                FeedUiState.Error(error.message.orEmpty())
            }
        }
    }
}

sealed interface FeedUiState {
    data object Loading : FeedUiState
    data class Ready(val items: List<FeedItem>) : FeedUiState
    data class Error(val message: String) : FeedUiState
}
```

```kotlin
@Composable
fun FeedRoute(viewModel: FeedViewModel = viewModel()) {
    val uiState by viewModel.uiState.collectAsStateWithLifecycle()

    LaunchedEffect(Unit) { viewModel.refresh() }

    when (val state = uiState) {
        is FeedUiState.Loading -> LoadingIndicator()
        is FeedUiState.Error -> ErrorMessage(state.message)
        is FeedUiState.Ready -> FeedList(state.items)
    }
}
```

`collectAsStateWithLifecycle` from `androidx.lifecycle:lifecycle-runtime-compose` stops
collecting below STARTED. Plain `collectAsState` keeps collecting while backgrounded.

Use `DisposableEffect` to tie a non-suspending subscription to the composition; it runs
`onDispose` when the composable leaves. Fragments remain the right choice for
`FragmentResult`, per-fragment nav graphs, or hosts shared across screens. Scope their
ViewModels with `viewModels()` and read typed arguments from `requireArguments()`.

## Coroutines and Flow

```kotlin
suspend fun loadProfile(id: String): Profile = withContext(Dispatchers.IO) {
    api.fetchProfile(id)
}

// Structured concurrency: children are awaited and cancelled together.
suspend fun loadMany(ids: List<String>): List<Profile> = coroutineScope {
    ids.map { id -> async(Dispatchers.IO) { api.fetchProfile(id) } }.awaitAll()
}

val visible: Flow<List<Item>> = repository.items
    .map { it.filter(Item::isVisible) }
    .distinctUntilChanged()
    .catch { emit(emptyList()) }
    .flowOn(Dispatchers.IO)

val uiModel: Flow<UiModel> =
    combine(queryFlow, resultsFlow) { query, results -> UiModel(query, results) }
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), UiModel())
```

- `viewModelScope` is cancelled when the ViewModel clears. A coroutine started in
  `onCreate` without a scope outlives the Activity. Use `lifecycleScope` or store and
  cancel the job.
- `Dispatchers.IO` for blocking I/O, `Dispatchers.Default` for CPU work, `Main` for UI.
- `Flow` is cold unless backed by `StateFlow` or `SharedFlow`. `stateIn` and `shareIn`
  make it hot with a defined lifecycle.

## Data layer

| Component | Use |
| --- | --- |
| `ViewModel` | Survives configuration change, scoped to screen |
| `SavedStateHandle` | Small state preserved across process death |
| `Room` | Typed SQLite with compile-time query verification |
| `DataStore` | Typed preferences exposing Flow |
| `WorkManager` | Deferrable, constraint-aware background work |
| `Paging 3` | Paged lists from Room or the network |
| `Navigation` | Single Activity, typed destinations |
| `ActivityResult` APIs | Runtime permissions, camera, document pickers |

```kotlin
@Entity(tableName = "items", indices = [Index(value = ["sku"], unique = true)])
data class ItemEntity(
    @PrimaryKey val id: String,
    val sku: String,
    val title: String,
    val updatedAt: Long,
)

@Dao
interface ItemDao {
    @Query("SELECT * FROM items ORDER BY updatedAt DESC")
    fun observeAll(): Flow<List<ItemEntity>>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(items: List<ItemEntity>)

    @Transaction
    suspend fun replaceAll(items: List<ItemEntity>) {
        deleteAll()
        upsert(items)
    }

    @Query("DELETE FROM items")
    suspend fun deleteAll()
}

@Database(entities = [ItemEntity::class], version = 1, exportSchema = true)
abstract class AppDatabase : RoomDatabase() {
    abstract fun itemDao(): ItemDao
}
```

```kotlin
class ItemRepository(private val dao: ItemDao, private val api: ItemApi) {
    // Room is the single source of truth. The UI observes the database, not the network.
    val items: Flow<List<Item>> = dao.observeAll().map { rows -> rows.map(ItemEntity::toDomain) }

    suspend fun refresh() {
        try {
            dao.replaceAll(api.fetchItems())
        } catch (error: IOException) {
            // Cached rows stay; the Flow already emitted them.
        }
    }
}
```

Room verifies queries at compile time, so a bad `@Query` fails the build. Export schemas
and commit the exported JSON so migrations can be tested. Use `AutoMigration` where it
suffices, or write and test a `Migration`. Never use `fallbackToDestructiveMigration` on a
database holding user data.

Background work with WorkManager is covered in `references/data-and-navigation.md`.
WorkManager is neither exact nor immediate; `ExistingPeriodicWorkPolicy` values are `KEEP`,
`REPLACE`, `UPDATE`, and `CANCEL_AND_REENQUEUE`. Sync architecture is covered by the
`mobile-offline-sync` skill.

## Runtime permissions

```kotlin
@Composable
fun CameraPermissionGate(onGranted: () -> Unit) {
    val context = LocalContext.current
    var granted by remember { mutableStateOf(false) }

    val launcher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
        onResult = { result -> granted = result },
    )

    LaunchedEffect(Unit) {
        granted = ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) ==
            PackageManager.PERMISSION_GRANTED
        if (!granted) launcher.launch(Manifest.permission.CAMERA)
    }

    if (granted) {
        ContentWithCamera()
    } else {
        PermissionRationale(onRequest = { launcher.launch(Manifest.permission.CAMERA) })
    }
}
```

Declare permissions in the manifest and request them at the moment of need, after
explaining why. Handle permanent denial by deep-linking to app settings.

## Gradle build

Three layers: `settings.gradle.kts` (modules and repositories), root `build.gradle.kts`
(plugin declarations with `apply false`), and `app/build.gradle.kts`. Use a version
catalog at `gradle/libs.versions.toml` rather than hardcoding coordinates.

```kotlin
// app/build.gradle.kts
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.ksp)
}

android {
    namespace = "com.example.app"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.example.app"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.material3)
    ksp(libs.room.compiler)
}
```

Facts that bite:

- `namespace` in the Gradle file replaces the manifest `package` attribute.
- The Compose compiler is a Gradle plugin on Kotlin 2.x
  (`org.jetbrains.kotlin.plugin.compose`), not a `kotlinCompilerExtensionVersion` setting.
- `versionCode` must strictly increase for every Play upload.
- Every component with an intent filter needs `android:exported` declared explicitly in the
  manifest, or the manifest merger fails.
- SDK and library versions change constantly. Read the AGP and AndroidX release notes;
  never copy versions from memory.

```bash
gradlew assembleDebug
gradlew installDebug
gradlew bundleRelease          # AAB for Play
gradlew test
gradlew connectedAndroidTest
gradlew :app:dependencies
gradlew --stop
```

On Windows use `gradlew.bat`. Release signing reads from `~/.gradle/gradle.properties` or
environment variables. Never commit a keystore. Full build files, manifest, version
catalogs, multi-module layout, and build failure fixes are in `references/gradle-build.md`.

## Testing

Three tiers: JVM unit tests, Compose UI tests, and instrumented tests. Put logic in
ViewModels and repositories so most coverage lands in the fast tier.

```kotlin
@OptIn(ExperimentalCoroutinesApi::class)
class FeedViewModelTest {
    private val dispatcher = UnconfinedTestDispatcher()

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun emitsReadyState() = runTest(dispatcher) {
        val viewModel = FeedViewModel(FakeFeedRepository(listOf(FeedItem("1", "Alpha"))))

        val states = mutableListOf<FeedUiState>()
        val job = backgroundScope.launch { viewModel.uiState.toList(states) }

        advanceUntilIdle()

        assertTrue(states.any { it is FeedUiState.Ready })
        job.cancel()
    }
}
```

`Dispatchers.setMain` and `resetMain` are mandatory for anything touching `viewModelScope`.
Without them you get
`IllegalStateException: Module with the Main dispatcher is missing`.

```kotlin
@RunWith(AndroidJUnit4::class)
class FeedScreenTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun showsItems() {
        composeTestRule.setContent {
            MaterialTheme {
                FeedScreen(uiState = FeedUiState.Ready(listOf(FeedItem("1", "Alpha"))))
            }
        }

        composeTestRule.onNodeWithText("Alpha").assertIsDisplayed()
    }
}
```

- `debugImplementation("androidx.compose.ui:ui-test-manifest")` is required, or you get
  `No compose UI test infrastructure found`.
- Tag what you need to find with `Modifier.testTag("checkout")` rather than matching text.
- Use `composeTestRule.waitUntil { ... }` for async UI, never a fixed sleep.
- Test the stateless composable with a plain state object and callbacks; then the test
  needs no ViewModel, database, or network.

```bash
gradlew test
gradlew testDebugUnitTest --tests "com.example.app.FeedViewModelTest"
gradlew connectedAndroidTest
```

Room tests, Turbine, migration testing, Espresso, and mocking patterns are in
`references/testing.md`.

## Checklist

- [ ] Blocking work runs off the main thread in a coroutine.
- [ ] Coroutines tied to a lifecycle and cancelled when it ends.
- [ ] Flow collected with `collectAsStateWithLifecycle`, not `collectAsState`.
- [ ] State classes annotated `@Stable` or `@Immutable` where appropriate.
- [ ] Lazy lists use stable `key` values.
- [ ] Modifier order gives the intended padding and background.
- [ ] Database is the source of truth; the UI observes Room, not the network.
- [ ] Room schema exported and migrations tested.
- [ ] `versionCode` incremented for every upload.
- [ ] Manifest declares `android:exported` on every component with an intent filter.
- [ ] Release build has minification, shrinking, and a real signing config.
- [ ] Tests cover ViewModels and repositories on the JVM where possible.

## Common mistakes

- **Manifest merger failure: `android:exported needs to be explicitly specified`.** Add
  the attribute to every component with an intent filter.
- **`Dependency requires compileSdk 34 or later`.** Raise `compileSdk`, or pin an older
  library version.
- **`Unresolved reference` on `R` or a resource.** The resource does not exist, or the
  module does not depend on the library declaring it.
- **`IllegalStateException: Module with the Main dispatcher is missing`.** A unit test used
  `viewModelScope` without `Dispatchers.setMain`.
- **Compose UI test: `No compose UI test infrastructure found`.** Add
  `debugImplementation("androidx.compose.ui:ui-test-manifest")`.
- **List state attaching to the wrong row.** Missing or non-unique `key` in a lazy list.
- **Screen not updating after a data change.** Observing the network instead of the
  database, or collecting with `collectAsState` in a composable that is not reading it.
- **Background work never running.** WorkManager defers work by design; check constraints
  and the policy used.
- **`ClassNotFoundException` or `NoSuchMethodError` only in release.** R8 stripped a class
  reached only through reflection. Add a keep rule.

## Anti-patterns

- Network or disk I/O inside a composable's body.
- Passing an entire data model into every composable so one field change rebuilds
  everything.
- `GlobalScope` or a bare `CoroutineScope` for work that should die with the screen.
- `runCatching` around suspending code without re-throwing `CancellationException`.
- Deeply nested Fragments and Views where Compose or a single Activity would do.
- Passing `Context` around instead of parameters and `LocalContext.current` at the leaf.
- Credentials in `SharedPreferences` instead of `EncryptedSharedPreferences` or the
  Keystore.
- Hard-coded SDK or library versions from memory.

## References

- Android Developers documentation: https://developer.android.com/
- Kotlin documentation: https://kotlinlang.org/docs/home.html
- Jetpack Compose documentation: https://developer.android.com/develop/ui/compose
- Compose performance guide: https://developer.android.com/develop/ui/compose/performance
- Activity lifecycle: https://developer.android.com/guide/components/activities/activity-lifecycle
- Fragments: https://developer.android.com/guide/fragments
- ViewModel: https://developer.android.com/topic/libraries/architecture/viewmodel
- Recommended app architecture: https://developer.android.com/topic/architecture
- Coroutines on Android: https://developer.android.com/kotlin/coroutines/coroutines-overview
- Kotlin Flow: https://developer.android.com/kotlin/flow
- Room: https://developer.android.com/training/data-storage/room
- WorkManager: https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started
- Navigation component: https://developer.android.com/guide/navigation
- Gradle build configuration: https://developer.android.com/build
- App Bundle format: https://developer.android.com/guide/app-bundle
- Testing: https://developer.android.com/training/testing
- Compose testing: https://developer.android.com/develop/ui/compose/testing

Supporting references in this skill:
- `references/compose-recomposition.md` - stability, skipping, effects, debugging.
- `references/gradle-build.md` - full build files, catalogs, multi-module, failure fixes.
- `references/testing.md` - Room, Flow, Compose UI, migration tests, mocking.