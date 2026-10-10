---
name: React Native
description: Covers React Native development including the core component set, the New Architecture with Fabric, TurboModules and JSI, React Navigation stacks and tabs, state management, StyleSheet styling, virtualized lists, native module authoring, iOS and Android builds, performance work, platform-specific files and debugging. Use when the user asks about a React Native screen, list, navigator, native module, build failure, redbox error, or a slow render, or when working in a project that contains app.json, metro.config.js, a Podfile, or android/gradle.properties.
metadata:
  opencode/autoinvoke: true
---

# React Native

React Native renders React elements to platform views. Your mental model should be: a
component tree, a renderer that turns that tree into UIKit or Android View hierarchies,
and a JavaScript runtime. Most debugging questions are really questions about one of
those three layers.

This skill covers the current stable surface. React Native has shipped two rendering
architectures in recent years; the New Architecture (JSI, Fabric, TurboModules) is
enabled by default in current releases. Check the release notes that match your
installed version before relying on a migration-specific flag.

## When to use this

- Writing or reviewing React Native screens, components, or navigation.
- Debugging a redbox, a native module that cannot be found, or a build failure.
- Choosing between `FlatList`, FlashList, and `SectionList`, or fixing blank/jittery rows.
- Wiring a screen to server state, local state, or a global store.
- Adding platform-specific behavior with `.ios.tsx` / `.android.tsx` files.
- Cutting startup time or re-render count.

## Quick start

A minimal component using the stable core APIs:

```tsx
import React, { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

export type Row = { id: string; title: string };

type Props = {
  rows: Row[];
  onRefresh: () => Promise<void>;
  onSelect: (row: Row) => void;
};

export function RowList({ rows, onRefresh, onSelect }: Props) {
  const insets = useSafeAreaInsets();
  const [refreshing, setRefreshing] = useState(false);

  const handleRefresh = useCallback(async () => {
    try {
      setRefreshing(true);
      await onRefresh();
    } finally {
      setRefreshing(false);
    }
  }, [onRefresh]);

  const renderItem = useCallback(
    ({ item }: { item: Row }) => (
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={item.title}
        onPress={() => onSelect(item)}
        style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
      >
        <Text style={styles.rowTitle}>{item.title}</Text>
      </Pressable>
    ),
    [onSelect],
  );

  return (
    <View style={[styles.container, { paddingBottom: insets.bottom }]}>
      <FlatList
        data={rows}
        renderItem={renderItem}
        keyExtractor={(item) => item.id}
        ItemSeparatorComponent={Separator}
        onRefresh={handleRefresh}
        refreshing={refreshing}
        ListEmptyComponent={<Text style={styles.empty}>Nothing here yet.</Text>}
        ListFooterComponent={
          rows.length > 0 ? <ActivityIndicator style={styles.footer} /> : null
        }
      />
    </View>
  );
}

function Separator() {
  return <View style={styles.separator} />;
}

const styles = StyleSheet.create({
  container: { flex: 1 },
  row: { paddingHorizontal: 16, paddingVertical: 12 },
  rowPressed: { opacity: 0.6 },
  rowTitle: { fontSize: 16, color: '#111111' },
  separator: { height: StyleSheet.hairlineWidth, backgroundColor: '#dddddd' },
  empty: { padding: 24, textAlign: 'center', color: '#666666' },
  footer: { paddingVertical: 24 },
});
```

## Core components

Reach for these before adding a dependency. Everything here is in the `react-native`
package itself.

| Need | Component | Note |
| --- | --- | --- |
| Layout box | `View` | Flexbox default `flexDirection: 'column'` |
| Text | `Text` | Raw strings outside `<Text>` throw at runtime |
| Scrolling viewport | `ScrollView` | Fine for small, known-size content |
| Touch target | `Pressable` | Preferred over `TouchableOpacity`; supports pressed/focused states |
| Text entry | `TextInput` | Use `onChangeText`; `value` must be controlled |
| Remote/local image | `Image` | Prefer `Image` with `source={{ uri }}`; add `resizeMode` |
| Async status | `ActivityIndicator` | |
| Modal | `Modal` | Presentation style differs per platform |
| Keyboard avoidance | `KeyboardAvoidingView` | Pair with platform-aware `behavior` |
| Refresh control | `RefreshControl` | Usually passed as a prop to `FlatList` |

Use `Pressable`'s `style` function form for press feedback. It runs on every press state
change and allocates, so keep it to one or two style lookups.

## Styling

`StyleSheet.create` registers styles so they are not re-serialized on every render. It
returns opaque IDs, which is why `StyleSheet.flatten` exists for the rare case where you
must merge an unknown style object.

```tsx
import { Platform, StyleProp, StyleSheet } from 'react-native';

const hitSlop = Platform.select({
  ios: { top: 8, bottom: 8, left: 8, right: 8 },
  default: { top: 12, bottom: 12, left: 12, right: 12 },
})!;

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#ffffff',
    borderRadius: 12,
    padding: 16,
    shadowColor: '#000000',
    shadowOpacity: Platform.OS === 'ios' ? 0.12 : 0,
    shadowRadius: 8,
    shadowOffset: { width: 0, height: 2 },
    elevation: Platform.OS === 'android' ? 2 : 0,
  },
});

export function merge<T>(...parts: Array<StyleProp<T> | undefined>): StyleProp<T> {
  return StyleSheet.compose<StyleProp<T> | undefined>(parts[0] ?? undefined, parts[1]);
}
```

Points that bite:

- There is no CSS cascade and no specificity. Later styles in an array win.
- `gap` is supported in flex layouts in current releases, but `margin`/`padding` are
  still the safest portable choice.
- Shadows are platform-split: iOS uses `shadowColor`/`shadowOffset`/`shadowOpacity`/
  `shadowRadius`; Android uses `elevation`. Use `Platform.select`.
- Never compute styles inline in a hot render path without `useMemo`. A new style object
  each render defeats memoization of child components.

## Navigation

React Navigation is the de facto choice. Two packages matter most:

- `@react-navigation/native` plus `@react-navigation/native-stack`. Native stack uses
  `react-native-screens` and `react-native-safe-area-context` and renders real
  UINavigationController / Fragment transactions. Prefer this.
- `@react-navigation/stack` is a JavaScript stack built on
  `react-native-gesture-handler`. It offers richer custom transitions at higher cost.

```tsx
import { NavigationContainer, DefaultTheme } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { SafeAreaProvider } from 'react-native-safe-area-context';

export type RootStackParamList = {
  Home: undefined;
  Details: { itemId: string; title: string };
};

const Stack = createNativeStackNavigator<RootStackParamList>();

export function RootNavigator() {
  return (
    <SafeAreaProvider>
      <NavigationContainer theme={DefaultTheme}>
        <Stack.Navigator
          screenOptions={{
            headerBackButtonDisplayMode: 'minimal',
            headerTitleAlign: 'center',
          }}
        >
          <Stack.Screen name="Home" component={HomeScreen} />
          <Stack.Screen
            name="Details"
            component={DetailsScreen}
            options={({ route }) => ({ title: route.params.title })}
          />
        </Stack.Navigator>
      </NavigationContainer>
    </SafeAreaProvider>
  );
}
```

Rules that prevent most navigation bugs:

1. `SafeAreaProvider` must be an ancestor of anything using `useSafeAreaInsets`.
2. Type the param list and use `NativeStackScreenProps<RootStackParamList, 'Details'>`
   for screen props. It catches wrong param names at compile time.
3. `navigation.navigate` never duplicates an already-focused route in the same screen.
   Use `navigation.push` when the same screen should appear twice.
4. Reset state explicitly with `navigation.reset({ index: 0, routes: [{ name: 'Home' }] })`
   after a login flow. Do not fake a reset by navigating back through history.
5. Deep links are configured once on `NavigationContainer` via the `linking` prop. See
   `references/navigation.md`.

## Lists and virtualization

Default to `FlatList`. It virtualizes by rendering only the visible window plus a small
buffer, and recycles row components.

```tsx
<FlatList
  data={rows}
  renderItem={renderItem}            // stable reference, defined with useCallback
  keyExtractor={(item) => item.id}  // stable, unique, cheap
  getItemLayout={(_, index) => ({
    length: ROW_HEIGHT,
    offset: ROW_HEIGHT * index,
    index,
  })}
  initialNumToRender={10}
  maxToRenderPerBatch={10}
  windowSize={7}
  onEndReached={loadMore}
  onEndReachedThreshold={0.5}       // a fraction of the list length, not pixels
  removeClippedSubviews={Platform.OS === 'android'}
/>
```

Decision table:

| Situation | Use | Why |
| --- | --- | --- |
| Flat homogeneous rows | `FlatList` | Least code, predictable memory |
| Grouped rows with headers | `SectionList` | Built-in sticky headers and section models |
| Mixed or variable rows, long lists | `@shopify/flash-list` | Recycles cells instead of unmounting; requires a measured cell size on v1 (see package README) |
| Fewer than ~20 items | `ScrollView.map` | Virtualization overhead is not worth it |
| Item count unknown and huge | `FlatList` with `getItemLayout` if rows are fixed height | Enables scroll-to-index without layout |

`getItemLayout` is the highest-leverage list prop. Without it, scroll-to-index and
momentum estimation degrade because the list must measure rows first. It is only valid
when every row is the same height, and it is not supported by `SectionList`.

See `references/lists.md` for row recycling, empty states, and FlashList caveats.

## State management

Split the problem in three. Server state, client state, and form state have different
tools.

| State kind | Recommended | Notes |
| --- | --- | --- |
| Local UI state | `useState` / `useReducer` | Keep it in the component that owns it |
| Cross-tree, rarely changes | React Context | Put only a stable dispatcher or store reference in context, never the value itself |
| App-wide client state | Redux Toolkit or Zustand | Slice by feature; select narrowly |
| Server state | TanStack Query | Handles cache, retries, refetch, invalidation |
| Form input | React Hook Form | Uncontrolled inputs avoid per-keystroke re-renders |

The single most common performance bug in React Native apps is a store provider whose
value is a fresh object every render, re-rendering the entire tree. Select primitives or
stable slices.

```tsx
// store.ts
import { configureStore, createSlice, PayloadAction } from '@reduxjs/toolkit';

type CartState = { items: Record<string, number>; hydrated: boolean };

const cartSlice = createSlice({
  name: 'cart',
  initialState: { items: {}, hydrated: false } as CartState,
  reducers: {
    hydrate(state, action: PayloadAction<CartState>) {
      state.hydrated = true;
      state.items = action.payload.items;
    },
    add(state, action: PayloadAction<{ id: string; qty: number }>) {
      const current = state.items[action.payload.id] ?? 0;
      state.items[action.payload.id] = current + action.payload.qty;
    },
    remove(state, action: PayloadAction<string>) {
      delete state.items[action.payload];
    },
  },
});

export const store = configureStore({ reducer: { cart: cartSlice.reducer } });
export const { add, remove, hydrate } = cartSlice.actions;
export type RootState = ReturnType<typeof store.getState>;
export type AppDispatch = typeof store.dispatch;
```

Put `dispatch` in one context object and the state in another. Components that only
dispatch will not re-render when state changes.

## Native modules and TurboModules

Writing a native module means a TypeScript spec plus platform code. The spec drives
codegen on both platforms.

```ts
// specs/NativeBattery.ts
import type { TurboModule } from 'react-native';
import { TurboModuleRegistry } from 'react-native';

export interface Spec extends TurboModule {
  getBatteryLevel(): Promise<number>;
  isCharging(): Promise<boolean>;
}

export default TurboModuleRegistry.getEnforcing<Spec>('Battery');
```

Add the codegen configuration to the package `package.json`:

```json
{
  "codegenConfig": {
    "name": "AppSpecs",
    "type": "modules",
    "jsSrcsDir": "specs"
  }
}
```

A failed lookup produces:

```
Error: TurboModuleRegistry.getEnforcing(...): 'Battery' could not be found.
Verify that a module by this name is registered in the native binary.
```

That means the JavaScript is fine but the platform implementation is missing, was not
registered in the app's package list, or the build did not run codegen. Full Android and
iOS registration code is in `references/native-modules.md`.

If you only need to consume an existing library, do not write a module. Adding a library
still requires a rebuild after `npm install` because native code is compiled into the app,
not shipped from node_modules.

## Building and running

```bash
# scaffold a new project
npx @react-native-community/cli@latest init AwesomeProject

# start the Metro bundler (leave it running)
npx react-native start

# build and install on a connected device or simulator
npx react-native run-ios
npx react-native run-android

# release builds (signing required)
npx react-native run-android --mode release
```

- `react-native.config.js` links local packages via `dependencies` so Metro resolves them.
- Android release builds need a signing config in `android/app/build.gradle`; debug builds
  use the auto-generated debug keystore.
- iOS builds use CocoaPods. After changing any dependency that ships native code, run
  `pod install` from the `ios/` directory, and use `npx react-native run-ios` rather than
  opening stale `.xcodeproj` state.
- Expo projects use prebuild and EAS Build instead of the native CLI. Do not mix the two
  workflows in one project without understanding what `prebuild` regenerates.

## Performance

Diagnose before optimizing. Measure the thing you plan to change.

Startup time:

1. Reduce the module graph that must be evaluated before first render. Lazy `require` or
   dynamic `import()` for screens that are not shown at launch.
2. Defer non-critical work past first paint with `InteractionManager.runAfterInteractions`
   or a `requestAnimationFrame` after mount.
3. Avoid JSON parsing of large payloads on the JS thread at startup. Hermes has a
   bytecode format; verify the current Hermes status and any bytecode flags in the docs
   before enabling them.
4. Release builds with Hermes are meaningfully faster than debug builds. Never profile in
   debug.

Re-renders:

- Wrap `renderItem`, event handlers, and derived objects in `useCallback` / `useMemo`.
- Pass primitives or memoized objects to memoized children (`React.memo`).
- Do not put frequently changing values in context.
- Use the React DevTools Profiler to find which component re-renders and why.

Rendering work:

- Images dominate list cost. Size images to the rendered box, not to the source file.
- Prefer `FlatList` over `ScrollView.map` past roughly 20 rows.
- Animate transform and opacity rather than `width`, `height`, or `margin`, which trigger
  layout passes.

## Platform-specific code

```tsx
import { Platform } from 'react-native';

export const cardRadius = Platform.select({ ios: 14, android: 4, default: 8 });

export function hapticKind() {
  if (Platform.OS === 'ios') return 'impactLight';
  if (Platform.OS === 'android') return 'impactMedium';
  return 'none';
}
```

File extensions resolve before the platform check: `Card.ios.tsx` and `Card.android.tsx`
win over `Card.tsx` on their platforms. `.native.tsx` is used by web bundles when the web
implementation lives in `Card.web.tsx`. Order of resolution is
`.web.tsx`/`.android.tsx`/`.ios.tsx` over `.native.tsx` over `.tsx`, so do not put shared
code in a `.native.tsx` file you expect to differ per platform.

## Debugging

- Redbox with a JS stack: read the top frame. A `TypeError` inside render usually means
  a bad prop or a stale closure.
- `console.log` works; `console.warn` shows a yellow box in dev. Strip logs before
  release with your bundler's minifier `drop_console` or an explicit logger wrapper.
- Current React Native ships React Native DevTools, which replaces the older Flipper
  integration for JS debugging and layout inspection. If you are on an older version, Flipper
  is the previous path. Confirm which is available in your version's docs.
- Android: `adb logcat`, `adb shell dumpsys activity activities` to see the resumed
  activity, and the layout inspector in Android Studio.
- iOS: Xcode debugger, the simulator's Debug menu, and `npx react-native log-ios`.
- Hermes can be inspected with Hermes-compatible debuggers; see the Hermes docs for the
  current supported frontend.

## Checklist

- [ ] Navigation typed with a param list; `SafeAreaProvider` above the navigator.
- [ ] Lists use `FlatList` or `SectionList` with a stable `keyExtractor` and stable
      `renderItem`.
- [ ] Row heights are fixed if `getItemLayout` is used.
- [ ] Store context exposes a stable dispatch, not the whole state object.
- [ ] Native modules implemented or pulled in, app rebuilt, and codegen run.
- [ ] No inline object or function props crossing a memoized boundary in a hot list.
- [ ] Debug-only code stripped and a release build profiled on a real device.
- [ ] Accessibility roles and labels on interactive elements.

## Common mistakes

- **`Text strings must be rendered within a <Text> component.`** A bare string or number
  is returned from a `View` subtree. Wrap it or delete it.
- **`VirtualizedLists should never be nested inside plain ScrollViews with the same
  orientation`.** Put the list outside the scroll view, or use a non-virtualizing list for
  genuinely small content.
- **Rows blanking or flashing on iOS.** Usually `removeClippedSubviews` interacting with
  transforms, absolute positioning, or nested scroll containers. Disable it on iOS.
- **`onEndReached` firing constantly.** `onEndReachedThreshold` is a ratio of visible list
  length, not a pixel distance. Guard with a loading flag and a last-page check.
- **`TextInput` losing focus mid-typing.** An inline `value` object plus a re-rendering
  parent. Hoist state and pass a primitive string.
- **Hermes/`undefined is not a function` after upgrading.** Native modules were linked for
  the old architecture. Clean (`node_modules`, `ios/Pods`, `android/.gradle`), rebuild.
- **Android duplicate-manifest merger error.** Two libraries declare the same provider or
  receiver. Fix with `tools:node="remove"` in an application-level manifest overlay,
  after checking the manifest merger report.
- **New Architecture error mentioning a `NativeXxx` spec that does not match the Kotlin or
  ObjC signature.** Regenerate codegen and rebuild; do not hand-edit generated files.

## Anti-patterns

- Calling `useState` for values that only a sibling needs. Lift once, not twice.
- Storing derived data in state. Compute it during render or with `useMemo`.
- Putting an entire API client in context. Context changes force full-tree re-renders.
- Using `ScrollView` with thousands of `map`ped children because it is "simpler".
- Reaching for a native module for something `AsyncStorage`, `Linking`, or
  `AppState` already provide.
- Assuming `setNativeProps` is a general escape hatch. It bypasses the declarative model
  and fights React's assumptions about ownership of the view.
- Shipping a screen with console logging and a missing `keyExtractor` because it "works on
  my device".

## References

- React Native docs home: https://reactnative.dev/docs/getting-started
- New Architecture overview: https://reactnative.dev/docs/new-architecture-introduction
- Fabric renderer: https://reactnative.dev/docs/new-architecture/fabric-component
- Native modules (legacy bridge): https://reactnative.dev/docs/native-modules-intro
- Native modules with the New Architecture: https://reactnative.dev/docs/the-new-architecture/native-modules-custom-events
- Hermes engine: https://reactnative.dev/docs/hermes
- React Navigation getting started: https://reactnavigation.org/docs/getting-started
- Native stack navigator: https://reactnavigation.org/docs/native-stack-navigator
- TypeScript in React Native: https://reactnative.dev/docs/typescript
- Metro bundler: https://reactnative.dev/docs/metro
- Testing with Jest and React Native Testing Library: https://reactnative.dev/docs/testing-overview
- React Native DevTools: https://reactnative.dev/docs/react-native-devtools

## References detail

- `references/navigation.md` - tab navigators, nested navigators, typed hooks, deep links.
- `references/lists.md` - virtualization internals, FlashList caveats, empty and error states.
- `references/native-modules.md` - full Kotlin and Objective-C++ module implementations.
- `references/build-and-debug.md` - native build configuration, Gradle, CocoaPods, CI.