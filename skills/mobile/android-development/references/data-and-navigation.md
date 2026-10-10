# Data layer, background work, and navigation

## MVVM layering

```
UI (Compose or Views)  →  ViewModel  →  Repository  →  Room / Network
        ↑                            ↓
        └──── StateFlow / LiveData ←──┘
```

Rules that hold the boundary:

- The ViewModel owns screen state and never touches Android framework classes except
  `AndroidViewModel` and `SavedStateHandle`.
- The repository owns the database and the API. It returns domain models, not entities.
- The UI never calls the API directly. It observes the database.
- The database is the single source of truth. A network response is written to the
  database, and the UI updates because the Room Flow emitted.

That last rule is what makes the app offline-first: with the network down, the Flow still
emits the cached rows.

## Room

```kotlin
@Entity(
    tableName = "items",
    indices = [Index(value = ["sku"], unique = true), Index(value = ["updatedAt"])],
)
data class ItemEntity(
    @PrimaryKey val id: String,
    val sku: String,
    val title: String,
    val priceCents: Long,
    val updatedAt: Long,
    val deletedAt: Long? = null,
)

@Dao
interface ItemDao {
    @Query("SELECT * FROM items WHERE deletedAt IS NULL ORDER BY updatedAt DESC")
    fun observeAll(): Flow<List<ItemEntity>>

    @Query("SELECT * FROM items WHERE id = :id AND deletedAt IS NULL")
    fun observeById(id: String): Flow<ItemEntity?>

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(items: List<ItemEntity>)

    @Query("DELETE FROM items WHERE id = :id")
    suspend fun deleteById(id: String)

    @Query("SELECT COUNT(*) FROM items WHERE deletedAt IS NULL")
    suspend fun count(): Int

    @Transaction
    suspend fun replaceAll(items: List<ItemEntity>) {
        deleteAll()
        upsert(items)
    }

    @Query("DELETE FROM items")
    suspend fun deleteAll()
}

@Database(
    entities = [ItemEntity::class],
    version = 1,
    exportSchema = true,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun itemDao(): ItemDao

    companion object {
        @Volatile
        private var instance: AppDatabase? = null

        fun get(context: Context): AppDatabase =
            instance ?: synchronized(this) {
                instance ?: Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "app.db",
                ).build().also { instance = it }
            }
    }
}
```

- Room verifies SQL at compile time. An invalid `@Query` fails the build.
- `exportSchema = true` writes a JSON schema per version. Configure the processor to write
  to a directory you commit, then test migrations against it.
- Use a soft delete column (`deletedAt`) if other devices need to learn about deletions.

Migrations:

```kotlin
@Database(entities = [ItemEntity::class], version = 2, exportSchema = true)
abstract class AppDatabase : RoomDatabase() {
    abstract fun itemDao(): ItemDao
}

val MIGRATION_1_2 = object : Migration(1, 2) {
    override fun migrate(db: SupportSQLiteDatabase) {
        db.execSQL("ALTER TABLE items ADD COLUMN priceCents INTEGER NOT NULL DEFAULT 0")
        db.execSQL("CREATE INDEX IF NOT EXISTS index_items_updatedAt ON items(updatedAt)")
    }
}
```

Then `Room.databaseBuilder(...).addMigrations(MIGRATION_1_2)`. Use `AutoMigration` where it
can infer the change; annotate with `@AutoMigration(from = 1, to = 2)`. Never use
`fallbackToDestructiveMigration()` on a database holding user data.

## Repository

```kotlin
class ItemRepository(
    private val dao: ItemDao,
    private val api: ItemApi,
    private val dispatchers: DispatcherProvider = DefaultDispatcherProvider(),
) {
    val items: Flow<List<Item>> =
        dao.observeAll().map { rows -> rows.map(ItemEntity::toDomain) }

    fun observeItem(id: String): Flow<Item?> =
        dao.observeById(id).map { it?.toDomain() }

    suspend fun refresh(): Result<Unit> = withContext(dispatchers.io) {
        runCatchingCancellable { dao.replaceAll(api.fetchItems().map(ItemDto::toEntity)) }
    }

    suspend fun save(item: Item): Result<Unit> = withContext(dispatchers.io) {
        runCatchingCancellable { dao.upsert(item.toEntity()) }
    }
}

// Re-throw cancellation so structured concurrency still works.
suspend inline fun <T> runCatchingCancellable(crossinline block: () -> T): Result<T> =
    try {
        Result.success(block())
    } catch (cancellation: CancellationException) {
        throw cancellation
    } catch (error: Throwable) {
        Result.failure(error)
    }

interface DispatcherProvider {
    val io: CoroutineDispatcher
    val default: CoroutineDispatcher
}
```

Returning `Result` from the repository lets the ViewModel map an error into UI state without
leaking exception types into the view layer.

## DataStore

```kotlin
val Context.settingsStore by preferencesDataStore(name = "settings")

class SettingsRepository(private val context: Context) {
    private object Keys {
        val THEME = stringPreferencesKey("theme")
    }

    val theme: Flow<String> = context.settingsStore.data.map { it[Keys.THEME] ?: "system" }

    suspend fun setTheme(value: String) {
        context.settingsStore.edit { it[Keys.THEME] = value }
    }
}
```

Prefer DataStore over `SharedPreferences`: it is coroutine- and Flow-native, and its API
never blocks the main thread. Use `EncryptedSharedPreferences` or the Keystore for secrets.

## WorkManager

```kotlin
class SyncWorker(
    context: Context,
    params: WorkerParameters,
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = try {
        repository.pushPending()
        Result.success()
    } catch (error: RetryableException) {
        if (runAttemptCount < 5) Result.retry() else Result.failure()
    } catch (error: CancellationException) {
        throw error
    } catch (error: Exception) {
        Log.e("SyncWorker", "sync failed", error)
        Result.failure()
    }
}

// Periodic, constrained, deduplicated.
val request = PeriodicWorkRequestBuilder<SyncWorker>(6, TimeUnit.HOURS)
    .setConstraints(
        Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .setRequiresBatteryNotLow(true)
            .build(),
    )
    .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 30, TimeUnit.SECONDS)
    .build()

WorkManager.getInstance(context).enqueueUniquePeriodicWork(
    "sync",
    ExistingPeriodicWorkPolicy.KEEP,
    request,
)

// One-time, expedited for user-initiated actions.
val manual = OneTimeWorkRequestBuilder<SyncWorker>()
    .setConstraints(
        Constraints.Builder()
            .setRequiredNetworkType(NetworkType.CONNECTED)
            .build(),
    )
    .build()

WorkManager.getInstance(context).enqueueUniqueWork("sync-manual", ExistingWorkPolicy.KEEP, manual)

// Observe progress.
WorkManager.getInstance(context)
    .getWorkInfoByIdLiveData(manual.id)
    .observeAsState()
```

- WorkManager is neither exact nor immediate. The system batches and defers to respect
  battery and network policy.
- `PeriodicWorkRequestBuilder` has a minimum interval of 15 minutes. Shorter periods are
  silently clamped.
- `ExistingPeriodicWorkPolicy`: `KEEP` (ignore if one exists), `REPLACE` (cancel and
  reschedule), `UPDATE` (keep the schedule, replace the work), `CANCEL_AND_REENQUEUE`.
- `ExistingWorkPolicy` for one-time work: `KEEP`, `REPLACE`, `APPEND`, `APPEND_OR_REPLACE`.
- Set backoff criteria explicitly or the default is exponential from 30 seconds.
- Expedited work (`setExpedited`) requires `foregroundServiceType` and still runs as a
  user-visible job; do not use it for periodic sync.
- Chain work with `ExistingWorkPolicy.APPEND` for sequential steps.

## Navigation

```kotlin
@Composable
fun AppNavHost(navController: NavHostController = rememberNavController()) {
    NavHost(navController = navController, startDestination = "feed") {
        composable("feed") {
            FeedRoute(onOpenItem = { navController.navigate("item/$it") })
        }
        composable(
            route = "item/{itemId}",
            arguments = listOf(navArgument("itemId") { type = NavType.StringType }),
            deepLinks = listOf(navDeepLink { uriPattern = "myapp://item/{itemId}" }),
        ) { entry ->
            ItemRoute(itemId = entry.arguments?.getString("itemId").orEmpty())
        }
        composable("settings") {
            SettingsRoute(onBack = { navController.popBackStack() })
        }
    }
}
```

- `navController.navigate` throws
  `IllegalArgumentException: Navigation destination that matches request cannot be found`
  when the route string is wrong. Build routes from constants, not string literals.
- The same library provides the Hilt navigation API, `@AndroidEntryPoint` on
  destinations, and `hiltViewModel()` for injecting a destination-scoped ViewModel.
- Passing arguments: define them in `navArgument(...)` with a `NavType`, then read them
  from `entry.arguments`. Use the Safe Args Gradle plugin for compile-time-safe args.
- `popBackStack(route, inclusive)` to remove a whole subtree.

## Paging 3

```kotlin
class ItemPagingSource(
    private val api: ItemApi,
) : PagingSource<Int, ItemDto>() {
    override suspend fun load(params: LoadParams<Int>): LoadResult<Int, ItemDto> = try {
        val page = params.key ?: 0
        val response = api.fetchItems(page = page, size = params.loadSize)
        LoadResult.Page(
            data = response.items,
            prevKey = if (page == 0) null else page - 1,
            nextKey = if (response.items.isEmpty()) null else page + 1,
        )
    } catch (error: IOException) {
        LoadResult.Error(error)
    }

    override fun getRefreshKey(state: PagingState<Int, ItemDto>): Int? =
        state.anchorPosition?.let { anchor ->
            state.closestPageToPosition(anchor)?.prevKey?.plus(1)
                ?: state.closestPageToPosition(anchor)?.nextKey?.minus(1)
        }
}
```

Paging 3 is for long, ordered, server-paged lists. For lists that fit in a few pages, a
Room Flow plus `LIMIT`/`OFFSET` is simpler and keeps the whole list reactive.

## Failure modes

- **`IllegalStateException: Cannot perform this operation after onSaveInstanceState`** on
  a Fragment: you called `commit` after state was saved. Use `commitAllowingStateLoss()`
  only when the loss is genuinely acceptable.
- **`Room cannot verify the data integrity`** after changing an entity: bump the version
  and add a migration.
- **Work never runs**: constraints unsatisfied, app force-stopped, or a battery or data
  saver policy that the system will not override. WorkManager has no timing guarantees.
- **Navigation crash on deep link**: the route does not match, or required arguments are
  missing.
- **`Flow` never emits**: the Room query returns nothing and you used `first()` instead of
  collecting, or the table was never written.

## Sources

- Recommended app architecture: https://developer.android.com/topic/architecture
- Room: https://developer.android.com/training/data-storage/room
- Room migrations: https://developer.android.com/training/data-storage/room/migrations
- DataStore: https://developer.android.com/topic/libraries/architecture/datastore
- WorkManager: https://developer.android.com/develop/background-work/background-tasks/persistent/getting-started
- Navigation Compose: https://developer.android.com/develop/ui/compose/navigation
- Paging: https://developer.android.com/topic/libraries/architecture/paging/v3-overview
- Hilt: https://developer.android.com/training/dependency-injection/hilt-android