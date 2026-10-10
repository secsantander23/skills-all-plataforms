# Component API and adoption reference

Read `../SKILL.md` first. This file covers component design in more depth, how to
decide between adopting and building, and how to measure adoption.

## Adopt or build

| Situation | Decision |
| --- | --- |
| Interaction primitives (menu, combobox, dialog, date picker, tabs) | Adopt a headless library |
| You need exact visual control of a brand-critical component | Build, on a headless primitive |
| A system already matches your needs within about 70% | Adopt and wrap; do not fork |
| You need the remaining 30% | Contribute upstream or wrap with an escape hatch |
| Team has no one to maintain it | Adopt, do not build |

Total cost of ownership of a component is not the code. It is every future keyboard bug,
every browser quirk, every screen reader difference, and every visual regression review,
across every product that uses it. That is why hard interactions come from a library that
already pays that cost.

## Primitive plus styled layer

Export two things:

1. **A primitive.** Unstyled or minimally styled, accessible, composable, accepts an
   `asChild`-style escape hatch.
2. **The system component.** The primitive plus tokens, defaults, and states.

```tsx
// primitive: behavior and accessibility, no visual decisions
import { Slot, type ComponentPropsWithoutRef } from 'react';

export interface ButtonPrimitiveProps extends ComponentPropsWithoutRef<'button'> {
  asChild?: boolean;
}

export function ButtonPrimitive({
  asChild = false,
  ...props
}: ButtonPrimitiveProps) {
  const Comp = asChild ? Slot : 'button';
  return <Comp type={asChild ? undefined : 'button'} {...props} />;
}
```

```tsx
// styled: tokens, defaults, states, and the public API products use
import { forwardRef } from 'react';
import { ButtonPrimitive } from './ButtonPrimitive';
import styles from './Button.module.css';

export const Button = forwardRef(function Button(
  { variant = 'primary', size = 'md', className, ...rest },
  ref,
) {
  return (
    <ButtonPrimitive
      {...rest}
      ref={ref}
      className={[styles.button, styles[variant], styles[size], className]
        .filter(Boolean)
        .join(' ')}
    />
  );
});
```

The escape hatch matters: `asChild` lets a consumer render a router link styled as a
button without the library guessing which router you use.

## Component documentation template

```md
# Button

Triggers an action. Use `Link` when navigating; use `Button` when performing an
operation on the current page.

## Do not use

- As a navigation element. Use `Link`.
- For form submission in a dialog footer when a `SplitButton` fits the pattern.
- With more than two actions in a row. Use an overflow menu.

## Variants

| Variant | Use for |
| --- | --- |
| `primary` | The single most important action on the screen |
| `secondary` | Supporting actions |
| `danger` | Destructive, irreversible actions |
| `ghost` | Low-emphasis actions in dense layouts |

## Props

See the generated props table below.

## Accessibility

- Role: `button`. It is a real `<button>` by default; `asChild` changes the role, so
  you own the semantics when you use it.
- Keyboard: Enter and Space activate. Focus is visible via `:focus-visible`.
- `loading` sets `aria-busy="true"` and disables activation. Announce completion in
  the surrounding region rather than in the button label.
- The label must come from `children` or `aria-label`. Icon-only buttons require
  `aria-label`.

## Migration

See the "2.x to 3.x" section below.
```

## Deprecation mechanics

```tsx
/** @deprecated since 3.1.0. Use `variant="primary"`. Removal planned for the next major. */
primary?: boolean;
```

Steps that actually work:

1. **Announce.** Changelog entry, docs note, and a release that still works.
2. **Warn.** In development builds only, warn once per prop name with a pointer to the
   replacement.
3. **Instrument.** Log usage of the deprecated prop so you know how much depends on it
   before removal.
4. **Provide a codemod.** For simple renames, `npx <your-codemod>` should do the work.
5. **Remove in a major.** With a migration guide written for the person doing the upgrade.

## Measuring adoption

Concrete, checkable numbers:

| Metric | How to measure |
| --- | --- |
| Component coverage | Share of UI elements rendered from system components, by screen |
| Token coverage | Count of raw hex, px, and ms literals outside the token layer |
| Duplicate components | Components in product repos that shadow a system component |
| Escape hatch rate | Uses of `asChild`, custom CSS, and token overrides per quarter |
| Migration pull | Open codemod migrations still blocking upgrades |
| Support load | Questions per component; the top three usually mean missing docs |

Track these quarterly. A rising escape hatch rate means the API is missing something;
rising duplicates means adoption stalled.

## Contribution models

| Model | How it works | Works when | Fails when |
| --- | --- | --- | --- |
| Central team owns everything | PRs accepted by the system team | Small, highly consistent product | Teams need a change today |
| Federated | Any team contributes, a council reviews | Many teams, high velocity | Review capacity is the bottleneck |
| Copy and own | System publishes guidance and packages; teams fork | Very heterogeneous products | Divergence; you end up with N buttons |

Pick one explicitly and write down who reviews, how long review takes, and what the
acceptance criteria are. An undocumented contribution model produces an unreviewed
pile of PRs or a six-week review queue, and both end the system.

## Anti-drift enforcement

Once adoption is real, add guardrails:

- **Lint rules.** Forbid raw hex colors and raw pixel values outside allowed files.
  Forbid importing a component from a deprecated path.
- **CI checks.** Regenerate tokens and fail on a diff. Run the accessibility checker.
  Run visual regression on component stories.
- **Bundle check.** Fail if a package imports a deprecated entry point.

Each rule should come with an explanation in the error message, not just a rule name.

## Failure modes

| Symptom | Cause | Fix |
| --- | --- | --- |
| Products wrap system components instead of using them | API cannot express what they need | Add slots before forking; track escape hatch rate |
| Deprecation warnings ignored | No replacement path was provided | Ship the replacement with the deprecation |
| Visual regressions surprise consumers | No visual regression tests | Add them on component stories |
| System ships rarely | Central review bottleneck | Federate with a written rubric |
| Contributions stall in review | Undocumented acceptance criteria | Write the rubric and the SLA |
| Consumers keep raw hex values | No lint rule | Add one with a codemod |

## References

- Design Tokens Community Group: https://www.designtokens.org/tr/drafts/format/
- Storybook: https://storybook.js.org
- Radix UI primitives: https://www.radix-ui.com/primitives
- Headless UI: https://headlessui.com/
- React Aria components: https://react-spectrum.adobe.com/react-aria/
- axe-core: https://github.com/dequelabs/axe-core
- WCAG 2.2: https://www.w3.org/TR/WCAG22/
- Semantic Versioning: https://semver.org/
- CSS Modules: https://github.com/css-modules/css-modules
