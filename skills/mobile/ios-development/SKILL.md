---
name: iOS Development
description: Covers native iOS app development in Swift including language essentials, the UIKit view controller lifecycle, SwiftUI declarative UI and state, Auto Layout and SwiftUI layout, Swift Concurrency with async await and actors, MVVM architecture, Core Data and SwiftData persistence, Xcode builds and code signing, and XCTest. Use when the user asks about a Swift class, a view controller, a SwiftUI view, an Xcode build failure, signing or provisioning errors, a concurrency crash, or a failing iOS test.
metadata:
  opencode/autoinvoke: true
---

# iOS Development

An iOS app is a bundle of resources plus an executable, signed and installed by the
system. Your code runs inside `UIApplication`, scenes own windows, and each window's root
is a view controller. Everything else is a detail of that hierarchy.

Two UI toolkits coexist. UIKit is imperative, object-based, and predates SwiftUI.
SwiftUI is declarative and value-based. They interoperate through `UIViewControllerRepresentable`
and `UIHostingController`. Pick one for a screen and mix at the boundary, not inside a
view.

## When to use this

- Writing a UIKit view controller, a SwiftUI view, or the app and scene delegates.
- Laying out with Auto Layout anchors or SwiftUI stacks.
- Moving blocking work off the main thread with async and await.
- Structuring an app with MVVM and an observable view model.
- Persisting with UserDefaults, Keychain, Core Data, or SwiftData.
- Fixing an Xcode build, signing, provisioning, or archiving problem.
- Writing XCTest or Swift Testing tests.

## Quick start

SwiftUI app with a scene-based lifecycle:

```swift
import SwiftUI

@main
struct MyApp: App {
    @State private var store = AppStore()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environment(store)
        }
    }
}

@Observable
final class AppStore {
    var items: [Item] = []
    var isLoading = false

    func load() async {
        isLoading = true
        defer { isLoading = false }
        items = (try? await ItemRepository.shared.fetch()) ?? []
    }
}
```

```swift
// ContentView.swift
import SwiftUI

struct ContentView: View {
    @Environment(AppStore.self) private var store

    var body: some View {
        NavigationStack {
            List(store.items) { item in
                Text(item.title)
            }
            .navigationTitle("Items")
            .overlay {
                if store.isLoading {
                    ProgressView()
                }
            }
            .task {
                await store.load()
            }
        }
    }
}
```

`@Observable` requires a recent OS and toolchain. For wider deployment targets use the
Combine-based pair instead:

```swift
import SwiftUI
import Combine

final class AppStore: ObservableObject {
    @Published var items: [Item] = []
    @Published var isLoading = false

    var cancellables = Set<AnyCancellable>()

    func load() async {
        isLoading = true
        defer { isLoading = false }
        items = (try? await ItemRepository.shared.fetch()) ?? []
    }
}

// at the root
ContentView().environmentObject(store)
```

In the Combine version, read values with `@EnvironmentObject` and own objects with
`@StateObject`. Verify which observation model your deployment target supports in the
Apple developer documentation.

## Swift essentials for app development

```swift
struct Item: Identifiable, Codable, Equatable {
    let id: UUID
    var title: String
    var price: Decimal
    var createdAt: Date
}

// Optionals, not nulls.
func label(for item: Item?) -> String {
    guard let item else { return "Unknown" }
    return item.title
}

// Value semantics for models, reference semantics for services.
protocol ItemRepository {
    func fetch() async throws -> [Item]
    func save(_ item: Item) async throws
}

// Errors are typed and thrown, not returned.
enum RepositoryError: Error {
    case offline
    case server(status: Int)
}

// Result combines both, useful at API boundaries.
func parse(_ data: Data) -> Result<Item, RepositoryError> { ... }
```

Rules that pay off:

- Use `struct` by default. Classes only when you need identity, inheritance, or mutable
  shared state.
- Prefer `async throws` to completion handlers. It composes with `async let`, `TaskGroup`,
  and structured cancellation.
- Mark types `Sendable` when they cross concurrency domains. Value types containing only
  immutable storage are implicitly `Sendable`.
- Avoid `force unwrap` (`!`) in shipping code. Every force unwrap is a crash you scheduled.
- Use `Result` at boundaries, `throws` internally.

## UIKit lifecycle

UIKit apps use `UIApplicationDelegate` plus a `UIWindowSceneDelegate`. The scene manifest
is declared in `Info.plist` under `UIApplicationSceneManifest`; without it the app may
never get a scene callback on some configurations.

```swift
import UIKit

@main
final class AppDelegate: UIResponder, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        true
    }

    func application(
        _ application: UIApplication,
        configurationForConnecting connectingSceneSession: UISceneSession,
        options: UIScene.ConnectionOptions
    ) -> UISceneConfiguration {
        UISceneConfiguration(name: "Default", sessionRole: connectingSceneSession.role)
    }

    func application(
        _ application: UIApplication,
        open url: URL,
        options: [UIApplication.OpenURLOptionsKey: Any] = [:]
    ) -> Bool {
        DeepLinkRouter.shared.handle(url)
    }
}
```

```swift
final class SceneDelegate: UIResponder, UIWindowSceneDelegate {
    var window: UIWindow?

    func scene(
        _ scene: UIScene,
        willConnectTo session: UISceneSession,
        options connectionOptions: UIScene.ConnectionOptions
    ) {
        guard let windowScene = scene as? UIWindowScene else { return }
        let window = UIWindow(windowScene: windowScene)
        window.rootViewController = UINavigationController(rootViewController: LoginViewController())
        window.makeKeyAndVisible()
        self.window = window

        if let urlContext = connectionOptions.urlContexts.first {
            DeepLinkRouter.shared.handle(urlContext.url)
        }
    }
}
```

View controller lifecycle:

```
init(nibName:bundle:) → loadView → viewDidLoad
  → viewWillAppear → viewDidAppear
  → viewWillDisappear → viewDidDisappear
```

- `viewDidLoad` runs once per view controller instance. Load data here.
- `viewWillAppear` and `viewDidAppear` run every time the controller becomes visible.
- `viewDidLayoutSubviews` runs after Auto Layout passes. Put layout code there if you must,
  but prefer constraints.
- `deinit` releases observers and timers.

```swift
final class ProfileViewController: UIViewController {
    private let viewModel: ProfileViewModel
    private var cancellables = Set<AnyCancellable>()

    init(viewModel: ProfileViewModel) {
        self.viewModel = viewModel
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .systemBackground
        viewModel.$profile
            .receive(on: DispatchQueue.main)
            .sink { [weak self] profile in
                self?.render(profile)
            }
            .store(in: &cancellables)
    }

    private func render(_ profile: Profile?) {
        title = profile?.displayName ?? "Profile"
    }
}
```

`[weak self]` in the sink closure breaks the retain cycle between the view model and the
controller. Without it the controller never deallocates.

## Auto Layout

Constraints solve equations. A view needs enough constraints to know its position and size
on both axes.

```swift
final class LoginViewController: UIViewController {
    override func viewDidLoad() {
        super.viewDidLoad()

        let titleLabel = UILabel()
        titleLabel.text = "Sign in"
        titleLabel.font = .preferredFont(forTextStyle: .largeTitle)

        let emailField = UITextField()
        emailField.borderStyle = .roundedRect
        emailField.placeholder = "Email"
        emailField.keyboardType = .emailAddress
        emailField.textContentType = .emailAddress

        let stack = UIStackView(arrangedSubviews: [titleLabel, emailField])
        stack.axis = .vertical
        stack.spacing = 12

        view.addSubview(stack)
        stack.translatesAutoresizingMaskIntoConstraints = false

        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor, constant: 24),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor, constant: -24),
            emailField.heightAnchor.constraint(equalToConstant: 44),
        ])
    }
}
```

Layout priority is a tool, not a fix for ambiguity. Making everything `required` and then
lowering priority until the log goes quiet produces a layout that changes at runtime with
device sizes. Find and remove the missing constraint instead.

```swift
view.translatesAutoresizingMaskIntoConstraints = false   // always for Auto Layout
label.setContentCompressionResistancePriority(.defaultLow, for: .horizontal)
```

Dynamic Type: use `UIFont.preferredFont(forTextStyle:)` and
`adjustsFontForContentSizeCategory = true`. Test at the largest accessibility sizes.

`UITableView` and `UICollectionView` are the right choice for large data sets. `UIScrollView`
with subviews is not.

## SwiftUI

```swift
struct ItemDetailView: View {
    let item: Item
    @State private var isEditing = false
    @State private var quantity = 1
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        Form {
            Section("Item") {
                LabeledContent("Title", value: item.title)
                LabeledContent("Price", value: item.price.formatted(.currency(code: "USD")))
            }
            Section("Quantity") {
                Stepper("Quantity: \(quantity)", value: $quantity, in: 1...99)
            }
            if isEditing {
                Section {
                    Button("Delete", role: .destructive) { delete() }
                }
            }
        }
        .navigationTitle(item.title)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Edit") { isEditing.toggle() }
            }
        }
        .task {
            await loadRelated()
        }
        .onChange(of: quantity) { oldValue, newValue in
            recomputeTotal(oldValue: oldValue, newValue: newValue)
        }
        .alert("Delete this item?", isPresented: $showDeleteAlert) {
            Button("Delete", role: .destructive) { delete() }
            Button("Cancel", role: .cancel) {}
        }
    }

    @State private var showDeleteAlert = false

    private func delete() { dismiss() }
    private func loadRelated() async {}
    private func recomputeTotal(oldValue: Int, newValue: Int) {}
}
```

Property wrappers:

| Wrapper | Purpose |
| --- | --- |
| `@State` | Local, owned by the view. Required for anything the view changes |
| `@Binding` | Two-way reference to state owned by an ancestor |
| `@Observable` / `@Environment` | Read an `@Observable` object from the environment (recent OS) |
| `@EnvironmentObject` | Read an `ObservableObject` from the environment (Combine) |
| `@StateObject` | Own an `ObservableObject` for the view's lifetime |
| `@Environment(\.dismiss)` | Dismiss the current presentation |
| `@AppStorage` | Persist a small value in UserDefaults |
| `@SceneStorage` | Persist state per scene, restored after termination |
| `@FocusState` | Manage which field has focus |
| `@Binding` with custom init | Constructors that accept bindings use `_name = Binding(initialValue:)` or an explicit `init(_ name: Binding<T>)` |

SwiftUI layout is a top-down size negotiation. `Spacer`, `Divider`, and `Color.clear` all
expand to fill available space, which is why stacking three of them produces a giant empty
view.

```swift
VStack(alignment: .leading, spacing: 12) {
    Text("Title")
    Spacer(minLength: 0)
    Text("Footer")
}
.frame(maxWidth: .infinity, alignment: .leading)
```

SwiftUI views should be small and take values. A `View` that reads a global singleton in
`body` is untestable. Pass dependencies in.

Interop:

```swift
struct WebContainer: UIViewRepresentable {
    let url: URL

    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        return WKWebView(frame: .zero, configuration: configuration)
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        if webView.url != url {
            webView.load(URLRequest(url: url))
        }
    }
}

struct ProfileHost: UIViewControllerRepresentable {
    func makeUIViewController(context: Context) -> ProfileViewController {
        ProfileViewController(viewModel: ProfileViewModel())
    }

    func updateUIViewController(_ controller: ProfileViewController, context: Context) {}
}
```

`updateUIView` and `updateUIViewController` run on every parent re-render. Keep them cheap
and idempotent; guard the expensive operation with an equality check as above.

## Swift Concurrency

```swift
actor ImageCache {
    private var storage: [URL: UIImage] = [:]

    func image(for url: URL) -> UIImage? { storage[url] }

    func store(_ image: UIImage, for url: URL) {
        storage[url] = image
    }
}

@MainActor
final class ProfileViewModel {
    private(set) var profile: Profile?
    private(set) var state: LoadState = .idle

    func load() async {
        state = .loading
        do {
            profile = try await ProfileService().fetch(id: id)
            state = .loaded
        } catch {
            state = .failed(error)
        }
    }
}
```

- Mark UI-touching types `@MainActor`. Then `await` from the main actor hops off
  automatically for genuinely blocking work.
- `Task {}` inherits the actor context. `Task.detached` does not; use it only for work with
  no actor affinity.
- Tasks are cancelled cooperatively. Check `Task.isCancelled` in loops, or use
  `Task.checkCancellation()`.
- `withTaskGroup` and `async let` give structured concurrency: children are awaited and
  cancelled together.
- URLSession: `try await URLSession.shared.data(from: request)`.

```swift
func loadAll(ids: [UUID]) async throws -> [Profile] {
    try await withThrowingTaskGroup(of: Profile.self) { group in
        for id in ids.prefix(8) {
            group.addTask { try await ProfileService().fetch(id: id) }
        }
        var results: [Profile] = []
        for try await profile in group {
            results.append(profile)
        }
        return results
    }
}
```

```swift
// Cancellable one-shot work
@MainActor
final class SearchViewModel {
    private var task: Task<Void, Never>?

    func queryChanged(_ text: String) {
        task?.cancel()
        task = Task {
            try? await Task.sleep(nanoseconds: 300_000_000)  // debounce
            guard !Task.isCancelled else { return }
            results = await search(text)
        }
    }

    deinit { task?.cancel() }
}
```

Bridge to Combine when you must:

```swift
@MainActor
final class LegacyModel {
    let subject = PassthroughSubject<String, Never>()
}

// SwiftUI
.onReceive(model.subject) { value in handle(value) }
```

Classic concurrency failures:

- `data races` from mutable reference types crossing isolation. Mark them `@MainActor`,
  `Sendable`, or confine them to an actor.
- `Task { }` escaping its scope and firing after the screen is gone. Hold the task and
  cancel in `deinit`.
- `Sendable` warnings for closures capturing non-Sendable types.
- Reentrancy: an `await` inside an actor method suspends, and other work runs before the
  method resumes. Re-read state after every `await`.

## MVVM

```swift
@MainActor
final class ProductsViewModel: ObservableObject {
    @Published private(set) var products: [Product] = []
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private let repository: ProductRepository

    init(repository: ProductRepository) {
        self.repository = repository
    }

    func load() async {
        guard !isLoading else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            products = try await repository.fetch()
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
```

The view model owns state and formatting. The view owns layout. The repository owns
networking and persistence. Inject the repository so tests can substitute a fake.

```swift
@MainActor
struct ProductsView: View {
    @StateObject private var viewModel: ProductsViewModel

    init(repository: ProductRepository) {
        _viewModel = StateObject(wrappedValue: ProductsViewModel(repository: repository))
    }

    var body: some View {
        List(viewModel.products) { product in
            Text(product.name)
        }
        .task { await viewModel.load() }
    }
}
```

## Persistence

```swift
// Small values
UserDefaults.standard.set(token, forKey: "auth.token")

// Keychain
import Security

enum Keychain {
    static func set(_ value: String, for key: String) throws {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrAccount as String: key,
            kSecValueData as String: data,
        ]
        SecItemDelete(query as CFDictionary)
        var attributes = query
        attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        let status = SecItemAdd(attributes as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainError(status: status) }
    }
}
```

Core Data for a relational store, with a managed object context you pass around or access
via the view context on the main queue.

SwiftData is the modern model layer:

```swift
import SwiftData

@Model
final class Trip {
    var name: String
    var startDate: Date

    init(name: String, startDate: Date) {
        self.name = name
        self.startDate = startDate
    }
}

@main
struct MyApp: App {
    var container: ModelContainer = {
        do {
            return try ModelContainer(for: Trip.self)
        } catch {
            fatalError("Model container failed: \(error)")
        }
    }()

    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        .modelContainer(container)
    }
}

struct TripList: View {
    @Query(sort: \Trip.startDate) private var trips: [Trip]
    @Environment(\.modelContext) private var context

    var body: some View {
        List(trips) { trip in
            Text(trip.name)
        }
        .onDelete { indexSet in
            for index in indexSet {
                context.delete(trips[index])
            }
        }
    }
}
```

Check the minimum OS version SwiftData requires before adopting it; it is not available on
older deployment targets.

## Xcode build and signing

- **Workspace vs project.** A `.xcworkspace` includes CocoaPods or Swift Package Manager
  dependencies. Always open the workspace after `pod install`.
- **Target.** A product (app, framework, test bundle) plus its build phases, sources, and
  settings.
- **Scheme.** How Xcode builds, runs, tests, and profiles a target. Share the scheme.
- **Build configuration.** Debug and Release. Set per target or per project.

Build settings that matter:

| Setting | Meaning |
| --- | --- |
| `PRODUCT_BUNDLE_IDENTIFIER` | Reverse-DNS identifier; must match provisioning |
| `DEVELOPMENT_TEAM` | Apple Developer team ID |
| `CODE_SIGN_STYLE` | `Automatic` or `Manual` |
| `CODE_SIGN_ENTITLEMENTS` | Path to the entitlements file |
| `IPHONEOS_DEPLOYMENT_TARGET` | Minimum supported iOS version |
| `SWIFT_VERSION` | Swift language mode |
| `INFOPLIST_FILE` | Location of Info.plist |
| `PROVISIONING_PROFILE_SPECIFIER` | Profile name when signing manually |
| `OTHER_LDFLAGS` | Extra linker flags; `$(inherited)` matters |

Signing chain: an App ID (bundle identifier plus capabilities) belongs to a team, a
provisioning profile ties the App ID to a set of devices or a distribution method, and a
certificate signs the build. Automatic signing asks Xcode to create and refresh these.
Manual signing names them explicitly.

Entitlements gate capabilities. Common ones: push notifications, associated domains for
Universal Links, keychain sharing, app groups, and Sign in with Apple. The entitlements file
must match the App ID's enabled capabilities or the build fails at signing.

```bash
xcodebuild -version
xcodebuild -list -workspace MyApp.xcworkspace
xcodebuild -workspace MyApp.xcworkspace -scheme MyApp -showBuildSettings
xcodebuild -workspace MyApp.xcworkspace -scheme MyApp \
  -destination 'generic/platform=iOS Simulator' build
xcodebuild -workspace MyApp.xcworkspace -scheme MyApp \
  -destination 'platform=iOS Simulator,name=iPhone 15' test
xcrun simctl list devices available
```

Simulator device names depend on your installed Xcode. List them rather than copying a name
from a tutorial.

## Testing

```swift
import XCTest
@testable import MyApp

final class PriceFormatterTests: XCTestCase {
    func testFormatsUSD() {
        let formatter = PriceFormatter(currencyCode: "USD")
        XCTAssertEqual(formatter.string(from: 12.5), "$12.50")
    }

    func testEmptyInput() {
        XCTAssertNil(PriceFormatter(currencyCode: "USD").decimal(from: ""))
    }
}
```

Async tests:

```swift
final class RepositoryTests: XCTestCase {
    func testFetchReturnsItems() async throws {
        let repository = FakeItemRepository(items: [Item(title: "A")])
        let items = try await repository.fetch()
        XCTAssertEqual(items.count, 1)
    }

    func testWaitsForCallback() {
        let expectation = expectation(description: "callback fired")
        DispatchQueue.main.async { expectation.fulfill() }
        wait(for: [expectation], timeout: 1.0)
    }
}
```

`wait(for:timeout:)` blocks the test thread. If the expectation is not fulfilled, the test
fails; if it is fulfilled on the main thread while the main thread is blocked, it deadlocks.
Never call `wait` on the main thread expecting main-thread work.

Swift Testing is available in recent toolchains as a second framework (`import Testing`,
`@Test`, `#expect`). Check whether your Xcode version supports it before choosing.

```bash
xcodebuild test -workspace MyApp.xcworkspace -scheme MyApp \
  -destination 'platform=iOS Simulator,name=iPhone 15'
```

Test host: unit tests need a host application if they touch application code. UI tests use
`XCUIApplication` and launch the app.

## Checklist

- [ ] Scene manifest in `Info.plist` and a real `UIWindow` set in the scene delegate.
- [ ] `deinit` or Combine cancellation releases observers; no retain cycles.
- [ ] UI work marked `@MainActor`; heavy work off the main thread.
- [ ] Long-running `Task` cancelled when its owner deallocates.
- [ ] Constraints complete: no priority-driven ambiguity in the console.
- [ ] Dynamic Type supported and tested at accessibility sizes.
- [ ] Dependencies injected, not reached for as globals.
- [ ] Workspace opened after `pod install`, entitlements match App ID capabilities.
- [ ] Release build archived with the distribution profile and exported.
- [ ] Tests cover the view model and repository, not just the happy path of the view.

## Common mistakes

- **`Terminating app due to uncaught exception 'NSInternalInconsistencyException'`**
  from a force unwrap of a nil optional. Guard instead.
- **App launches to a black screen.** No root view controller set in the scene delegate, or
  a constraint conflict collapsing the view. Check the scene configuration and the console.
- **`Unable to simultaneously satisfy constraints.`** A view has two constraints for the
  same dimension, or a required one conflicts. Find the redundant or conflicting pair.
- **`No such module 'X'`** on device but not in the simulator. The dependency is not linked
  into the target; check the framework's embed and sign phase.
- **`Failed to register bundle identifier` / `No profiles for` during signing.** The App ID
  or profile does not match. Run automatic signing or fix the team and bundle identifier.
- **Entitlement error at install time: `ApplicationVerification failed`.** The profile lacks
  a capability the entitlements file claims. Align them.
- **Deadlock with `wait(for:timeout:)` on the main thread.** Move the wait off the main
  thread or use `async` tests.
- **Data race crash or TSAN warning.** Shared mutable reference types crossing isolation;
  mark them `@MainActor`, `Sendable`, or confine them to an actor.

## Anti-patterns

- Networking directly from a view controller. Put it behind a repository the view model
  owns.
- Reaching for singletals everywhere. They make tests order-dependent.
- Long files of force unwraps that defer crashes to production.
- `DispatchQueue.main.async` sprinkled around instead of `@MainActor` isolation.
- Starting work in `viewDidLoad` that is never cancelled when the user leaves.
- Hard-coding deployment targets above your real minimum without checking SwiftData,
  Observation, or other API availability.
- Ignoring the accessibility text size and VoiceOver labels.
- Shipping a build that was only ever debug-signed.

## References

- Apple Developer documentation: https://developer.apple.com/documentation/
- Swift language guide: https://www.swift.org/documentation/the-swift-programming-language/
- Swift Concurrency: https://www.swift.org/documentation/tspl/StructuredConcurrency/
- SwiftUI framework: https://developer.apple.com/documentation/swiftui
- UIKit framework: https://developer.apple.com/documentation/uikit
- Auto Layout guide: https://developer.apple.com/documentation/uikit/views-and-layout/understanding-auto-layout
- Human Interface Guidelines: https://developer.apple.com/design/human-interface-guidelines
- App Store review guidelines: https://developer.apple.com/app-store/review/guidelines/
- Core Data: https://developer.apple.com/documentation/coredata
- SwiftData: https://developer.apple.com/documentation/swiftdata
- XCTest: https://developer.apple.com/documentation/xctest
- Bundle resources and Info.plist keys: https://developer.apple.com/documentation/bundleresources/information-property-list