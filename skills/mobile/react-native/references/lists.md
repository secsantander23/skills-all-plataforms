# Lists, virtualization, and large collections

## How virtualization works

`FlatList` is built on `VirtualizedList`. It renders `initialNumToRender` items, then as
the user scrolls it fills up to `maxToRenderPerBatch` more per batch, maintaining a window
of roughly `windowSize * maxToRenderPerBatch` items around the visible region. Row
components outside that window are unmounted, so their local state is lost.

Consequences you must design around:

- A row's internal `useState` resets when it scrolls out of the window. Persist state in
  the parent or in a store, not in the row.
- Very tall rows break the window model because a single item can exceed the viewport.
- Screens containing a list should let the list own scrolling. Put a `ScrollView` around a
  list only when the two scroll on different axes, and only if you accept the memory cost.

## Tuning props

| Prop | Default | What it does |
| --- | --- | --- |
| `initialNumToRender` | 10 | Rows rendered on first paint |
| `maxToRenderPerBatch` | 10 | Rows rendered per scroll batch |
| `updateCellsBatchingPeriod` | 50 | Milliseconds between batches |
| `windowSize` | 21 | Number of viewports kept mounted either side |
| `removeClippedSubviews` | platform-dependent | Detach offscreen native subviews |
| `getItemLayout` | none | Fixed row metrics; enables scroll-to-index |
| `extraData` | none | Change it when `renderItem` depends on non-data state |
| `numColumns` | 1 | Grid layout; `keyExtractor` must include the column |
| `onEndReachedThreshold` | 2 | Ratio of the visible list length |

Lower `windowSize` reduces memory and can increase blank rows during fast scrolls. Raising
it hides more rows but costs memory. `windowSize` between 5 and 10 is a reasonable range
for simple row lists; measure before changing.

`getItemLayout` signature and use:

```tsx
const ROW_HEIGHT = 72;

<FlatList
  data={rows}
  renderItem={renderItem}
  keyExtractor={(item) => item.id}
  getItemLayout={(_data, index) => ({
    length: ROW_HEIGHT,
    offset: ROW_HEIGHT * index,
    index,
  })}
/>
```

`initialScrollIndex` works only with `getItemLayout`, and it is imperfect: to land on index
5000 the list may still need to build a large offset. For deep links to a specific item,
scroll to a nearby index and fetch around it.

## Grid layout

```tsx
<FlatList
  data={photos}
  numColumns={3}
  keyExtractor={(item) => `${item.id}-${item.columnHint ?? 0}`}
  renderItem={({ item }) => <PhotoCell photo={item} />}
  columnWrapperStyle={{ gap: 8 }}
/>
```

`keyExtractor` keys must be unique across the whole list, not per row, or cells will be
reused across columns incorrectly.

## SectionList

```tsx
<SectionList
  sections={sections}
  keyExtractor={(item) => item.id}
  renderSectionHeader={({ section }) => (
    <Text style={styles.header}>{section.title}</Text>
  )}
  renderItem={({ item }) => <Row row={item} />}
  stickySectionHeadersEnabled
  sections={sections.map((s) => ({ ...s, data: s.data.slice(0, 200) }))}
  initialNumToRender={12}
/>
```

`SectionList` does not support `getItemLayout`. If a sectioned list must support
scroll-to-index or has very large sections, model it as a `FlatList` of `{ type, ... }`
union items instead.

## Sticky headers with FlatList

```tsx
<FlatList
  data={data}
  ListHeaderComponent={<Header />}
  renderItem={renderItem}
  stickyHeaderIndices={[0]}
  onScrollToIndexFailed={(info) => {
    listRef.current?.scrollToOffset({
      offset: info.averageItemLength * info.index,
      animated: true,
    });
  }}
/>
```

`onScrollToIndexFailed` is not optional when `onScrollToIndex` can be triggered out of
range. Without it you get `scrollToIndex should be used in conjunction with
getItemLayout or onScrollToIndexFailed, otherwise there is no way to know the location of
offscreen indices or handle failures.`

## Pagination

```tsx
export function FeedList() {
  const [items, setItems] = useState<Row[]>([]);
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [loading, setLoading] = useState(false);
  const inFlight = useRef(false);

  const loadMore = useCallback(async () => {
    if (inFlight.current || !cursor) return;
    inFlight.current = true;
    setLoading(true);
    try {
      const page = await fetchPage(cursor);
      setItems((prev) => [...prev, ...page.items]);
      setCursor(page.nextCursor);
    } finally {
      inFlight.current = false;
      setLoading(false);
    }
  }, [cursor]);

  return (
    <FlatList
      data={items}
      renderItem={renderItem}
      keyExtractor={(item) => item.id}
      onEndReached={loadMore}
      onEndReachedThreshold={0.4}
      ListFooterComponent={loading ? <Spinner /> : null}
    />
  );
}
```

Use a ref as well as state for the in-flight guard. State updates are async, so two rapid
`onEndReached` calls in the same tick both read `loading === false`.

Prefer cursor pagination over offset pagination. Offset pagination skips or repeats rows
when the underlying data changes between requests, and offset queries get slower on large
tables.

## Stable render items

```tsx
// Bad: new function identity every render, new style object every render.
<FlatList
  data={rows}
  renderItem={({ item }) => (
    <Pressable onPress={() => onSelect(item)} style={[styles.row, { opacity: 1 }]}>
      <Text>{item.title}</Text>
    </Pressable>
  )}
/>
```

```tsx
// Good: memoized row, memoized callback, no inline objects.
const Row = React.memo(function Row({ item, onSelect }: RowProps) {
  const handlePress = useCallback(() => onSelect(item), [onSelect, item]);
  return (
    <Pressable onPress={handlePress} style={styles.row}>
      <Text style={styles.title}>{item.title}</Text>
    </Pressable>
  );
});

<FlatList
  data={rows}
  renderItem={renderItem}          // useCallback with [onSelect] deps
  keyExtractor={keyExtractor}      // module-level function
/>
```

`React.memo` is per-component-type. Inlining a JSX element in `renderItem` creates a new
element type each render only if you define the component inline; a top-level component
keeps its identity.

## Flip versus inverted lists

For chat-style feeds where new items appear at the bottom:

```tsx
<FlatList
  inverted
  data={messages}
  keyExtractor={(m) => m.id}
  renderItem={renderItem}
  onEndReached={loadOlder}
  onEndReachedThreshold={0.1}
  maintainVisibleContentPosition={{ minIndexForVisible: 0 }}
/>
```

`inverted` flips the scale and removes the need to reverse the data array yourself. It
changes the meaning of `onEndReached`, which now fires toward the top.

## Images in lists

```tsx
<Image
  source={{ uri: photo.url }}
  style={{ width: '100%', aspectRatio: 1 }}
  resizeMode="cover"
/>
```

- Set an explicit width and aspect ratio or height so the list can compute layout without
  downloading each image.
- Remote images are fetched over the network per instance. Use a caching server or a
  downsized thumbnail endpoint; do not feed 4000px originals into a list.
- Avoid `ImageBackground` in long lists; it adds an extra native view per row.

## Empty, error, and loading states

```tsx
<FlatList
  data={rows}
  renderItem={renderItem}
  keyExtractor={keyExtractor}
  ListEmptyComponent={
    loading ? <Spinner /> : <EmptyState onRetry={() => setReloadKey((k) => k + 1)} />
  }
  ListFooterComponent={rows.length > 0 && hasMore ? <Spinner /> : null}
  contentContainerStyle={rows.length === 0 ? styles.emptyContent : undefined}
/>
```

When the empty state is tall, set `contentContainerStyle={{ flexGrow: 1 }}` so the empty
component can center itself.

## FlashList

`@shopify/flash-list` recycles cells rather than unmounting them, so it can drop
`estimatedItemSize` style configuration on v2. The API changed materially between v1 and
v2: v1 required `estimatedItemSize`, v2 removed it. Read the README for the version you
install before copying any example.

Use it when:

- Lists are long and row content is uniform.
- You have already measured with `React Native DevTools` that `FlatList` is the bottleneck.

Do not adopt it because it is faster in a benchmark. Recycling trades memory and
complexity, and rows with heterogeneous heights or per-row state can behave subtly.

## Failure modes

- **Blank rows on iOS**: `removeClippedSubviews` combined with `position: 'absolute'`,
  transforms, or nested scroll containers. Turn it off on iOS.
- **`key` warnings**: `keyExtractor` returns undefined or duplicates. If your data has no
  stable id, add one server-side rather than using the array index. Index keys break
  insertion, deletion, and reordering.
- **Memory growth**: very large `windowSize`, huge images, or rows holding large cached
  objects that are never released when unmounted.
- **Jump to top when data updates**: new array identity with a `FlashList`. Preserve
  scroll offset with `maintainVisibleContentPosition` or restore it manually after the
  update.

## Sources

- FlatList: https://reactnative.dev/docs/flatlist
- SectionList: https://reactnative.dev/docs/sectionlist
- VirtualizedList props: https://reactnative.dev/docs/virtualizedlist
- FlashList: https://shopify.github.io/flash-list/
- Testing Library React Native: https://callstack.github.io/react-native-testing-library/