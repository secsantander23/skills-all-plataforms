# Choosing and organizing a styling approach

Read `../SKILL.md` first. This file compares the practical tradeoffs of BEM, CSS
Modules, and utility-first code, and gives a file organization that scales.

## Comparison

| Criterion | BEM + plain CSS | CSS Modules | Utility-first |
| --- | --- | --- | --- |
| Selector specificity | Flat by convention | Flat by construction | Flat by construction |
| Global theming | Custom properties | Custom properties | Custom properties |
| Colocated with component | Manual | Automatic | Automatic |
| Runtime cost of extra classes | Small | Small | Small |
| Reading a complex style | Whole rule visible in one file | Whole rule visible in one file | Must jump between utility definitions |
| Constraining design in code | No, discipline only | No, discipline only | Yes, the utility set is the constraint |
| Deleting a component | Delete the files | Delete the module | Delete the class lists |
| Best for | Small teams, no build step, long-lived sites | Most component-based products | Large teams with a mature token system |

The honest summary: CSS Modules and utility-first are usually combined. Components own
their structure through CSS Modules, utilities handle one-off spacing and layout, and
tokens constrain everything.

## File organization

```
src/
  styles/
    reset.css           # minimal reset
    tokens.css          # primitives, semantics, theme overrides
    base.css            # element defaults, typography, focus ring
    utilities.css       # a small set of layout and visibility utilities
    index.css           # @layer declarations and @imports, the only entry point
  components/
    Button/
      Button.tsx
      Button.module.css
    Card/
      Card.tsx
      Card.module.css
```

Rules:

- `index.css` is the only stylesheet loaded by the app entry point. Everything else is
  imported by it, into a named layer.
- Component styles never leave their directory.
- Tokens are imported by `index.css` only. A component that needs a new token adds it
  to the token file, not to its own stylesheet.

## A minimal reset

```css
@layer reset {
  *,
  *::before,
  *::after {
    box-sizing: border-box;
  }

  * {
    margin: 0;
  }

  body {
    min-height: 100dvh;
    line-height: 1.5;
    -webkit-text-size-adjust: 100%;
  }

  img,
  picture,
  video,
  canvas,
  svg {
    display: block;
    max-inline-size: 100%;
  }

  input,
  button,
  textarea,
  select {
    font: inherit;
    color: inherit;
  }

  :focus-visible {
    outline: 2px solid currentColor;
    outline-offset: 2px;
  }

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
}
```

Why minimal: a large reset (normalize, reboot) makes global decisions for you that you
then undo component by component. This reset handles only the things that are actually
painful: box sizing, margin, media defaults, font inheritance, and a visible focus ring.

The focus ring line matters. Browsers' default focus indicators are removed by some
resets and some frameworks. Never remove `outline` without replacing it with a visible
`:focus-visible` style that meets the non-text contrast requirement.

## Composing across a component boundary

Prefer a custom property API over `composes` or deep selectors:

```css
/* Card.module.css */
.card {
  /* Consumers set these; the component reads them */
  --card-padding: var(--space-4);
  --card-media-width: 8rem;
  padding: var(--card-padding);
}

.card__media {
  inline-size: var(--card-media-width);
}
```

```tsx
<article className={`${styles.card} ${styles.compact}`}>...</article>
```

```css
.compact {
  --card-padding: var(--space-2);
  --card-media-width: 4rem;
}
```

The component's internals never branch on a variant class. They read a value. That
means adding a variant is a one-line custom property declaration, and a consumer can
expose the same variables without the component author writing a new modifier.

## BEM when you use it

If the project is plain CSS, BEM is a reasonable flat-naming convention:

```css
.card {}
.card__title {}
.card__title--large {}
.card--featured {}
.card.is-open .card__title {}
```

Modern equivalent that avoids the naming ceremony: use `:where()` for state and variant
classes so they contribute zero specificity.

```css
.card {}
.card__title {}
.card:where(.card--featured) .card__title { ... }
.card:where(.is-open) .card__title { ... }
```

Do not write `.card.card.card__title` to win specificity. That is a bug report about
your architecture, not a solution.

## Utility-first done carefully

```tsx
<div className="flex flex-col gap-3 rounded-md border p-4 md:flex-row md:items-center">
```

Rules that keep utilities readable:

- Keep the utility set small and hand-curated. A utility exists only if the design
  system defines that value.
- Extract a component when the same utility list appears three times.
- Put layout utilities in a layer after components so they win without `!important`.
- Do not invent a one-off value inline (`mt-[13px]`). If 13px is real, it is a token.

## Dark mode, print, and forced colors

Every stylesheet needs these blocks, at minimum:

```css
@media print {
  .card {
    border: 1px solid #000;
    background: none;
    box-shadow: none;
  }

  .app__nav {
    display: none;
  }

  a[href^='http']::after {
    content: ' (' attr(href) ')';
  }
}

@media (forced-colors: active) {
  .button {
    /* System colors so the OS high-contrast theme can take over */
    border: 1px solid ButtonText;
    background: ButtonFace;
    color: ButtonText;
  }
}
```

Print styles are the most-skipped accessibility-adjacent work. A user who prints a
receipt does not want your sticky navigation, and URLs matter when the print is
paper.

## Common failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Styles apply in dev but not in production | File import order changed | Declare layer order explicitly |
| A utility class does nothing | Utilities layer declared before components | Declare order as reset, tokens, base, components, utilities |
| A third-party widget ignores your styles | Unlayered vendor CSS | Import the vendor sheet into a layer |
| Component looks wrong in a container | Media-query layout | Container query |
| Focus ring is invisible | A reset removed `outline` without a replacement | Restore `:focus-visible` styling |
| Style file grows without bound | Every variant adds a modifier class | Expose custom properties instead |
| A utility breaks the design system | One-off value inlined | Promote the value to a token |

## Verification

- Toggle every layer on and off in DevTools to confirm ordering.
- Load the page with CSS disabled to check semantic HTML still conveys structure.
- Test at 200% browser zoom and at a 320px viewport width.
- Test in forced-colors mode.
- Run the page through a contrast checker on every token pair used by text.
