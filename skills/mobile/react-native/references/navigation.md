# React Navigation reference

## Package layout

| Package | Purpose |
| --- | --- |
| `@react-navigation/native` | `NavigationContainer`, linking, theme |
| `@react-navigation/native-stack` | Platform-native stack. Needs `react-native-screens`, `react-native-safe-area-context` |
| `@react-navigation/bottom-tabs` | Bottom tab bar. Native variant is `@react-navigation/bottom-tabs` with native code |
| `@react-navigation/drawer` | Drawer. Needs `react-native-reanimated` and `react-native-gesture-handler` |
| `@react-navigation/stack` | JS stack with rich transitions |

All navigators require a wrapping `NavigationContainer`. One container per app is normal;
nested containers are almost always a bug.

## Typed navigator setup

```tsx
// src/navigation/types.ts
export type RootStackParamList = {
  Tabs: undefined;
  Details: { itemId: string };
  Settings: { initialTab?: 'general' | 'privacy' } | undefined;
};

export type TabParamList = {
  Feed: undefined;
  Search: { query: string };
  Profile: { userId: string };
};
```

```tsx
// src/navigation/hooks.ts
import {
  useNavigation,
  useRoute,
  RouteProp,
} from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { RootStackParamList } from './types';

export type RootNavigation = NativeStackNavigationProp<RootStackParamList>;

export function useRootNavigation(): RootNavigation {
  return useNavigation<RootNavigation>();
}

export function useItemId(): string {
  const route = useRoute<RouteProp<RootStackParamList, 'Details'>>();
  return route.params.itemId;
}
```

`useNavigation()` needs the param list as a type argument; TypeScript cannot infer it
through `NavigationContainer`.

## Tabs

```tsx
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
import { TabParamList } from './types';

const Tab = createBottomTabNavigator<TabParamList>();

export function TabNavigator() {
  return (
    <Tab.Navigator screenOptions={{ headerShown: false }}>
      <Tab.Screen name="Feed" component={FeedScreen} />
      <Tab.Screen name="Search" component={SearchScreen} />
      <Tab.Screen name="Profile" component={ProfileScreen} />
    </Tab.Navigator>
  );
}
```

Hide a tab from users without removing it from the navigator:

```tsx
<Tab.Screen
  name="Profile"
  component={ProfileScreen}
  options={{
    tabBarButton: (props) => <TabBarButton {...props} accessibilityLabel="Profile" />,
    tabBarAccessibilityLabel: 'Profile',
  }}
/>
```

Or use `getFocusedRouteNameFromRoute` so that entering a nested stack highlights the right
tab:

```tsx
<Tab.Navigator
  screenOptions={({ route }) => ({
    tabBarIcon: ({ color, size }) => iconFor(route.name, color, size),
    title: route.name,
  })}
>
```

## Nested navigators

Each navigator gets its own `NavigationContainer`-free subtree. Nest by rendering the
child navigator as the component of a parent screen, not as a sibling.

```tsx
<Stack.Navigator>
  <Stack.Screen name="RootTabs" component={TabNavigator} />
  <Stack.Screen name="Details" component={DetailsScreen} />
</Stack.Navigator>
```

Navigation then targets a path:

```tsx
navigation.navigate('RootTabs', {
  screen: 'Feed',
  params: { screen: 'Profile', params: { userId: 'u_123' } },
});
```

If `navigate` cannot find a route in the current navigator it walks up. If it cannot find
it anywhere you get `The action 'NAVIGATE' with payload ... was not handled by any
navigator.` Read that message literally.

## Effects tied to focus

```tsx
import { useCallback } from 'react';
import { useFocusEffect } from '@react-navigation/native';

export function FeedScreen() {
  useFocusEffect(
    useCallback(() => {
      // runs on focus, cleanup on blur
      return () => {
        /* stop timers, cancel requests */
      };
    }, []),
  );
  return null;
}
```

Do not use `useEffect` with navigation focus state. `useFocusEffect` already wraps in
`useCallback`; double-wrapping causes the effect to re-subscribe every render.

## Resetting and auth gating

```tsx
const RootStack = createNativeStackNavigator<RootStackParamList>();

type GateProps = { isAuthed: boolean; onSignedOut: () => void };

export function RootNavigator({ isAuthed, onSignedOut }: GateProps) {
  return (
    <Stack.Navigator>
      {isAuthed ? (
        <>
          <Stack.Screen name="Tabs" component={TabNavigator} />
          <Stack.Screen name="Details" component={DetailsScreen} />
        </>
      ) : (
        <Stack.Screen name="Settings" component={SignInScreen} />
      )}
    </Stack.Navigator>
  );
}

export function resetToHome(navigation: RootNavigation) {
  navigation.reset({
    index: 0,
    routes: [{ name: 'Tabs' }],
  });
}
```

Changing the set of registered screens changes the navigator identity. If a modal or
screen disappears mid-flow, verify this is not the cause.

## Deep links

```tsx
import { LinkingOptions } from '@react-navigation/native';

const linking: LinkingOptions<RootStackParamList> = {
  prefixes: ['myapp://', 'https://myapp.example.com', 'https://example.com'],
  config: {
    screens: {
      Tabs: {
        screens: {
          Feed: 'feed',
          Search: 'search/:query',
          Profile: 'profile/:userId',
        },
      },
      Details: 'items/:itemId',
    },
  },
};

export function App() {
  return (
    <NavigationContainer linking={linking} onReady={() => reportReady()}>
      <RootStackNavigator />
    </NavigationContainer>
  );
}
```

Every dynamic segment needs a parameter in the nested path. Path parameters arriving in a
nested navigator must map to the screen that declares them.

Unhandled inbound links go to a `notFound` screen if declared:

```tsx
<Stack.Navigator>
  ...
</Stack.Navigator>
```

Add a `notFound` screen inside `config.screens` at the level you want, or handle unknown
URLs in your server's link routing instead.

## Navigation container ref

`createNavigationContainerRef` gives imperative access outside React, useful for one-tap
sign-out from a non-React module:

```tsx
import { createNavigationContainerRef } from '@react-navigation/native';

export const navigationRef = createNavigationContainerRef<RootStackParamList>();

if (navigationRef.isReady()) {
  navigationRef.navigate('Settings');
}
```

Guard with `isReady()`. Calling `navigate` before the container mounts is a no-op that
fails silently, which is the usual cause of "the deep link did nothing".

## Testing navigation

```tsx
import { render, screen } from '@testing-library/react-native';
import userEvent from '@testing-library/react-native/user-event';

render(<RootNavigator />);

expect(screen.getByText('Nothing here yet.')).toBeOnTheScreen();
await userEvent.press(screen.getByRole('button', { name: 'Row 3' }));
expect(screen.getByText('Row 3')).toBeOnTheScreen();
```

Wrap the component under test in a real `NavigationContainer` when the test needs
navigation; otherwise the component throws for missing navigation context.

## Sources

- React Navigation docs: https://reactnavigation.org/docs/getting-started
- Navigation container: https://reactnavigation.org/docs/navigation-container
- Linking: https://reactnavigation.org/docs/linking
- Nested navigators: https://reactnavigation.org/docs/nested-navigators