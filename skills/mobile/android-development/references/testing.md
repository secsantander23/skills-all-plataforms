# Testing reference

Three tiers. Prefer the fastest tier that can actually catch the bug.

| Tier | Runs on | Command | Speed |
| --- | --- | --- | --- |
| Local unit | JVM | `gradlew test` | Seconds |
| Instrumented | Device or emulator | `gradlew connectedAndroidTest` | Minutes |
| Compose UI | Device or emulator with a test activity | `gradlew connectedDebugAndroidTest` | Minutes |

Rule of thumb: put logic in ViewModels and repositories so tier 1 covers it. Compose UI
tests verify wiring, not behavior.

## Local unit tests

```kotlin
class MoneyFormatterTest {
    @Test
    fun formatsCentsAsCurrency() {
        val formatter = MoneyFormatter(currencyCode = "USD")
        assertEquals("$12.34", formatter.format(1234))
    }

    @Test
    fun negativeAmountsWrapParentheses() {
        assertEquals("($5.00)", MoneyFormatter("USD").format(-500))
    }
}
```

`kotlin.test` gives `assertEquals`, `assertTrue`, `assertIs`, and `assertFailsWith`. JUnit 4
gives `@Before`, `@After`, `@Test(expected = ...)`, and rules.

## Coroutines and Flow

```kotlin
@OptIn(ExperimentalCoroutinesApi::class)
class FeedViewModelTest {

    private val dispatcher = StandardTestDispatcher()

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
        val repository = FakeFeedRepository(listOf(FeedItem("1", "Alpha")))
        val viewModel = FeedViewModel(repository)

        val states = mutableListOf<FeedUiState>()
        val job = backgroundScope.launch(UnconfinedTestDispatcher(testScheduler)) {
            viewModel.uiState.toList(states)
        }

        advanceUntilIdle()

        assertTrue(states.any { it is FeedUiState.Ready })
        job.cancel()
    }

    @Test
    fun surfacesError() = runTest(dispatcher) {
        val repository = FakeFeedRepository(error = IOException("offline"))
        val viewModel = FeedViewModel(repository)

        advanceUntilIdle()

        assertEquals(
            FeedUiState.Error("offline"),
            viewModel.uiState.value,
        )
    }
}
```

Details that decide whether the test is real:

- `Dispatchers.setMain` in `setUp`, `Dispatchers.resetMain` in `tearDown`. Without them
  `viewModelScope` cannot launch and you get
  `Module with the Main dispatcher had failed to initialize`.
- `runTest(dispatcher)` binds the test scheduler to the same dispatcher, so
  `advanceUntilIdle()` actually drives the code under test.
- `UnconfinedTestDispatcher` runs coroutines eagerly, which makes collecting the initial
  state simple. It also executes everything inside `runTest`, including `delay` calls,
  which is a documented testing shortcut.
- `backgroundScope.launch` is cancelled automatically when `runTest` ends, so a
  never-ending `StateFlow` collector does not hang the test.
- `advanceUntilIdle()` drains the scheduler. Use `runCurrent()` when you want to observe
  work done so far without waiting for scheduled delays.

Assert on a single emission rather than the whole stream when possible. Collecting the
entire list couples the test to the number of notifications.

## Turbine for Flow tests

```kotlin
@Test
fun emitsLoadingThenReady() = runTest {
    repository.feed().test {
        assertEquals(FeedUiState.Loading, awaitItem())
        assertTrue(awaitItem() is FeedUiState.Ready)
        cancelAndIgnoreRemainingEvents()
    }
}
```

Turbine gives `test`, `awaitItem`, `awaitComplete`, `awaitError`, and `cancelAndIgnoreRemainingEvents`.
It fails with a clear message on timeout instead of hanging.

## Room tests

```kotlin
@RunWith(AndroidJUnit4::class)
class ItemDaoTest {
    private lateinit var db: AppDatabase
    private lateinit var dao: ItemDao

    @Before
    fun setUp() {
        db = Room.inMemoryDatabaseBuilder(
            ApplicationProvider.getApplicationContext(),
            AppDatabase::class.java,
        ).allowMainThreadQueries().build()
        dao = db.itemDao()
    }

    @After
    fun tearDown() {
        db.close()
    }

    @Test
    fun upsertThenObserveReturnsRows() = runTest {
        dao.upsert(listOf(ItemEntity("1", "SKU-1", "Widget", 100L)))

        val rows = dao.observeAll().first()

        assertEquals(1, rows.size)
        assertEquals("Widget", rows.first().title)
    }

    @Test
    fun replaceAllDeletesPreviousRows() = runTest {
        dao.upsert(listOf(ItemEntity("1", "SKU-1", "Widget", 100L)))

        dao.replaceAll(listOf(ItemEntity("2", "SKU-2", "Gadget", 200L)))

        val rows = dao.observeAll().first()
        assertEquals(listOf("2"), rows.map { it.id })
    }
}
```

An in-memory database tests logic but not migrations. Test migrations by writing a file
database, opening the old version, inserting rows, running the migration, and asserting.

```kotlin
@Test
fun migrationFrom1To2PreservesRows() {
    val context = ApplicationProvider.getApplicationContext<AppContext>()
    val helper = MigrationTestHelper(
        InstrumentationRegistry.getInstrumentation(),
        AppDatabase::class.java,
    )

    val db = helper.createDatabase(TEST_DB, 1).apply {
        execSQL("INSERT INTO items (id, sku, title, updatedAt) VALUES ('1','SKU','T',0)")
        close()
    }

    helper.runMigrationsAndValidate(TEST_DB, 2, true, MIGRATION_1_2).use { migrated ->
        val cursor = migrated.query("SELECT * FROM items")
        assertTrue(cursor.moveToFirst())
        assertEquals("1", cursor.getString(cursor.getColumnIndexOrThrow("id")))
    }
}
```

Requires the exported schemas in `room.schemaLocation` on the annotation processor.

## Compose UI tests

```kotlin
@RunWith(AndroidJUnit4::class)
class FeedScreenTest {
    @get:Rule
    val composeTestRule = createComposeRule()

    @Test
    fun showsItemsAndOpensDetail() {
        composeTestRule.setContent {
            MaterialTheme {
                FeedScreen(
                    uiState = FeedUiState.Ready(
                        listOf(FeedItem("1", "Alpha"), FeedItem("2", "Beta")),
                    ),
                    onOpenItem = { },
                )
            }
        }

        composeTestRule.onNodeWithText("Alpha").assertIsDisplayed()
        composeTestRule.onNodeWithText("Beta").assertIsDisplayed()
    }
}
```

Test a `FeedScreen` that takes a plain state object and callbacks. Then the same test runs
without a ViewModel, a database, or a network.

Interactions:

```kotlin
composeTestRule.onNodeWithText("Increment").performClick()
composeTestRule.onNodeWithText("Field").performTextInput("hello")
composeTestRule.onNodeWithTag("feed").performScrollToIndex(5)
composeTestRule.onNodeWithContentDescription("Open menu").performClick()
```

Assertions:

```kotlin
.onNodeWithText("Checkout").assertIsDisplayed()
.onNodeWithText("Checkout").assertDoesNotExist()
.onNodeWithText("Checkout").assertCountEquals(1)
.onNodeWithText("Checkout").assert(hasClickAction())
.onNodeWithTag("cart-total").assertTextEquals("$42.00")
```

Prefer `Modifier.testTag` over text for anything that is not user-visible copy. Add the
semantics you need:

```kotlin
Modifier
    .testTag("cart-total")
    .semantics { contentDescription = "Cart total" }
```

`createComposeRule()` is enough for pure composables. Use
`createAndroidComposeRule<ComponentActivity>()` when the test needs a real `Context`,
an Activity, or platform resources.

Asynchronous UI:

```kotlin
composeTestRule.waitUntil(timeoutMillis = 5_000) {
    composeTestRule.onAllNodesWithText("Loaded").fetchSemanticsNodes().isNotEmpty()
}
```

Never use `Thread.sleep` in a Compose test.

## Espresso

```kotlin
@RunWith(AndroidJUnit4::class)
class LoginFlowTest {
    @get:Rule
    val composeTestRule = createAndroidComposeRule<MainActivity>()

    @Test
    fun loginNavigatesToHome() {
        composeTestRule.onNodeWithText("Email").performTextInput("a@b.com")
        composeTestRule.onNodeWithText("Password").performTextInput("hunter22")
        composeTestRule.onNodeWithText("Sign in").performClick()

        composeTestRule.waitUntil(5_000) {
            composeTestRule.onAllNodesWithText("Welcome").fetchSemanticsNodes().isNotEmpty()
        }
    }
}
```

Espresso remains useful for system dialogs, WebView, and views that Compose cannot see.
`Intents` and `IntentsTestRule` verify launched intents.

## Mocking

Hand-written fakes first. When the seam does not fit:

```kotlin
class MockApi : ItemApi {
    var items: List<ItemDto> = emptyList()
    var error: IOException? = null

    override suspend fun fetchItems(): List<ItemDto> {
        error?.let { throw it }
        return items
    }
}
```

MockK and Mockito-Kotlin exist for cases a fake will not cover. Use `kotlinx.serialization`
or Moshi to produce JSON fixtures rather than mocking the HTTP layer.

## Commands

```bash
gradlew test
gradlew testDebugUnitTest --tests "com.example.app.FeedViewModelTest"
gradlew connectedAndroidTest
gradlew connectedDebugAndroidTest
gradlew lint
```

CI should run `gradlew test lint` on every push. Instrumented tests run on a dedicated job
with an emulator; the Gradle Managed Devices plugin can provision one, or use a container
image. Do not gate merges on instrumented tests.

## Coverage

```bash
gradlew testDebugUnitTest
# aggregate reports with jacoco
```

Coverage is a signal, not a target. A test that asserts `assertTrue(result.isNotEmpty())`
raises coverage without raising confidence.

## Sources

- Android testing overview: https://developer.android.com/training/testing
- Testing local unit tests: https://developer.android.com/training/unit-tests
- Building and running instrumented tests: https://developer.android.com/training/instrumented-tests
- Compose testing: https://developer.android.com/develop/ui/compose/testing
- Room testing: https://developer.android.com/training/data-storage/room/testing-data
- Kotlin coroutines testing: https://kotlinlang.org/docs/kotlinx-coroutines-test.html
- Turbine: https://github.com/cashapp/turbine