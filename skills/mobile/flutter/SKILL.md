---
name: Flutter
description: Covers Flutter development including the widget tree and BuildContext, stateless versus stateful widgets, layout and constraints, state management with setState, Provider, Riverpod and Bloc, navigation with Navigator and go_router, platform channels, performance with const widgets and rebuild scoping, and widget testing. Use when the user asks about a Flutter widget, layout overflow, state management choice, navigation, MethodChannel, performance regression, or a failing widget test.
metadata:
  opencode/autoinvoke: true
---

# Flutter

Flutter builds a tree of widgets, turns that tree into a retained layer tree of rendering
objects, and composites the result with Skia or Impeller. Your code only ever builds
widgets. Everything expensive happens below, driven by the widget tree.

The two ideas that resolve most Flutter confusion:

1. **Widgets are immutable descriptions.** They are configuration, not objects with
   identity. A `build` method must be a pure function of its inputs plus any state the
   framework owns.
2. **The parent sets constraints, the child chooses its size.** Layout in Flutter is a
   negotiation, not CSS. Most layout errors come from handing a widget an unbounded
   constraint and then asking it to be bounded.

## When to use this

- Writing or reviewing Flutter widgets and screens.
- Deciding between `setState`, Provider, Riverpod, Bloc, or plain `ValueNotifier`.
- Diagnosing `RenderFlex overflowed by X pixels` or `incoming width constraints are
  unbounded`.
- Wiring native code through `MethodChannel`, `EventChannel`, or `BasicMessageChannel`.
- Cutting rebuild count, jank, or binary size.
- Writing widget tests that do not flake.

## Quick start

```dart
import 'package:flutter/material.dart';

class CounterPage extends StatefulWidget {
  const CounterPage({super.key, required this.title});

  final String title;

  @override
  State<CounterPage> createState() => _CounterPageState();
}

class _CounterPageState extends State<CounterPage> {
  int _count = 0;

  void _increment() {
    // Guard against setState after dispose when an async gap exists.
    if (!mounted) return;
    setState(() => _count++);
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: Text(widget.title)),
      body: Center(
        child: Column(
          mainAxisAlignment: MainAxisAlignment.center,
          children: <Widget>[
            const Text('You have pushed the button this many times:'),
            Text('$_count', style: Theme.of(context).textTheme.headlineMedium),
          ],
        ),
      ),
      floatingActionButton: FloatingActionButton(
        onPressed: _increment,
        tooltip: 'Increment',
        child: const Icon(Icons.add),
      ),
    );
  }
}
```

App entry point:

```dart
import 'package:flutter/material.dart';

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'My App',
      theme: ThemeData(colorSchemeSeed: Colors.indigo, useMaterial3: true),
      darkTheme: ThemeData(
        colorSchemeSeed: Colors.indigo,
        brightness: Brightness.dark,
        useMaterial3: true,
      ),
      home: const CounterPage(title: 'Counter'),
    );
  }
}
```

## The widget tree, context, and keys

- Every widget has a `BuildContext`. `Theme.of(context)`, `MediaQuery.of(context)`,
  `Navigator.of(context)`, and `ScaffoldMessenger.of(context)` all walk up the tree from
  that context.
- Calling `of(context)` inside `build` creates a dependency. When the inherited widget
  changes, this widget rebuilds. Wrap in `Builder` or split the subtree to narrow the
  rebuild.
- Keys identify a widget across rebuilds and across list reordering. Use `ValueKey` for
  stable data identity, `ObjectKey` for object identity, `UniqueKey` to force a fresh
  subtree, and `GlobalKey` only when you need access to the state from outside the tree.

```dart
ListView.builder(
  itemCount: items.length,
  itemBuilder: (BuildContext context, int index) {
    final Item item = items[index];
    return Dismissible(
      key: ValueKey<String>(item.id),
      onDismissed: (_) => removeItem(item.id),
      child: ListTile(title: Text(item.label)),
    );
  },
)
```

An unkeyed widget in a reorderable list is the usual cause of
`Duplicate GlobalKey detected in widget tree` and of state jumping to the wrong row.

## Stateless versus stateful

Use `StatelessWidget` unless the widget must react to something the framework does not
already know about. Local toggles, animation controllers, and form controllers are
arguments for a stateful widget.

The `State` object holds state that outlives a single build. `initState` runs once,
`didUpdateWidget` runs when the parent supplies new props while the `State` survives, and
`dispose` runs when the widget leaves the tree. Anything acquired in `initState` must be
released in `dispose`.

```dart
class PriceField extends StatefulWidget {
  const PriceField({super.key, required this.onChanged});

  final ValueChanged<double> onChanged;

  @override
  State<PriceField> createState() => _PriceFieldState();
}

class _PriceFieldState extends State<PriceField> {
  late final TextEditingController _controller;

  @override
  void initState() {
    super.initState();
    _controller = TextEditingController();
    _controller.addListener(() {
      final double? value = double.tryParse(_controller.text);
      if (value != null) widget.onChanged(value);
    });
  }

  @override
  void dispose() {
    _controller
      ..removeListener(_emptyListener)
      ..dispose();
    super.dispose();
  }

  void _emptyListener() {}

  @override
  Widget build(BuildContext context) {
    return TextField(
      controller: _controller,
      keyboardType: const TextInputType.numberWithOptions(decimal: true),
      decoration: const InputDecoration(labelText: 'Price'),
    );
  }
}
```

Async callbacks must check `mounted` before using `context` or calling `setState` after
an `await`. `use_build_context_synchronously` is a lint that catches most of these; run
`flutter analyze` in CI.

## Layout and constraints

The constraint box is a min/max range on each axis. `Container` without explicit size
becomes as large as the constraints allow. Inside a `Column` or `ListView` the vertical
constraint is unbounded, so a child that tries to be infinitely tall throws.

The canonical error:

```
RenderFlex children have non-zero flex but incoming height constraints are unbounded.
```

Fix it by bounding the parent:

```dart
ListView(
  children: <Widget>[
    Flexible(child: SingleChildScrollView(child: orderDetails)),
    const SizedBox(height: 24),
    FilledButton(onPressed: submit, child: const Text('Submit')),
  ],
)
```

Overflow:

```
A RenderFlex overflowed by 42 pixels on the right.
```

The row wanted more space than the screen allows. Fixes, in order of preference: use
`Expanded` or `Flexible`, wrap text in `Flexible` and let it wrap, switch to a `ListView`
for the whole page, or use `LayoutBuilder` to switch to a column layout below a width
threshold.

Constraint-aware layout:

```dart
LayoutBuilder(
  builder: (BuildContext context, BoxConstraints constraints) {
    if (constraints.maxWidth < 600) {
      return const _NarrowLayout();
    }
    return const _WideLayout();
  },
)
```

Never call `MediaQuery.of(context).size` and ignore the actual constraints the widget
received. Inside a dialog or a split view, `MediaQuery` and the real box disagree, and
using `MediaQuery` produces overflow. Use `LayoutBuilder`.

| Widget | Use for |
| --- | --- |
| `Row` / `Column` | Linear layout on one axis |
| `Expanded` | Force a child to fill remaining main-axis space |
| `Flexible` | Allow a child up to remaining space, less if it wants less |
| `Spacer` | `Expanded` with no child |
| `Stack` | Overlapping children; first child is the base layer |
| `Positioned` | Non-positioned children only inside a `Stack` |
| `SizedBox` / `Container` | Fixed or constrained space |
| `Padding` | Space inside the child's own box |
| `ListView.builder` | Lazy vertical list |
| `GridView.builder` | Lazy grid |
| `SingleChildScrollView` | Small known content that must scroll |

`SizedBox.expand`, `SizedBox.shrink`, `Center`, `Align`, `AspectRatio`, `FractionallySizedBox`,
`ConstrainedBox`, and `LimitedBox` cover most of the rest.

## State management

Decision table. Pick the lightest option that solves the sharing problem.

| Need | Use | Cost |
| --- | --- | --- |
| One widget, ephemeral | `setState` | None |
| One value, many listeners, below a subtree | `ValueListenableBuilder` / `ValueNotifier` | None |
| Scoped, app-lifetime values, mockable in tests | `provider` (`ChangeNotifier`, `Provider`, `Consumer`) | Low |
| Compile-time-safe, granular, testable, ref-counted disposal | `riverpod` / `flutter_riverpod` | Medium |
| Event-sourced, log of transitions, teams already know Redux | `bloc` / `flutter_bloc` | Highest |
| Pure immutable derived values only | `signals` package | Low |

### Provider

```dart
import 'package:flutter/foundation.dart';

class CartModel extends ChangeNotifier {
  final Map<String, int> _items = <String, int>{};

  int get itemCount => _items.length;

  void add(String sku) {
    _items[sku] = (_items[sku] ?? 0) + 1;
    notifyListeners();
  }

  void remove(String sku) {
    final int? next = _items[sku];
    if (next == null) return;
    if (next <= 1) {
      _items.remove(sku);
    } else {
      _items[sku] = next - 1;
    }
    notifyListeners();
  }
}
```

```dart
void main() {
  runApp(
    ChangeNotifierProvider<CartModel>(
      create: (_) => CartModel(),
      child: const MyApp(),
    ),
  );
}
```

Read inside `build` with `context.watch<CartModel>()`, or outside build with
`context.read<CartModel>()`. Watching a `ChangeNotifier` rebuilds the reading widget on
every notification. Narrow the scope: extract the leaf widget that actually needs the
value instead of watching in a big page widget. `context.select` rebuilds only when the
selected value changes.

### Riverpod

```dart
import 'package:flutter_riverpod/flutter_riverpod.dart';

final cartProvider =
    NotifierProvider<CartNotifier, CartState>(CartNotifier.new);

@immutable
class CartState {
  const CartState({this.items = const <String, int>{}});

  final Map<String, int> items;

  int get itemCount => items.values.fold(0, (int a, int b) => a + b);

  CartState copyWith({Map<String, int>? items}) =>
      CartState(items: items ?? this.items);
}

class CartNotifier extends Notifier<CartState> {
  @override
  CartState build() => const CartState();

  void add(String sku) {
    state = state.copyWith(
      items: <String, int>{...state.items, sku: (state.items[sku] ?? 0) + 1},
    );
  }
}
```

Consumption rules:

- `ref.watch(provider)` in a widget's `build` subscribes and rebuilds on change.
- `ref.read(provider)` reads once without subscribing. Use it in callbacks.
- `ref.listen(provider, (previous, next) { ... })` reacts without rebuilding.
- Providers dispose their resources when the container or scope is disposed, which makes
  testing straightforward: override a provider in tests and let the scope clean up.

### Bloc

```dart
import 'package:bloc/bloc.dart';

sealed class CartEvent {
  const CartEvent();
}

final class CartItemAdded extends CartEvent {
  const CartItemAdded(this.sku);

  final String sku;
}

final class CartCleared extends CartEvent {
  const CartCleared();
}

class CartItemsState {
  const CartItemsState({this.items = const <String>[]});

  final List<String> items;

  CartItemsState copyWith({List<String>? items}) =>
      CartItemsState(items: items ?? this.items);
}

class CartBloc extends Bloc<CartEvent, CartItemsState> {
  CartBloc({List<String> initial = const <String>[]})
      : super(CartItemsState(items: initial)) {
    on<CartItemAdded>(_onAdded);
    on<CartCleared>((CartCleared event, Emitter<CartItemsState> emit) {
      emit(const CartItemsState());
    });
  }

  void _onAdded(CartItemAdded event, Emitter<CartItemsState> emit) {
    emit(state.copyWith(items: <String>[...state.items, event.sku]));
  }
}
```

Use `BlocBuilder` to rebuild on state and `BlocListener` for side effects such as
navigation or SnackBars. A `BlocListener` in `build` runs its callback on state change,
which is the wrong place for one-time side effects.

## Navigation

`Navigator` is an imperative stack. `go_router` is a declarative router built on the
Navigator API and is the usual choice for apps.

```dart
// Plain Navigator
Navigator.of(context).push(
  MaterialPageRoute<void>(
    builder: (BuildContext context) => const DetailsPage(itemId: itemId),
  ),
);

final String? answer = await Navigator.of(context).push<String>(
  MaterialPageRoute<String>(builder: (_) => const PickOnePage()),
);
```

```dart
// go_router
final GoRouter router = GoRouter(
  initialLocation: '/',
  routes: <RouteBase>[
    GoRoute(
      path: '/',
      builder: (BuildContext context, GoRouterState state) => const HomePage(),
      routes: <RouteBase>[
        GoRoute(
          path: 'items/:id',
          builder: (BuildContext context, GoRouterState state) =>
              DetailsPage(itemId: state.pathParameters['id']!),
        ),
      ],
    ),
  ],
);

MaterialApp.router(routerConfig: router, title: 'My App');
```

Router 2.0 is the underlying mechanism: a `RouterDelegate` owns navigation state, a
`RouteInformationParser` converts a `RouteInformation` into a match, and the router
reports the new location back to the platform. `go_router`, `flutter_bloc`'s router, and
`auto_route` all implement that contract.

Notes that bite:

- `context.go` replaces the stack, `context.push` pushes on top. Mixing them produces
  surprising back behavior.
- `push` twice for the same route creates two identical entries. Guard against double
  taps.
- Modal routes such as dialogs and bottom sheets use `showDialog` and
  `showModalBottomSheet`, not `push` with a custom `PageRoute`.
- Call `SystemNavigator.pop()` or `context.pop()` only if `canPop()` is true.

## Platform channels

```dart
import 'package:flutter/services.dart';

class Battery {
  static const MethodChannel _channel = MethodChannel('com.example.app/battery');

  static Future<double> level() async {
    final double? value =
        await _channel.invokeMethod<double>('getBatteryLevel');
    if (value == null) {
      throw const PlatformException(
        code: 'UNAVAILABLE',
        message: 'Battery level was not reported',
      );
    }
    return value;
  }
}
```

Event streams:

```dart
final EventChannel _events = const EventChannel('com.example.app/battery');

Stream<double> batteryChanges() =>
    _events.receiveBroadcastStream().map((dynamic e) => e as double);
```

Host side, Kotlin, in a plugin's `FlutterPlugin`:

```kotlin
class BatteryPlugin : FlutterPlugin, MethodChannel.MethodCallHandler {
  private var channel: MethodChannel? = null

  override fun onAttachedToEngine(binding: FlutterPlugin.FlutterPluginBinding) {
    channel = MethodChannel(binding.binaryMessenger, "com.example.app/battery")
    channel?.setMethodCallHandler(this)
  }

  override fun onDetachedFromEngine(binding: FlutterPlugin.FlutterPluginBinding) {
    channel?.setMethodCallHandler(null)
    channel = null
  }

  override fun onMethodCall(call: MethodCall, result: MethodChannel.Result) {
    when (call.method) {
      "getBatteryLevel" -> result.success(readBatteryLevel())
      else -> result.notImplemented()
    }
  }
}
```

Host side, Swift, in an `FlutterPlugin` type:

```swift
public func handle(_ call: FlutterMethodCall, result: @escaping FlutterResult) {
  switch call.method {
  case "getBatteryLevel":
    UIDevice.current.isBatteryMonitoringEnabled = true
    let level = UIDevice.current.batteryLevel
    result(level < 0 ? FlutterError(code: "UNAVAILABLE",
                                    message: "Battery level unavailable",
                                    details: nil)
                   : Double(level))
  default:
    result(FlutterMethodNotImplemented)
  }
}
```

Dart, Swift, and Kotlin must agree on the channel name string exactly. `MissingPluginException`
means the platform handler was never registered, or the app was not rebuilt. Always call
`notImplemented()` / `FlutterMethodNotImplemented` for unknown methods, and clear the handler
in `onDetachedFromEngine`. For plugins, register in the plugin class rather than the app
delegate so it works when another app consumes the plugin. Full plugin packaging,
`EventChannel` streams, typed arguments, and isolate channels are in
`references/platform-channels.md`.

## Performance

Ranked by how much they matter in practice.

1. **Use `const` constructors.** A `const` widget is canonicalized and reused, so
   identical subtrees do not rebuild. Add `const` everywhere it compiles; it is a compile
   error if a widget subtree cannot be constant, so it doubles as a guard.
2. **Keep `build` cheap.** No JSON parsing, no sorting large lists, no database reads, no
   `DateTime.now()` formatting of many items inside `build`. Compute in the model or in
   `initState`.
3. **Scope rebuilds.** Extract small widgets. Use `Selector` or `ref.watch` on the narrow
   provider, not a broad notifier.
4. **Isolate heavy work.** `compute()` takes a top-level function and a message and runs it
   on a background isolate. `Isolate.run` is the modern entry point; check the concurrency
   docs for what your Flutter version supports.
5. **Build lists lazily.** `ListView.builder` and `ListView.separated` build items on
   demand. `ListView(children: [...])` builds everything.
6. **Constrain image decoding.** `Image.memory` and `NetworkImage` accept `cacheWidth` and
   `cacheHeight`. Decoding a large image at full size is the most common cause of jank.
7. **Avoid transparency and shadows on scrolling lists.** They force extra compositing
   passes. `RepaintBoundary` limits repaints but costs memory.
8. **Profile before and after.** Use `flutter run --profile` and DevTools. Debug builds
   disable optimizations and will mislead you.

Startup and binary size:

```bash
flutter build apk --release --split-per-abi
flutter build appbundle --release
flutter build ipa --release
flutter build ios --release --no-codesign
flutter run --profile
flutter build apk --release --dart-define=API_URL=https://api.example.com
flutter build apk --flavor dev --dart-define=API_URL=https://dev.example.com
flutter build apk --release --analyze-size
flutter tree-shake-icons
```

Release mode enables AOT compilation and tree shaking. Split APKs reduce download size at
the cost of one build per ABI. `--analyze-size` reports what dominates.

## Widget testing

```dart
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  testWidgets('increments the counter', (WidgetTester tester) async {
    await tester.pumpWidget(const MyApp());

    expect(find.text('0'), findsOneWidget);
    expect(find.text('1'), findsNothing);

    await tester.tap(find.byIcon(Icons.add));
    await tester.pump();

    expect(find.text('0'), findsNothing);
    expect(find.text('1'), findsOneWidget);
  });
}
```

The rules that prevent flakes:

- `tester.pump()` renders one frame. `tester.pumpAndSettle()` pumps until no frames are
  scheduled. Use `pumpAndSettle` for animations and navigation, `pump` for a single state
  change. `pumpAndSettle` hangs on an infinite animation such as a `CircularProgressIndicator`
  that never stops, or on a `Stream.periodic`. Use `tester.pump(Duration)` there.
- Never use `Future.delayed` in tests. Use `tester.pump` and `fakeAsync`-controlled time.
- Key widgets with `Key('add-button')` and find them with `find.byKey`. Do not rely on
  positional finders that break when the tree changes.
- `setUp` should reset or override providers so tests do not leak state. Riverpod supports
  `ProviderContainer` overrides; `provider` supports wrapping the tree.

```dart
testWidgets('shows the empty state', (WidgetTester tester) async {
  await tester.pumpWidget(
    ChangeNotifierProvider<CartModel>(
      create: (_) => CartModel(),
      child: const MaterialApp(home: CartPage()),
    ),
  );
  expect(find.text('Your cart is empty'), findsOneWidget);
});
```

Integration tests exercise the real app on a device or emulator:

```dart
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();

  testWidgets('signs in', (WidgetTester tester) async {
    await tester.pumpWidget(const MyApp());
    await tester.tap(find.text('Sign in'));
    await tester.pumpAndSettle();
    expect(find.text('Welcome'), findsOneWidget);
  });
}
```

Commands:

```bash
flutter test
flutter test test/widget/counter_test.dart --plain-name "increments"
flutter test --coverage
flutter test integration_test/app_test.dart -d <device-id>
flutter analyze
dart format --set-exit-if-changed .
dart fix --apply
```

## Checklist

- [ ] Every `State` disposes controllers, subscriptions, and timers in `dispose`.
- [ ] Async callbacks check `mounted` after `await`; `flutter analyze` is clean.
- [ ] No unbounded constraints feeding a flex child.
- [ ] Reorderable and deletable lists use stable `ValueKey`s.
- [ ] `const` used wherever the subtree allows it.
- [ ] `build` performs no I/O, parsing, or sorting.
- [ ] State management is the lightest option that works; watchers are scoped to leaves.
- [ ] Channel names match across Dart, Kotlin, and Swift; plugin handlers are registered.
- [ ] Images are decoded at display size.
- [ ] Tests use `pump` and `pumpAndSettle` deliberately, never real delays.

## Common mistakes

- **`RenderFlex overflowed by N pixels`.** A child wanted more space than the parent
  allowed. Wrap the offending child in `Expanded` or `Flexible`, allow text to wrap, or
  replace the `Column` with a scrollable.
- **`incoming height constraints are unbounded`.** A `Column` inside a `ListView` or
  `SingleChildScrollView` containing an `Expanded`. Wrap the scrollable in `Flexible` or
  use `shrinkWrap` with a bounded parent.
- **`setState() called after dispose()`.** An `await` resolved after the widget was
  removed. Check `mounted` or cancel the work.
- **`Looking up a deactivated widget's ancestor is unsafe`.** A `BuildContext` used from a
  callback that outlives the element. Look the dependency up synchronously before `await`.
- **`Incorrect use of ParentDataWidget`.** `Expanded`, `Flexible`, or `Positioned` used
  outside a `Row`, `Flex`, or `Stack`. It must be the direct child of that widget.
- **`MissingPluginException` from a channel.** The platform side never registered a
  handler. Rebuild the app, or register the handler in the plugin rather than the app.
- **Null check operator used on a null value after a channel call.** Platform code
  returned null instead of failing. Handle the null.
- **Test hangs.** An animation that never settles under `pumpAndSettle`, or a
  `Stream.periodic` in the tree.

## Anti-patterns

- `setState` on a `StatefulWidget` that wraps the whole app to hold a theme toggle.
- Calling `notifyListeners` in a loop inside one action, which notifies N times.
- Global mutable state in a top-level variable, untestable and leaking between tests.
- Performing network calls inside `build`.
- Using `ListView(children: [...])` with thousands of children.
- Rebuilding `Scaffold` per list item to get a divider; use `ListView.separated`.
- Watching a broad notifier in a page widget because one leaf needs one field.
- Reaching for a package before trying `const`, `LayoutBuilder`, and `ListView.builder`.

## References

- Flutter documentation home: https://docs.flutter.dev/
- Widgets overview: https://docs.flutter.dev/ui/widgets
- Learning layout: https://docs.flutter.dev/ui/layout
- State management overview: https://docs.flutter.dev/cookbook/managing-state
- Architecture overview: https://docs.flutter.dev/app/architecture
- Navigation: https://docs.flutter.dev/ui/navigation
- go_router: https://pub.dev/packages/go_router
- Platform channels: https://docs.flutter.dev/platform-integration/platform-channels
- Performance: https://docs.flutter.dev/perf/best-practices
- Impeller and rendering: https://docs.flutter.dev/perf/impeller
- Testing overview: https://docs.flutter.dev/testing/overview
- Dart language tour: https://dart.dev/language

## References detail

- `references/platform-channels.md` - plugin packaging, codecs, background isolate channels.
- `references/testing.md` - widget test patterns, golden tests, mocking, integration tests.
- `references/performance.md` - rebuild profiling, jank diagnosis, startup work reduction.