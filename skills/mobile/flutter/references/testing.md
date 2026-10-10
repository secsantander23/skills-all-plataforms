# Flutter testing reference

## Test categories

| Kind | Runs on | Use |
| --- | --- | --- |
| Unit test | VM, milliseconds | Pure Dart logic, models, services |
| Widget test | Headless engine | Widget tree, layout, gestures, finders |
| Integration test | Real device or emulator | Real platform plugins, end-to-end flows |

```dart
// test/cart_test.dart
import 'package:my_app/cart.dart';
import 'package:test/test.dart';

void main() {
  group('Cart', () {
    test('starts empty', () {
      expect(Cart().itemCount, 0);
    });

    test('adds an item', () {
      final Cart cart = Cart();
      cart.add('sku-1');
      expect(cart.itemCount, 1);
      expect(cart.items['sku-1'], 1);
    });
  });
}
```

## Widget test mechanics

`WidgetTester.pumpWidget` mounts the tree and renders the first frame.

```dart
testWidgets('renders the cart page', (WidgetTester tester) async {
  await tester.pumpWidget(
    const MaterialApp(home: CartPage()),
  );
  expect(find.byType(CartPage), findsOneWidget);
});
```

Finders:

| Finder | Matches |
| --- | --- |
| `find.text('Checkout')` | widgets whose text matches |
| `find.byType(IconButton)` | widget of that type |
| `find.byKey(const Key('cart-total'))` | widget with that key |
| `find.byIcon(Icons.delete)` | an `Icon` with that icon data |
| `find.bySemanticsLabel('Remove item')` | accessibility label |
| `find.descendant(of: find.byType(CartView), matching: find.text('Total'))` | scoped search |

Interactions:

```dart
await tester.tap(find.byIcon(Icons.add));
await tester.longPress(find.byType(ListTile));
await tester.enterText(find.byType(TextField), 'hello');
await tester.drag(find.byType(ListView), const Offset(0, -300));
await tester.scrollUntilVisible(find.text('Footer'), 200);
await tester.pump(const Duration(milliseconds: 300));
```

Prefer `scrollUntilVisible` over raw drags; a drag scrolls by a fixed distance and fails
once content length changes.

Assertions:

```dart
expect(find.text('Total'), findsOneWidget);
expect(find.text('Total'), findsNothing);
expect(find.text('Total'), findsNWidgets(3));
expect(find.byKey(const Key('cart-total')), findsOneWidget);
```

`findsWidgets` and `findsOneWidget` fail loudly; avoid `findsWidgets` unless you mean
"one or more".

## Timing

- `await tester.pump()` renders exactly one frame. Enough for `setState`.
- `await tester.pumpAndSettle()` repeats frames until nothing is scheduled. Enough for
  implicit animations, transitions, and Navigator pushes.
- `await tester.pump(const Duration(seconds: 1))` advances the fake clock by one second and
  renders one frame. Use for animations that never settle.
- `tester.runAsync(() async { ... })` runs real asynchronous work such as file or network
  I/O, which the fake async zone blocks. Its body must be genuinely awaited.

`pumpAndSettle` has a default timeout and throws
`pumpAndSettle timed out` when the tree keeps scheduling frames. That usually means a
progress indicator is spinning forever in the test environment.

## State management in tests

Provider:

```dart
testWidgets('adds an item from the cart page', (WidgetTester tester) async {
  final FakeCart cart = FakeCart();
  await tester.pumpWidget(
    ChangeNotifierProvider<CartModel>.value(
      value: cart,
      child: const MaterialApp(home: CartPage()),
    ),
  );

  await tester.tap(find.byIcon(Icons.add));
  expect(cart.itemCount, 1);
});
```

Riverpod:

```dart
testWidgets('shows items from a fake repository', (WidgetTester tester) async {
  await tester.pumpWidget(
    ProviderScope(
      overrides: [
        cartProvider.overrideWith(() => FakeCartNotifier()),
      ],
      child: const MyApp(),
    ),
  );
  expect(find.text('sku-1'), findsOneWidget);
});
```

For pure logic, skip the widget tree entirely and use a `ProviderContainer`:

```dart
test('cart totals update', () {
  final ProviderContainer container = ProviderContainer(
    overrides: [cartProvider.overrideWith(() => FakeCartNotifier())],
  );
  addTearDown(container.dispose);

  expect(container.read(cartProvider).itemCount, 0);
  container.read(cartProvider.notifier).add('sku-1');
  expect(container.read(cartProvider).itemCount, 1);
});
```

Always `addTearDown(container.dispose)` or providers leak across tests.

Bloc:

```dart
test('emits an added item state', () async {
  final CartBloc bloc = CartBloc();
  addTearDown(bloc.close);

  final Future<CartItemsState> next =
      bloc.stream.firstWhere((CartItemsState s) => s.items.isNotEmpty);

  bloc.add(const CartItemAdded('sku-1'));
  expect((await next).items, <String>['sku-1']);
});
```

## Golden tests

```dart
testWidgets('matches the light theme card', (WidgetTester tester) async {
  await tester.pumpWidget(
    const MaterialApp(
      home: Scaffold(body: ProfileCard(name: 'Ada', handle: '@ada')),
    ),
  );
  await expectLater(
    find.byType(ProfileCard),
    matchesGoldenFile('goldens/profile_card.png'),
  );
});
```

```bash
flutter test --update-goldens
flutter test --platform chrome     # for web goldens
```

Goldens are fragile across Flutter versions, platforms, and fonts. Pin the version, commit
the images, and review diffs carefully. Consider tolerance-based matchers or a dedicated
snapshot package if your CI runs on several platforms.

## Mocking

```dart
import 'package:mocktail/mocktail.dart';

class MockApi extends Mock implements Api {}

class FakeUser extends Fake implements User {}

void main() {
  setUpAll(() {
    registerFallbackValue(FakeUser());
  });

  test('renders users from the api', () async {
    final MockApi api = MockApi();
    when(() => api.fetchUsers()).thenAnswer((_) async => <User>[
      const User(id: '1', name: 'Ada'),
    ]);

    final UserListPage page = UserListPage(api: api);
    expect(await page.load(), hasLength(1));
    verify(() => api.fetchUsers()).called(1);
  });
}
```

`mocktail` needs `registerFallbackValue` for any non-nullable argument that is not
literally provided. `mockito` is the other mainstream option and generates mocks from
build_runner.

For pure Dart dependencies prefer hand-written fakes. They are faster, refactor safely,
and read better than generated mocks in a test.

## Keys in production code

Add keys to anything a test needs to find:

```dart
FilledButton(
  key: const Key('checkout-button'),
  onPressed: _checkout,
  child: const Text('Checkout'),
)
```

Then `find.byKey(const Key('checkout-button'))`. Tests that select by index or by sibling
text break on every UI tweak.

## Integration tests

```dart
// integration_test/app_test.dart
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:my_app/main.dart' as app;

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('signs in and lands on home', (WidgetTester tester) async {
    app.main();
    await tester.pumpAndSettle();

    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();

    expect(find.text('Welcome'), findsOneWidget);
  });
}
```

```bash
flutter test integration_test -d emulator-5554
flutter drive --driver=test_driver/integration_test.dart \
  --target=integration_test/app_test.dart -d <device-id>
```

Integration tests are slow. Keep the count small and put one per critical user path. Stub
or fake anything non-deterministic such as analytics or push.

## Performance

```bash
flutter test --coverage
genhtml coverage/lcov.info -o coverage/html
```

Unit and widget tests in the same run stay well inside CI budgets. Integration tests on
emulators do not; run them nightly or on a separate job.

## Sources

- Testing overview: https://docs.flutter.dev/testing/overview
- Widget tests: https://docs.flutter.dev/testing/widget-tests
- Unit tests: https://docs.flutter.dev/testing/unit-tests
- Integration tests: https://docs.flutter.dev/testing/integration-tests
- Common widget test failures: https://docs.flutter.dev/testing/widget-tests
- mocktail: https://pub.dev/packages/mocktail
- flutter_test API: https://api.flutter.dev/flutter_test/flutter_test/flutter_test-library.html