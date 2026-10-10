# Flutter performance reference

## Diagnose before changing code

1. Run in profile mode. Debug mode disables optimizations and distorts every number.
2. Open DevTools (run `flutter run`, then inspect in the browser) and use the
   Performance view with "Track widget rebuilds" enabled.
3. Record a frame, find the expensive span, and read the flame chart.
4. Change one thing. Measure again.

Frame budget: 16.67 ms at 60 Hz. Frames that miss the budget are jank. The build modes
table shows what each mode optimizes.

## Build modes

```bash
flutter run                          # debug: JIT, assertions, no tree shaking
flutter run --profile                # profile: AOT, no debugger, timing overlays
flutter run --release                # release: AOT, tree shaking, no dev tools
flutter build apk --release
flutter build appbundle --release    # Play Store format
flutter build ipa --release          # iOS archive
flutter build ios --release --no-codesign
flutter build macos --release
```

Release mode enables AOT compilation and tree shaking of unused icons and code paths.
Never measure performance in debug mode.

## Common jank causes

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Scrolling stutters with images | Decoding full-resolution bitmaps | Set `cacheWidth` / `cacheHeight` to the display size in logical pixels times device pixel ratio |
| Whole screen rebuilds on a keystroke | State or notifier watched too high | Extract leaf widgets; watch the narrowest provider |
| Layout jank on tap | Expensive work in `build` or a synchronous `setState` during gesture | Move work into `initState`, cache it, or defer with `addPostFrameCallback` |
| Dropped frames during navigation | Heavy page built eagerly | Use lazy route builders |
| Startup stutter | Large JSON, database, or heavy provider initialization in `main` | Move it after first frame, or into `Isolate.run` / `compute` |
| Memory growth on long lists | Unbounded caches or large images retained | Clear image caches, limit in-memory lists, dispose controllers |

## Rebuild scoping

```dart
// Rebuilds the entire page when any field of Cart changes.
class CartPage extends StatelessWidget {
  const CartPage({super.key});

  @override
  Widget build(BuildContext context) {
    final CartState cart = context.watch<cartProvider>();
    return Column(children: <Widget>[
      CartHeader(count: cart.itemCount),
      CartBody(items: cart.items),
    ]);
  }
}

// Rebuilds only the header.
class CartHeader extends StatelessWidget {
  const CartHeader({super.key, required this.count});

  final int count;

  @override
  Widget build(BuildContext context) {
    final int count = context.select<CartState, int>((CartState c) => c.itemCount);
    return Text('Items: $count');
  }
}
```

`context.select` rebuilds only when the selected value changes. `context.watch` rebuilds
on every notification. `context.read` never subscribes.

In plain `provider`, wrap a leaf in `Selector<Model, T>`:

```dart
Selector<CartModel, int>(
  builder: (BuildContext context, int count, _) => Text('Items: $count'),
  selector: (_, CartModel model) => model.itemCount,
)
```

`Consumer<Model>` rebuilds on every notification; it does not narrow the rebuild the way
`Selector` does.

## Keep build pure

`build` must not have side effects. Problems it causes:

- `setState` inside `build` produces
  `setState() or markNeedsBuild() called during build`.
- Network or database reads inside `build` run on every rebuild.
- Creating a `DateTime` or `Random` inside `build` produces different output every frame.
- `await` inside `build` is illegal; make `build` return a `FutureBuilder` or restructure.

Pattern: compute in the model.

```dart
class OrderSummaryViewModel extends ChangeNotifier {
  OrderSummaryViewModel(this._order) {
    _recompute();
  }

  final Order _order;
  late List<LineItem> _items;

  List<LineItem> get items => _items;

  void _recompute() {
    _items = List<LineItem>.unmodifiable(_order.lines);
  }
}
```

## Images

```dart
Image.network(
  url,
  cacheWidth: (screenWidth * pixelRatio).round(),
  errorBuilder: (BuildContext context, Object error, StackTrace? stack) =>
      const Icon(Icons.broken_image),
)
```

Decode at the size you will paint. A 4000 px source decoded into a 100 px box costs memory
and time that are pure waste.

For lists of images, cache decoded frames deliberately or use a package that does. Do not
re-decode in `build`.

## Lists

```dart
ListView.builder(
  itemCount: items.length,
  itemBuilder: (BuildContext context, int index) => OrderRow(item: items[index]),
  cacheExtent: 250,
  addRepaintBoundaries: true,
  addAutomaticKeepAlives: false,
)
```

- `ListView.builder` and `ListView.separated` build lazily. `ListView(children: [...])`
  builds everything eagerly.
- `addAutomaticKeepAlives: false` reduces overhead when rows do not hold state. Keep it if
  rows hold text field state.
- `cacheExtent` controls how far ahead the viewport renders. Lower means less memory, more
  blank space during fast scrolls.

## Off the UI isolate

```dart
import 'dart:isolate';

// Top-level or static function; the argument must be sendable.
List<RowModel> parseRows(String raw) {
  final List<RowModel> out = <RowModel>[];
  for (final String line in raw.split('\n')) {
    out.add(RowModel.fromLine(line));
  }
  return out;
}

Future<List<RowModel>> load(String raw) => Isolate.run(() => parseRows(raw));
```

`Isolate.run` is the modern one-shot entry point; `compute` from `package:flutter/foundation.dart`
wraps it. Verify which your Flutter version supports in the concurrency docs. Isolates
cannot share mutable objects, so return plain data and copy it back.

## Startup time

```dart
void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const MyApp());
}
```

Reduce startup by:

- Lazily initializing services. Construct providers that do work inside their `build`
  body, not at app construction.
- Deferring non-critical work:

```dart
void main() {
  WidgetsFlutterBinding.ensureInitialized();
  runApp(const MyApp());
  WidgetsBinding.instance.addPostFrameCallback((Duration _) {
    unawaited(prefetchContent());
  });
}
```

`addPostFrameCallback` runs after the first frame paints, so it does not delay first
content.

- Avoiding synchronous asset decoding and large JSON parsing before `runApp`.
- Checking the release binary size:

```bash
flutter build appbundle --release --analyze-size
flutter tree-shake-icons
```

Icon tree shaking requires the `--tree-shake-icons` flag on the build command.

## Painting and repaint

- Wrap frequently changing, non-overlapping subtrees in `RepaintBoundary` so their repaints
  do not invalidate siblings. Each boundary costs memory.
- Avoid `BackdropFilter` and large blurs in scrolling lists.
- Prefer solid fills over `Opacity` over partially transparent layers. Each translucent
  layer may require an offscreen buffer.
- Use `Opacity` over animating `Color` or `Container` when you only need fade.

## Measuring

- Enable performance overlays: `flutter run --profile` shows frame timings, or add a
  `Timeline` and read `addTimelineSummary` from the observatory.
- Use DevTools Performance view; enable "Track widget rebuilds" to see which widgets rebuild
  and why.
- Profile on a physical device. Simulators and low-end phones surface jank that never
  appears on your desktop.

## Sources

- Performance best practices: https://docs.flutter.dev/perf/best-practices
- Performance overview: https://docs.flutter.dev/perf/overview
- Flutter DevTools performance view: https://docs.flutter.dev/tools/devtools-performance
- Rendering overview: https://docs.flutter.dev/ui/performance
- Impeller rendering backend: https://docs.flutter.dev/perf/impeller
- Dart isolates: https://dart.dev/language/concurrency