# Layering and theming reference

Read `../SKILL.md` first. This file covers the mechanics of layer precedence, token
architecture, and the different theming strategies and their failure modes.

## Precedence rules in full

For a given element, the winning declaration is chosen in this order:

1. **Origin and importance.** User-agent styles, user styles, author styles. Within
   author styles, important user-agent and user declarations outrank everything.
2. **Layer.** Among normal author declarations, the one in the last declared matching
   layer wins. **Important author declarations reverse this**: the earliest matching
   layer wins.
3. **Specificity.** Compared with the specificity convention `(id, class, type)`.
4. **Source order.** The last matching declaration wins.

Practical consequences:

- Layer order beats specificity, so you can write flat, low-specificity component
  styles and still let utilities override them.
- Unlayered author styles are treated as a final implicit layer and beat all named
  layers. This is why third-party CSS imported unlayered is so hard to override.
- Important declarations reverse layer precedence. Do not use `!important` expecting it
  to behave like normal precedence.

## Multiple layers in one selector

```css
.card {
  padding: 1rem;
}

/* This selector participates in the cascade as if it matched */
.card:where(.card--compact) {
  padding: 0.5rem;
}
```

`@layer` accepts a list of names, and nesting is allowed:

```css
@layer components {
  .button {
    /* ... */
  }

  @layer components.overrides {
    .button--danger {
      /* ... */
    }
  }
}
```

Layer names that start with a digit or that are otherwise not valid identifiers need to
be quoted, for example `layer("my.layer")`. Prefer plain identifiers.

## Token architecture

Three tiers, and each tier is only useful if the layer above it uses it correctly:

| Tier | Purpose | Consumed by |
| --- | --- | --- |
| **Primitive** | Raw values: `--blue-600`, `--space-4`, `--font-size-3` | The semantic layer only |
| **Semantic** | Meaning: `--color-action`, `--space-inline-gap`, `--text-body` | Components |
| **Component** | Local overrides: `--button-padding-inline` | That component only |

```css
@layer tokens {
  :root {
    /* Primitive */
    --space-3: 0.75rem;
    --gray-100: oklch(0.97 0.005 260);
    --gray-900: oklch(0.2 0.01 260);

    /* Semantic */
    --color-surface: var(--gray-100);
    --color-text: var(--gray-900);
    --space-gap: var(--space-3);
  }
}

@layer components {
  .stack {
    gap: var(--space-gap);
  }

  .panel {
    /* Component tier: scoped to this component, overridable by consumers */
    --space-gap: var(--space-3);
    display: grid;
    gap: var(--space-gap);
  }
}
```

Rules that keep the tiers honest:

- A component must never reference a primitive token directly. If it does, theming that
  component becomes a find-and-replace across the codebase.
- Semantic tokens should be descriptive of use, not of appearance. `--color-action`, not
  `--color-blue`.
- Delete primitives that nothing uses. Unused tokens become dead weight that reviewers
  cannot tell apart from intentional extension points.

## Theming strategies

### 1. Custom property overrides (recommended)

```css
:root {
  --color-action: oklch(0.55 0.19 255);
}

.theme-dark {
  --color-action: oklch(0.75 0.14 255);
}

.theme-contrast {
  --color-action: oklch(0.3 0.2 255);
}
```

- Works at runtime, no reload, no duplicated stylesheet.
- Cost: a paint of everything using the overridden property.
- The class must be applied to an ancestor of every consumer, usually `<html>`.
- Set `color-scheme` alongside so native UI (scrollbars, form controls, the default
  canvas) matches: `color-scheme: light` / `color-scheme: dark`.

### 2. `prefers-color-scheme`

```css
:root {
  color-scheme: light dark;
}

@media (prefers-color-scheme: dark) {
  :root {
    --color-surface: oklch(0.22 0.01 260);
  }
}
```

Use this for the system preference. Combine with an explicit override class when the
user can choose, and make the explicit choice win by putting it after the media query.

### 3. `light-dark()`

```css
:root {
  color-scheme: light dark;
  --color-surface: light-dark(oklch(1 0 0), oklch(0.22 0.01 260));
}
```

`light-dark()` resolves based on the used `color-scheme`. It removes the need for a
duplicate media block, but it does not give you a manual override beyond changing
`color-scheme`. Check browser support for your matrix before depending on it.

### 4. Attribute-driven theming with `data-theme`

```html
<html lang="en" data-theme="dark">
```

```css
[data-theme='dark'] {
  --color-surface: oklch(0.22 0.01 260);
}
```

Useful when themes are server-rendered, so the correct theme is present in the first
byte and there is no flash. Prefer this over a class for SSR correctness.

### 5. Per-tenant brand tokens loaded at runtime

```ts
export function applyBrandTokens(tokens: Record<string, string>): void {
  const root = document.documentElement;
  for (const [name, value] of Object.entries(tokens)) {
    if (!name.startsWith('--')) {
      throw new Error(`Refusing to set non-token property: ${name}`);
    }
    root.style.setProperty(name, value);
  }
}
```

Two safeguards that matter:

- **Validate the names.** An allowlist of permitted token prefixes prevents a bad tenant
  payload from injecting arbitrary custom properties.
- **Remove tokens that no longer apply.** Setting `--brand-primary` for tenant A and
  then loading tenant B that does not define it leaves A's value in place. Explicitly
  clear the previous set.

## Preventing a flash of the wrong theme

1. Read the stored preference **before the framework hydrates**, in a blocking inline
   script in `<head>`, and set the attribute or class on `<html>` immediately.
2. Prefer a server-rendered `data-theme` when the preference is available on the
   server, for example from a cookie.
3. Keep the default token values and the themed token values in the same file so there
   is one network request and no ordering hazard.

## Token file structure

```css
/* tokens.css */
@layer tokens {
  /* 1. Primitive palettes */
  :root { /* colors, spacing scale, type scale, radii, shadows */ }

  /* 2. Semantic tokens with defaults */
  :root { /* --color-*, --space-*, --text-* */ }

  /* 3. Environment-dependent semantic overrides */
  @media (prefers-color-scheme: dark) {
    :root { /* only override semantic tokens, never primitives */ }
  }

  /* 4. Explicit theme hooks */
  [data-theme='dark'] { /* same overrides, higher precedence */ }
}
```

Rule 3 overriding only semantic tokens is what keeps the file readable. If the dark
theme redefines primitives, you have two palettes to maintain instead of one.

## Failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Vendor styles always win | Unlayered import | Import into a layer |
| Override only works in dev | Import order differs between dev and prod builds | Declare layer order explicitly, never rely on file order |
| Theme flash on load | Theme applied after first paint | Apply the attribute in a blocking head script or server-render it |
| Tenant B shows part of tenant A's brand | Old custom properties not cleared | Track and clear the applied set |
| A component ignores the theme | It hardcodes a primitive value | Route every color through a semantic token |
| Dark mode text is unreadable in one component | Only surface tokens were themed | Theme text and border tokens too, and verify contrast |
| `!important` in reduced-motion block does nothing | It is in a later layer than the animated rule | Put the preference block in the last layer or unlayered |

## Verification

- Run a contrast check on every foreground/background token pair, in every theme.
  `design-tokens.md` in the design-systems skill covers the process.
- Toggle each theme and check every interactive state, including focus rings.
- Check with `forced-colors: active` and with a browser zoom of 200%.
