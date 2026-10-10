---
name: Design Systems
description: Covers design token architecture, primitive versus semantic tokens, component API and variant design, theming and dark mode, documentation with Storybook, versioning and deprecation, baking in accessibility, and incremental adoption strategies. Use when the user asks about building or maintaining a design system, standardizing UI across products, naming tokens, designing component APIs, or rolling a system out to an existing codebase.
metadata:
  opencode/autoinvoke: true
---

# Design Systems

A design system is three things that have to stay in sync: **tokens** (the decisions),
**components** (the implementations), and **documentation** (the contract). When they
drift, the system becomes a parts catalog and teams go back to local CSS.

The failure mode is not technical, it is organizational. Systems die because nobody
owns deprecation, or because adoption requires a rewrite. Design for both from the
start: every breaking change has a deprecation path, and every adoption step delivers
value on its own.

## When to use this

- Building a shared component library or token set.
- Naming and structuring tokens, or migrating to a standard token format.
- Designing a component's props, variants, and slots.
- Adding a theme, a brand, or dark mode.
- Deciding how to roll the system out across several products or teams.
- Deciding whether to adopt an existing system or build your own.

## Quick start

A token set with three tiers, generated into CSS, consumed only through semantic names:

```json
{
  "$schema": "https://www.designtokens.org/tr/drafts/format/",
  "color": {
    "blue": {
      "600": { "$value": "#1d4ed8", "$type": "color" }
    },
    "gray": {
      "50": { "$value": "#f8fafc", "$type": "color" },
      "900": { "$value": "#0f172a", "$type": "color" }
    }
  },
  "space": {
    "3": { "$value": "0.75rem", "$type": "dimension" },
    "4": { "$value": "1rem", "$type": "dimension" }
  },
  "semantic": {
    "color": {
      "action": {
        "$value": "{color.blue.600}",
        "$type": "color",
        "$description": "Default interactive accent. Must meet 4.5:1 on --color-surface."
      },
      "surface": {
        "$value": "{color.gray.50}",
        "$type": "color"
      },
      "text": {
        "$value": "{color.gray.900}",
        "$type": "color"
      },
      "danger": {
        "$value": "#b91c1c",
        "$type": "color"
      }
    }
  }
}
```

```css
/* generated/tokens.css - do not edit by hand */
:root {
  --ds-color-blue-600: #1d4ed8;
  --ds-color-gray-50: #f8fafc;
  --ds-color-gray-900: #0f172a;
  --ds-space-3: 0.75rem;
  --ds-space-4: 1rem;

  --ds-color-action: var(--ds-color-blue-600);
  --ds-color-surface: var(--ds-color-gray-50);
  --ds-color-text: var(--ds-color-gray-900);
  --ds-color-danger: #b91c1c;
}

[data-theme='dark'] {
  --ds-color-action: #93c5fd;
  --ds-color-surface: #0f172a;
  --ds-color-text: #f8fafc;
  --ds-color-danger: #fca5a5;
}
```

```css
/* a component, which never names a primitive */
.ds-button {
  background: var(--ds-color-action);
  color: var(--ds-color-surface);
  padding: var(--ds-space-3) var(--ds-space-4);
  border-radius: 0.375rem;
}
```

The three rules that keep this intact:

1. Components reference semantic tokens only.
2. Semantic tokens may reference primitives.
3. Primitives are never referenced by components.

Break rule 1 and dark mode becomes a fork of every component.

## Token architecture

| Tier | Example | Changes when | Consumed by |
| --- | --- | --- | --- |
| Primitive | `--ds-color-blue-600`, `--ds-space-4` | The palette or scale changes | Semantic tokens only |
| Semantic | `--ds-color-action`, `--ds-color-text` | A theme or role changes | Components |
| Component | `--ds-button-padding-inline` | That component's API changes | That component only |

Rules:

- **Name primitives by what they are** (`blue-600`, `space-4`).
- **Name semantics by what they do** (`color-action`, `color-text-muted`). Never
  `color-blue-action`: the moment dark mode changes the hue, the name lies.
- **Add a `$description` to every semantic token** stating the constraint it must
  satisfy, such as the minimum contrast ratio or where it may be used.
- **Document the scale as a scale, not as values.** Four is a step in a scale, not a
  number.

Spacing, type, and radius scales should be small and deliberate. A scale with 30 spacing
values is a scale no one can choose from.

```css
/* Spacing scale in rem so user font-size preferences are respected */
:root {
  --ds-space-0: 0;
  --ds-space-1: 0.25rem;
  --ds-space-2: 0.5rem;
  --ds-space-3: 0.75rem;
  --ds-space-4: 1rem;
  --ds-space-6: 1.5rem;
  --ds-space-8: 2rem;
  --ds-space-12: 3rem;
}

/* Type scale with fluid middle steps */
:root {
  --ds-text-sm: clamp(0.875rem, 0.84rem + 0.18vw, 0.9375rem);
  --ds-text-md: clamp(1rem, 0.95rem + 0.25vw, 1.125rem);
  --ds-text-lg: clamp(1.25rem, 1.1rem + 0.75vw, 1.5rem);
  --ds-text-xl: clamp(1.5rem, 1.2rem + 1.5vw, 2.25rem);
}
```

### Token format and pipeline

The Design Tokens Community Group format is the interoperable standard: a JSON (or YAML)
file with `$value`, `$type`, `$description`, and `$extensions` keys, using `{a.b.c}`
references. Documentation: https://www.designtokens.org/tr/drafts/format/

A pipeline looks like:

```
Figma variables (source)
  -> tokens JSON (Design Tokens Community Group format)
  -> build step
       -> CSS custom properties
       -> TypeScript constants (for canvas, charts, inline SVG)
       -> iOS / Android resources
```

Tools commonly used in this pipeline: Style Dictionary for generating platform outputs
from a token source, and Tokens Studio as a Figma plugin that keeps a Figma library in
sync with a JSON file. Confirm each tool's current input format and configuration keys
against its own documentation; this area moves quickly.

The most important pipeline rule: **tokens flow one way**. Figma and code both derive
from a token source of truth. If designers edit code tokens directly, the two drift.

## Component API design

A component's props are its public API. Every prop is a promise you have to keep.

| Prop kind | Purpose | Example |
| --- | --- | --- |
| Semantic variant | What the component means | `variant="primary" \| "danger"` |
| Size | Physical scale | `size="sm" \| "md" \| "lg"` |
| Boolean state | On/off state | `disabled`, `loading` |
| Composition slots | Caller supplies content | `icon`, `children`, `footer` |
| Escape hatch | Caller replaces the rendering | `asChild`, `render` |

```tsx
// Button.tsx
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react';
import styles from './Button.module.css';

export type ButtonVariant = 'primary' | 'secondary' | 'danger' | 'ghost';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  iconStart?: ReactNode;
  iconEnd?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = 'primary',
    size = 'md',
    loading = false,
    iconStart,
    iconEnd,
    disabled,
    className,
    children,
    type = 'button',
    ...rest
  },
  ref,
) {
  const isDisabled = disabled === true || loading;
  return (
    <button
      {...rest}
      ref={ref}
      type={type}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      className={[styles.button, styles[variant], styles[size], className]
        .filter(Boolean)
        .join(' ')}
    >
      {iconStart ? <span className={styles.iconStart} aria-hidden="true">{iconStart}</span> : null}
      <span className={styles.label}>{children}</span>
      {iconEnd ? <span className={styles.iconEnd} aria-hidden="true">{iconEnd}</span> : null}
    </button>
  );
});
```

API rules:

- **Sensible defaults, so the common case is one prop.** `<Button>Save</Button>` should
  be a primary button of medium size.
- **Variants are about meaning, not color.** `danger` is understood in every product.
  `red-500` is not.
- **Boolean props do not scale past about three.** `isCompact isSubtle isFlat isFancy`
  should be a variant and a size.
- **Accept `className` and spread `...rest`.** Consumers will need to add something you
  did not anticipate, and refusing creates a fork.
- **Forward refs** on anything that might need a measurement, a tooltip anchor, or a
  focus manager.
- **Separate the primitive from the styling.** Export the unstyled behavior (with
  accessibility baked in) plus the styled version. Teams that need to restyle a
  component should not have to reimplement its keyboard behavior.

Composition over configuration: a `Card` with `header`, `body`, and `footer` slots beats a
`Card` with `showFooterIcon showHeaderDivider useCompactFooter` booleans.

## Accessibility as part of the component, not a review step

The system should make the accessible thing the easy thing:

- **Use established primitives for hard interactions.** Menus, comboboxes, dialogs,
  date pickers, and tabs have keyboard behavior that is genuinely hard to get right.
  Build on a maintained headless library (for example Radix UI, Headless UI, or React
  Aria) and style the parts you own.
- **Bake in the focus ring.** Never ship a component without a visible `:focus-visible`
  style.
- **Bake in the states.** Every interactive component ships hover, active, focus-visible,
  disabled, and loading styles.
- **Bake in semantics.** Use the correct element. A button is a `<button>`.
- **Enforce contrast in tokens**, and check it automatically.
- **Test components in CI** with an automated accessibility checker plus keyboard tests
  for the interactions.

```tsx
import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import axe from 'axe-core';
import { Button } from './Button';

describe('Button', () => {
  it('has no automatically detectable accessibility violations', async () => {
    const { container } = render(<Button>Save</Button>);
    const results = await axe.run(container);
    expect(results.violations).toEqual([]);
    expect(screen.getByRole('button', { name: 'Save' })).toBeDefined();
  });
});
```

Automated checks catch roughly a third of real accessibility problems. The rest is
keyboard testing, screen reader testing, and content review. Say so in your contribution
guidelines, or teams will believe a green test means the component is accessible.

## Theming and dark mode

Dark mode is a token problem, not a component problem.

- Every semantic token has a value per theme. Themes override the semantic layer only.
- Set `color-scheme` so native UI matches the theme.
- Never invert colors algorithmically. `filter: invert(1)` on dark mode breaks images,
  logos, and any brand color. Author dark values.
- Test contrast per theme. A token pair that passes in light mode may fail in dark.
- Support a forced override (`data-theme`) in addition to the system preference, and
  persist the user's explicit choice.

```css
:root {
  color-scheme: light;
}

@media (prefers-color-scheme: dark) {
  :root:not([data-theme='light']) {
    color-scheme: dark;
    --ds-color-surface: #0f172a;
    --ds-color-text: #f8fafc;
  }
}

:root[data-theme='dark'] {
  color-scheme: dark;
  --ds-color-surface: #0f172a;
  --ds-color-text: #f8fafc;
}
```

The `:not([data-theme='light'])` guard is what lets an explicit light choice win over a
dark system preference.

For brand or multi-tenant theming, override the semantic tokens from a validated payload
at runtime rather than shipping a stylesheet per brand.

## Documentation

Documentation is the deliverable, not a nice-to-have. If it is not written, every team
reimplements the component.

Each component needs:

1. **When to use it, and when not to.** The single most valuable section.
2. **Props table** with types, defaults, and which are required.
3. **Live examples** for each variant and state, ideally interactive.
4. **Accessibility notes**: the role, the keyboard model, the labels it expects.
5. **Do and do not** visual examples.
6. **Migration notes** for the previous version.

Storybook is the most widely used documentation environment for component libraries
(https://storybook.js.org). Its interaction tests and accessibility addon are worth
using, but check the current add-on names and configuration against the Storybook
documentation for the version you install.

Adoption depends on discoverability. Document in the place developers already look,
and keep each example copy-pasteable and complete.

## Versioning and deprecation

Design systems are libraries. Treat them like one.

- **Semantic versioning** on the package, with a separate documented policy for visual
  changes. Many systems ship visual changes in minor versions and communicate them as
  non-breaking; that is a policy choice, and it must be written down.
- **Every breaking change gets a codemod or a codemod-able migration guide.** Manual
  migrations are how adoption stalls.
- **Deprecate before you remove.** Mark a prop deprecated in the type definition, warn
  in development, document the replacement, keep it working for a published window, then
  remove in a major.
- **Changelog entries are written for consumers**, not for the diff.
- **Visual regression tests** on component stories catch unintended visual changes
  before they ship. This matters more than unit tests for a design system.

```tsx
export interface ButtonProps {
  /** @deprecated Use `variant="primary"` instead. Removed in the next major. */
  primary?: boolean;
  variant?: ButtonVariant;
}
```

## Adoption strategy

Adoption fails when the first step is "migrate everything". Design steps that each pay
for themselves:

| Step | Effort | Payoff | Risk |
| --- | --- | --- | --- |
| 1. Publish tokens and use them for new work only | Low | Immediate consistency for new UI | None |
| 2. Ship one or two high-leverage components (Button, Input) | Medium | Removes duplicated local code | Low, if APIs are compatible |
| 3. Provide codemods for existing local components | High | Removes the migration tax | Medium |
| 4. Enforce via lint rules and CI | Medium | Prevents drift back | Medium, announce it |
| 5. Migrate remaining screens | High | Full consistency | Highest, do last |

Rules for adoption:

- **Never block a product release on the system.** The system follows the roadmap; it
  does not gate it.
- **Have a compatibility story.** If your `Button` cannot replace the product's current
  button without prop changes, either add the props or start with a different component.
- **Make the escape hatch explicit.** Products need to do unstyled things sometimes.
  Exporting primitives makes that a supported path rather than a fork.
- **Measure adoption**, not just delivery: share of screens using system components,
  count of duplicate local components, and the rate of one-off `#hex` values in the
  codebase.
- **Write the contribution model.** Either the system team owns contributions (slow,
  consistent) or products contribute behind a review (fast, needs strong review). Pick
  one and document it.

## Checklist

- Tokens have three tiers and components reference only the semantic tier.
- Every semantic token has a description stating its constraint, including contrast.
- Tokens are generated from a single source, not hand-edited in multiple platforms.
- Every component has sensible defaults, forwards refs, accepts `className`, and spreads
  rest props.
- Boolean props are minimized; variants are semantic.
- Every interactive component has hover, focus-visible, active, disabled, and loading
  states, plus a keyboard test.
- Hard interactions are built on an accessible headless primitive.
- Each theme overrides the semantic tier only, and sets `color-scheme`.
- Every component has docs: when to use, props, examples, accessibility, and migration.
- Breaking changes have a codemod and a published deprecation window.
- CI runs visual regression tests and an automated accessibility check.

## Common mistakes

- **Components referencing primitive tokens.** Symptom: dark mode requires editing every
  component file. Fix: route through the semantic tier.
- **A token set with 200 primitives and no semantics.** Symptom: no one knows which value
  to use, so inconsistency returns. Fix: publish a small semantic set as the supported
  API.
- **Visual changes shipped without a version note.** Symptom: products upgrade and their
  UIs shift unexpectedly, and trust in the system collapses. Fix: changelog plus visual
  regression tests.
- **Dark mode by inverting.** Symptom: logos invert, brand colors turn wrong, images go
  negative. Fix: author explicit dark values.
- **Documenting only the props table.** Symptom: teams misuse the component anyway. Fix:
  write the when-not-to-use section first.
- **Building from scratch when an accessible primitive exists.** Symptom: months of
  keyboard bugs in a menu or combobox. Fix: adopt a headless library and style it.
- **A design system with no owner.** Symptom: unreviewed additions, no deprecations, no
  releases. Fix: name a team and a release cadence before announcing the system.
- **Gating a product release on the migration.** Symptom: the system becomes the biggest
  blocker on the roadmap and loses its advocates. Fix: incremental, value-first steps.
- **Contrast verified only in light mode.** Symptom: unreadable text in dark mode or in
  high-contrast mode. Fix: check every token pair per theme, and add the check to CI.

## Anti-patterns

- **A component library with no token layer.** Symptom: every product re-themes by
  copying the component and editing the stylesheet.
- **Config flags instead of slots.** Symptom: `showHeaderIcon showFooterAction
  hideDivider` grows to twenty booleans and no combination is tested.
- **Shipping a component without a keyboard test.** Automated accessibility checks do
  not test keyboard interaction.
- **Version numbers tied to design tool pages.** Symptom: consumers cannot tell whether
  a change is safe. Fix: semantic versioning on the published package.
- **One component library for every surface.** Web, mobile, and embedded platforms have
  different constraints; share tokens and interaction rules, not necessarily components.
- **Design tokens named after Figma layer names.** They become meaningless outside the
  design file.
- **Announcing a system before the first two components are used in production.** Early
  adopters find the problems that would otherwise be fixed in a major release.
- **Treating the design system as a deliverable rather than a product with users.** No
  one adopts software they do not know exists or cannot get help with.

## References

- Design Tokens Community Group format: https://www.designtokens.org/tr/drafts/format/
- Style Dictionary (token build pipeline): https://styledictionary.com/
- Tokens Studio for Figma: https://tokens.studio/
- Storybook: https://storybook.js.org
- W3C Design Tokens Community Group: https://www.w3.org/community/design-tokens/
- W3C DTCG on GitHub: https://github.com/design-tokens/community-group
- Radix UI primitives: https://www.radix-ui.com/primitives
- Headless UI: https://headlessui.com/
- React Aria components: https://react-spectrum.adobe.com/react-aria/
- WCAG 2.2: https://www.w3.org/TR/WCAG22/
- axe-core: https://github.com/dequelabs/axe-core
- Semantic Versioning: https://semver.org/
- CSS custom properties: https://developer.mozilla.org/en-US/docs/Web/CSS/CSS_cascading_variables/Using_CSS_custom_properties
- Cascade layers: https://developer.mozilla.org/en-US/docs/Web/CSS/@layer
