---
name: CSS Architecture
description: Covers cascade layers, custom properties, container queries, specificity control with where and is, BEM, CSS Modules, utility-first tradeoffs, logical properties, and responsive design. Use when the user asks about styling architecture, CSS specificity battles, theming with custom properties, making components responsive, choosing between CSS approaches, or organizing a large stylesheet.
metadata:
  opencode/autoinvoke: true
---

# CSS Architecture

CSS is a cascade, and architecture is the practice of making the cascade predictable.
The three things that make a cascade predictable are **order** (layers), **specificity**,
and **where the value comes from** (custom properties and inheritance).

Modern CSS gives you real tools for all three: `@layer` controls order, `:where()` makes
specificity controllable, and registered custom properties (`@property`) make design
values a typed, inheritable API. Use them before reaching for a preprocessor or a
framework.

A working default order for a product stylesheet:

```css
/* src/styles/index.css - declare the order once, up front */
@layer reset, tokens, base, layout, components, utilities;
```

## When to use this

- Choosing between CSS Modules, utility classes, and a component-scoped approach.
- Fighting `!important` or fighting specificity across files.
- Making a component work in both a page and a sidebar where its container width
  differs from the viewport.
- Theming: light mode, dark mode, a white-label brand, or per-tenant colors.
- Writing styles that survive RTL, vertical writing modes, and user font sizes.
- Deciding layer order and file organization for a large app.

## Quick start

A component whose width responds to its container, not the viewport, using layers,
custom properties, and zero specificity games:

```css
/* src/styles/tokens.css */
@layer tokens {
  :root {
    color-scheme: light dark;

    /* Primitive palette */
    --blue-600: oklch(0.55 0.19 255);
    --blue-700: oklch(0.48 0.19 255);
    --white: oklch(1 0 0);

    /* Semantic tokens */
    --color-action: var(--blue-600);
    --color-action-hover: var(--blue-700);
    --color-on-action: var(--white);
    --color-surface: var(--white);
    --color-text: oklch(0.22 0.01 260);

    --space-2: 0.5rem;
    --space-3: 0.75rem;
    --space-4: 1rem;

    --radius-md: 0.375rem;
  }

  @media (prefers-color-scheme: dark) {
    :root {
      --color-action: var(--blue-500, oklch(0.7 0.16 255));
      --color-on-action: oklch(0.16 0.02 255);
      --color-surface: oklch(0.22 0.01 260);
      --color-text: oklch(0.95 0.01 260);
    }
  }

  /* Optional brand override applied by a wrapper class */
  .theme-brand-b {
    --color-action: oklch(0.55 0.19 150);
    --color-action-hover: oklch(0.48 0.19 150);
  }
}
```

```css
/* src/components/card.css */
@layer components {
  .card {
    /* Become a container so the internals respond to the box, not the screen */
    container-type: inline-size;
    container-name: card;

    background: var(--color-surface);
    color: var(--color-text);
    border: 1px solid var(--color-text);
    border-radius: var(--radius-md);
    padding: var(--space-4);
  }

  .card :where(a) {
    color: var(--color-action);
  }

  .card :where(a:hover) {
    color: var(--color-action-hover);
  }

  @container card (min-width: 30rem) {
    .card {
      display: grid;
      grid-template-columns: 12rem 1fr;
      gap: var(--space-4);
    }
  }
}
```

```css
/* src/styles/index.css */
@import './reset.css' layer(reset);
@import './tokens.css' layer(tokens);
@import './base.css' layer(base);
@import './components/card.css' layer(components);
@layer utilities {
  .visually-hidden {
    position: absolute;
    width: 1px;
    height: 1px;
    margin: -1px;
    padding: 0;
    overflow: hidden;
    clip-path: inset(50%);
    white-space: nowrap;
    border: 0;
  }
}
```

Note that `.card` is written plainly with class specificity `0-1-0`, but it wins against
anything in `base` because `components` is a later layer. You no longer need
`.theme-dark .theme-dark .card`, and you never need `!important` to win an override.

## Cascade layers

Layers beat specificity. Among declarations that are important for matching, the one in
the **last matching layer** wins, regardless of how specific it is.

```css
@layer base, components, utilities;

/* utilities is last, so it always beats the others */
@layer utilities {
  .p-4 { padding: 1rem; }
}

@layer components {
  /* higher specificity, but an earlier layer, so .p-4 still wins */
  .card.card .card__body { padding: 2rem; }
}
```

Rules that catch people:

- **Declare the layer order in one place, first.** `@import ... layer(name)` appends to
  the layer list, and the order of first appearance determines precedence.
- **Unlayered styles beat all layered styles.** A bare `.card { }` at the top level of
  a file overrides everything in every layer. This is a very common surprise when a
  third-party stylesheet is imported unlayered. Import third-party CSS into a layer.
- **`@layer` blocks do not create scope.** Two layers named the same across different
  files merge into one layer.
- **`!important` reverses layer order.** Within important declarations, the *earlier*
  layer wins. This inversion surprises people who reach for `!important` expecting the
  same behavior.

Import order:

```css
/* Vendor CSS must be layered too, otherwise it beats everything */
@import 'normalize.css' layer(reset);
@import 'reboot.css' layer(reset);
```

If your build tool resolves `@import` itself (Vite, PostCSS, webpack with css-loader),
prefer importing from JavaScript so the bundler can track dependencies, or run the
`postcss-import` plugin. Check your tool's documentation.

## Specificity control with `:where()` and `:is()`

| Selector | Specificity contribution |
| --- | --- |
| `:where(.a, .b, .c)` | **0, 0, 0** regardless of arguments |
| `:is(.a, .b)` | Highest specificity among its arguments |
| `:not(.a)` | Highest specificity among its arguments |
| `:has(.a)` | Highest specificity among its arguments |
| `.a.b` | 0, 2, 0 |
| `#a` | 1, 0, 0 |

Pattern: put specificity in `:where()` where you want it to be zero, and in the rest of
the compound selector where you want it to count.

```css
/* Every descendant link, but the state classes still outrank it */
.card :where(a, button) { color: var(--color-action); }
.card :where(a.is-active) { color: var(--color-on-action); }

/* Grouping variants without adding specificity */
:is(h1, h2, h3) { font-size: 1.5rem; }
:where(ul, ol) { padding-inline-start: 0; list-style: none; }

/* A modifier that does not need to beat anything */
.card:where(.card--featured) { outline: 2px solid var(--color-action); }
```

This is how modern CSS replaces BEM's specificity ladder and `!important` overrides.
BEM's single-class-per-block rule exists to keep specificity flat; `:where()` gives you
the same result without the naming ceremony.

## Custom properties and `@property`

Custom properties are the standard theming mechanism: they inherit, they cascade, and
they can be redefined by any ancestor.

```css
.card {
  /* Fallback chain: a component works even outside the token layer */
  padding: var(--space-4, 1rem);
}

/* A consumer can restyle the component without touching its internals */
.card--compact {
  --space-4: 0.5rem;
}
```

Register a custom property when you want a type, an initial value, or animation
interpolation:

```css
@property --radius-md {
  syntax: '<length>';
  inherits: true;
  initial-value: 0.375rem;
}

@property --opacity {
  syntax: '<number>';
  inherits: false;
  initial-value: 1;
}

.card {
  transition: --radius-md 200ms ease;
}

.card:hover {
  --radius-md: 0.75rem;
}
```

Without registration, a custom property is an untyped token: it changes discretely and
cannot be interpolated, so transitions on it do nothing. Registration is what makes
them animatable and type-checked. Verify `@property` support for your browser matrix at
caniuse.com before relying on transitions of custom properties.

Do not set a custom property on an element you also read it from in the same rule with a
fallback that depends on itself; that produces the "invalid at computed-value time"
class of bugs. Declare it on an ancestor or on `:root`.

## Container queries

A media query asks about the viewport. A container query asks about the box the
component is in. That distinction is what makes a component reusable in a sidebar, a
modal, and a full-width page.

```css
.layout-grid {
  display: grid;
  gap: 1rem;
  grid-template-columns: repeat(auto-fit, minmax(16rem, 1fr));
}

/* Each card becomes its own query container */
.card {
  container-type: inline-size;
  container-name: card;
}

/* Implicit container: nearest ancestor with container-type */
@container (min-width: 24rem) {
  .card { flex-direction: row; }
}

/* Named container */
@container card (min-width: 40rem) {
  .card__media { aspect-ratio: 16 / 9; }
}
```

| Concept | Property or unit |
| --- | --- |
| Enable containment | `container-type: inline-size \| size \| normal` |
| Name a container | `container-name` or the `container` shorthand |
| Query a specific container | `@container name (min-width: 40rem)` |
| Units inside a container | `cqw`, `cqh`, `cqi` (inline), `cqb` (block), `cqmin`, `cqmax` |
| Style queries (limited support) | `@container style(--variant: wide)` |

Keep `container-type: inline-size` rather than `size` unless you truly want both axes
constrained; `size` requires the element's height to be resolvable, which breaks
auto-height content.

## Choosing a styling approach

| Approach | Wins when | Costs |
| --- | --- | --- |
| CSS Modules | Component-scoped class names, colocated with the component, no extra dependency | Class names are generated; global theming still needs custom properties |
| Utility-first | Fast iteration, design constraints enforced in code, spacing and variant scales are consistent | Markup gets noisy; long class lists; hard to read complex selectors |
| BEM with a plain stylesheet | Long-lived sites, small teams, no build step for styles | Naming discipline; low reuse of primitives |
| Vanilla custom elements | Framework-agnostic widgets reused across stacks | More plumbing for props and events |
| Utility-first plus component abstractions | Teams that want constraints plus readable markup | Needs the design system to be solid first |

CSS Modules setup, in a Vite project:

```css
/* Card.module.css */
.card {
  container-type: inline-size;
  padding: var(--space-4);
}

.cardTitle {
  font-size: 1.125rem;
}

.visuallyHidden {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip-path: inset(50%);
  white-space: nowrap;
}
```

```tsx
import styles from './Card.module.css';

export function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <article className={styles.card}>
      <h2 className={styles.cardTitle}>{title}</h2>
      {children}
    </article>
  );
}
```

CSS Modules requires no configuration in Vite or in Next.js. Other bundlers need a
loader or a plugin; check your build tool's documentation. If you must pass a
composition hook, use `composes` rather than `extends`, because `composes` also
includes the other class:

```css
.button {
  composes: control from './Button.module.css';
  padding-inline: var(--space-3);
}
```

With `composes`, `styles.button` contains both class names in the built output. With
`:global(...)` you are opting out of scoping, which is what you want for reset and
utility rules, and not much else.

## Responsive design without device widths

| Need | Tool |
| --- | --- |
| The page layout changes | Media query with a range: `@media (width < 48rem)` |
| A component changes based on its box | Container query |
| Fluid type or spacing | `clamp(min, preferred, max)` with `rem` and `vw` |
| Text that should not leave a single orphan word | `text-wrap: balance` on headings |
| Body text that should not stretch | `text-wrap: pretty` on paragraphs |
| A fluid grid | `repeat(auto-fit, minmax(min(20rem, 100%), 1fr))` |

```css
.heading {
  /* Scales with viewport, clamped at both ends, respects user zoom */
  font-size: clamp(1.5rem, 1.1rem + 2vw, 2.5rem);
  text-wrap: balance;
}

.cardGrid {
  display: grid;
  gap: clamp(0.75rem, 2vw, 1.5rem);
  grid-template-columns: repeat(auto-fit, minmax(min(18rem, 100%), 1fr));
}
```

Never use `vw` alone for font sizes: users who zoom text expect the text to grow.
`clamp()` with a `rem` minimum keeps the floor readable.

## Logical properties and internationalization

Physical properties (`margin-left`, `padding-top`, `left`) break under right-to-left
languages and vertical writing modes. Logical properties express intent instead.

| Physical | Logical |
| --- | --- |
| `margin-left` / `margin-right` | `margin-inline-start` / `margin-inline-end` |
| `margin-top` / `margin-bottom` | `margin-block-start` / `margin-block-end` |
| `padding: 1rem` | `padding-inline` / `padding-block` |
| `left` / `right` | `inset-inline-start` / `inset-inline-end` |
| `border-top-left-radius` | `border-start-start-radius` |
| `width` / `height` in text flow | `inline-size` / `block-size` |
| `text-align: left` | `text-align: start` |

```css
.thumbnail {
  margin-inline-start: var(--space-3);
  padding-block: var(--space-2);
  border-start-start-radius: var(--radius-md);
  inline-size: 6rem;
  text-align: start;
}
```

Set `dir="rtl"` on the root element to flip the whole layout, and nothing else needs to
change.

## User preferences

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}

@media (prefers-color-scheme: dark) {
  :root {
    color-scheme: dark;
  }
}

@media (prefers-contrast: more) {
  :root {
    --color-border: currentColor;
  }
}

@media (forced-colors: active) {
  .card {
    border: 1px solid CanvasText;
  }
}
```

The reduced-motion block is one of the few places `!important` is the right answer,
and it should be in the last layer or unlayered so it actually wins. Always pair
reduced-motion handling with `prefers-reduced-transparency` and `forced-colors` when the
component depends on motion, shadow, or translucency.

## Checklist

- Layer order is declared once, at the top, before any layer block.
- Every third-party stylesheet is imported into a layer.
- No `!important` outside the reduced-motion and forced-colors blocks.
- Component styles use `:where()` where specificity is not needed.
- Tokens live in one layer and components only reference semantic tokens.
- Components that can appear in more than one layout use container queries.
- All physical spacing and positioning properties are logical.
- Font sizes use `clamp()` with a `rem` floor, never bare `vw`.
- `prefers-reduced-motion`, `prefers-color-scheme`, and `forced-colors` are handled.
- Theming is done by overriding custom properties, never by swapping stylesheets.

## Common mistakes

- **Unlayered third-party CSS.** Symptom: your `.button` rules never apply, even with
  higher specificity. Root cause: unlayered styles beat every layer. Fix: import the
  vendor sheet with `layer(reset)`.
- **Declaring `@layer` order after the rules.** Symptom: layer precedence follows first
  appearance, so the order is inverted from what you expected. Fix: declare the order
  in the first stylesheet the app loads.
- **Adding `.theme-x .card .card__title` to win a specificity fight.** Symptom: every
  new theme adds another class. Fix: put utilities in the last layer, or use `:where()`
  so the base rule has low specificity from the start.
- **`!important` in a component.** Symptom: no override is possible anywhere, including
  for the consumer who really needs one. Fix: fix the specificity or layer problem.
- **Media queries for a component that also renders in a sidebar.** Symptom: the
  component lays out for the viewport, not for its box. Fix: container queries.
- **`container-type: size` on an auto-height element.** Symptom: the element collapses
  or its children are clipped. Fix: use `inline-size`.
- **`vw`-only font sizes.** Symptom: zooming text to 200% does nothing; the layout
  breaks. Fix: `clamp()` with a `rem` minimum.
- **Hardcoded hex values in components.** Symptom: dark mode requires forking every
  component. Fix: reference semantic tokens.
- **A `@media` query that overrides user preferences with `!important` for color.**
  Symptom: `prefers-contrast` and forced-colors users lose their settings. Fix: express
  the change as a token override.

## Anti-patterns

- **A preprocessor as the first resort.** Sass and Less were workarounds for nesting and
  variables, both of which CSS now provides. Adding one hides the cascade problems
  rather than fixing them.
- **`@layer` used as if it were scope.** Layers control precedence, not selector
  matching. Two rules in the same layer still compete on specificity.
- **Deep nesting purely for aesthetics.** Deeply nested selectors are hard to read and
  easy to break. Prefer flat selectors with a clear naming convention, or nesting only
  one or two levels where it genuinely mirrors the DOM.
- **Utility classes plus a component class on the same element with no layering.**
  Whichever wins becomes a coin flip. Define the layer order once.
- **A single breakpoint-per-device table.** Device widths are not breakpoints. Break
  where the layout actually breaks.
- **Resetting everything with a heavy reset and then undoing it.** A minimal modern
  reset plus layers is less code and fewer surprises than a large reset.
- **Generating class names with a hash per file and treating that as encapsulation.**
  Generated names protect nothing at runtime; they only avoid collisions. Encapsulation
  comes from cascade control.
- **Shipping a theme as a separate stylesheet per tenant.** Duplicates every byte and
  multiplies flash-of-wrong-style bugs. Override custom properties instead.

## References

- The cascade: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_cascade/Cascade
- `@layer`: https://developer.mozilla.org/en-US/docs/Web/CSS/@layer
- Specificity: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_cascade/Specificity
- `:where()`: https://developer.mozilla.org/en-US/docs/Web/CSS/:where
- `:is()`: https://developer.mozilla.org/en-US/docs/Web/CSS/:is
- Custom properties: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_cascading_variables/Using_CSS_custom_properties
- `@property`: https://developer.mozilla.org/en-US/docs/Web/CSS/@property
- Container queries: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_containment/Container_queries
- CSS logical properties: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_logical_properties_and_values
- CSS nesting: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_nesting
- `clamp()`: https://developer.mozilla.org/en-US/docs/Web/CSS/clamp
- `prefers-reduced-motion`: https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion
- `prefers-color-scheme`: https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-color-scheme
- Forced colors: https://developer.mozilla.org/en-US/docs/Web/CSS/@media/forced-colors
- `:focus-visible`: https://developer.mozilla.org/en-US/docs/Web/CSS/:focus-visible
- CSS Modules: https://github.com/css-modules/css-modules
- MDN browser compatibility data: https://developer.mozilla.org/en-US/docs/MDN/Writing_guidelines/Page_structures/Compatibility_tables

**Verify before relying on this:** newer CSS features (`light-dark()`, `@scope`, style
container queries, nesting with relative selectors) have uneven support. Check
caniuse.com or MDN's browser compatibility data for your exact browser matrix before
shipping them.
