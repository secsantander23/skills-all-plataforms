---
name: Frontend Accessibility
description: Covers WCAG 2.2 level AA criteria, semantic HTML, ARIA roles states and live regions, keyboard navigation and focus management, color contrast, forms and labels, and automated testing with axe. Use when the user asks about making a page accessible, fixing an accessibility bug, choosing ARIA attributes, managing focus in a dialog or menu, meeting contrast requirements, or setting up accessibility testing.
metadata:
  opencode/autoinvoke: true
---

# Frontend Accessibility

Accessibility is not a feature you add at the end. It is mostly a consequence of using
the right element for the job and not breaking the behaviors the browser and the
platform already give you.

The rule that produces most of the improvement: **the first rule of ARIA is not to use
ARIA.** A native `<button>` gets focus, keyboard activation, the correct role, and the
correct announcement for free. A `<div role="button">` gets none of those, and you
reimplement all of them badly.

WCAG 2.2 is the current W3C Recommendation: https://www.w3.org/TR/WCAG22/

## When to use this

- Reviewing or fixing an accessibility defect.
- Choosing between a native element and a custom widget.
- Building a dialog, menu, combobox, tabs, or a live-updating region.
- Getting a form's labels, errors, and validation announcements right.
- Meeting contrast requirements for text, icons, and focus indicators.
- Adding automated accessibility checks to a test suite or a CI pipeline.

## Quick start

A form field group that is labeled, described, validated, and announced correctly. No
ARIA needed beyond `aria-invalid` and a live region:

```html
<form novalidate>
  <div class="field">
    <label for="email">Work email</label>
    <p id="email-hint" class="hint">Use the address your team is invited to.</p>
    <input
      id="email"
      name="email"
      type="email"
      autocomplete="email"
      required
      aria-describedby="email-hint email-error"
      aria-invalid="true"
    />
    <p id="email-error" class="error">Enter an email address that contains an @.</p>
  </div>

  <button type="submit">Invite</button>
</form>
```

A dialog implemented with the native `<dialog>` element, which gives focus trapping,
the top layer, Escape handling, and the backdrop:

```html
<dialog id="confirm-dialog" aria-labelledby="confirm-title">
  <form method="dialog">
    <h2 id="confirm-title">Delete this project?</h2>
    <p>This cannot be undone.</p>
    <menu>
      <button value="cancel">Cancel</button>
      <button value="confirm" class="danger">Delete</button>
    </menu>
  </form>
</dialog>
```

```ts
const dialog = document.getElementById('confirm-dialog') as HTMLDialogElement | null;

export function openConfirm(onConfirm: () => void): void {
  if (!dialog) return;
  dialog.showModal();
  dialog.addEventListener(
    'close',
    () => {
      if (dialog.returnValue === 'confirm') onConfirm();
    },
    { once: true },
  );
}
```

## The four principles

| Principle | Question it answers | Typical failures |
| --- | --- | --- |
| Perceivable | Can it be presented in alternate ways? | Missing alt text, no captions, insufficient contrast |
| Operable | Can it be used with a keyboard and without a pointer? | Custom widgets with no keyboard support, traps, tiny targets |
| Understandable | Is it predictable and readable? | Unclear labels, unexpected context changes, no error messages |
| Robust | Is it reliably interpretable by assistive tech? | Invalid ARIA, missing names, wrong roles |

Conformance levels are A, AA, and AAA. Legal obligations in many jurisdictions (the
European Accessibility Act, Section 508 in the US, and equivalents elsewhere) generally
reference WCAG AA. Target AA as the floor.

## Semantic HTML first

| Instead of | Use | Why |
| --- | --- | --- |
| `<div onclick>` | `<button>` | Focus, Enter/Space, role, and announcement for free |
| `<span onclick>` on text | `<a href>` | Right role, right activation, opens in a new tab correctly |
| `<div class="heading">` | `<h1>` to `<h6>` | Document outline, heading navigation |
| `<p class="label">` | `<label for>` | Association and click target |
| `<img>` as a button | `<button>` containing the `<img>` with `alt=""` | Correct role and name |
| Table layout for data | `<table>` with `<caption>`, `<th scope>` | Navigation and relationships |
| `<br>` for layout | CSS | Screen readers announce breaks oddly |
| `aria-label` on a `<button>` | Visible text | Label in Name (2.5.3) wants the visible text included |

```html
<a href="/reports" aria-current="page">Reports</a>

<table>
  <caption>Quarterly revenue by region</caption>
  <thead>
    <tr>
      <th scope="col">Region</th>
      <th scope="col">Revenue</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <th scope="row">EMEA</th>
      <td>1,204</td>
    </tr>
  </tbody>
</table>
```

## ARIA: the minimum necessary

ARIA changes how assistive technology interprets an element. It does not add
capability the browser does not have.

| Attribute | Purpose | Example |
| --- | --- | --- |
| `role` | The element's role, when the native one is wrong | `role="tab"`, `role="alert"` |
| `aria-labelledby` | Name from other elements by id | `aria-labelledby="dialog-title"` |
| `aria-describedby` | Description from other elements by id | `aria-describedby="email-hint"` |
| `aria-expanded` | Collapsed or expanded state | `aria-expanded="false"` on a disclosure |
| `aria-selected` | Selection state | `aria-selected="true"` on a tab |
| `aria-checked` | Checkbox, radio, or switch state | `aria-checked="mixed"` for a tri-state tree |
| `aria-pressed` | Toggle button state | `aria-pressed="true"` |
| `aria-disabled` | Disabled, but still focusable | Prefer the native `disabled` for form controls |
| `aria-invalid` | The value is not valid | `aria-invalid="true"` on a failed field |
| `aria-current` | Current item in a set | `aria-current="page"` |
| `aria-live` | Announce changes to that region | `aria-live="polite"` |
| `aria-hidden` | Hide from assistive tech | `aria-hidden="true"` on a decorative icon |

```html
<button type="button" aria-expanded="false" aria-controls="details-panel">
  Shipping details
</button>
<div id="details-panel" hidden>
  <p>Ships in 3 to 5 business days.</p>
</div>
```

Rules:

- **`hidden` beats `aria-hidden` for hiding content.** `hidden` removes the element from
  layout and from the accessibility tree. Use `aria-hidden="true"` only on elements that
  must remain visible, such as a decorative SVG.
- **Never put `aria-hidden="true"` on an element that contains a focusable descendant.**
  The focus lands somewhere invisible. Symptom: a keyboard user tabs into nothing.
- **A name is mandatory** for every interactive role. If there is no accessible name, it
  will not be announced usefully.
- **`aria-*` attributes do not create behavior.** `aria-expanded="false"` does not make
  the panel collapse; you still need the state and the handler.

### Live regions for status messages

WCAG 2.2 success criterion 4.1.3 Status Messages requires that status messages be
programmatically determinable without moving focus.

```html
<!-- Polite: waits for a pause, does not interrupt -->
<div id="save-status" role="status"></div>

<!-- Assertive: interrupts immediately, use sparingly -->
<div id="error-status" role="alert"></div>
```

```ts
const status = document.getElementById('save-status');
if (status) status.textContent = 'Draft saved at 14:32.';
```

A live region must exist in the DOM **before** the text is inserted. Adding a node with
the text and the `role` at the same time is unreliable across screen readers. Render an
empty container, then set its text content.

```ts
export async function submit(form: HTMLFormElement): Promise<void> {
  const status = document.getElementById('save-status');
  const button = form.querySelector('button[type="submit"]');
  if (button instanceof HTMLButtonElement) button.disabled = true;
  try {
    const res = await fetch(form.action, { method: 'POST', body: new FormData(form) });
    if (status) {
      status.textContent = res.ok ? 'Draft saved.' : 'Save failed. Try again.';
    }
  } finally {
    if (button instanceof HTMLButtonElement) button.disabled = false;
  }
}
```

## Keyboard and focus management

Every interactive element must be reachable with Tab and operable with the keyboard,
with no trap (2.1.1 and 2.1.2).

Expected keyboard behavior by widget:

| Widget | Keys |
| --- | --- |
| Button, link, checkbox, radio | Enter and Space (links: Enter only) |
| Disclosure | Enter or Space toggles |
| Dialog | Escape closes; focus moves in on open, returns to the trigger on close |
| Tabs | Arrow keys move between tabs; Tab moves to the panel |
| Menu | Arrow keys move, Enter activates, Escape closes, typeahead optional |
| Combobox | Arrow keys move the active option, Enter selects, Escape closes |
| Data grid | Arrow keys navigate cells, Ctrl+Arrow extends, Page keys page |

```css
/* Never remove the focus indicator without a replacement */
:focus-visible {
  outline: 2px solid var(--color-focus);
  outline-offset: 2px;
}
```

Focus management patterns:

- **Moving focus on open.** When a dialog opens, move focus to the dialog's first
  focusable element or to the dialog itself. `<dialog>.showModal()` does this for you.
- **Returning focus on close.** When a dialog closes, return focus to whatever opened it.
  Otherwise a keyboard user lands at the top of the document.
- **Trapping focus.** A modal dialog must keep Tab inside. `<dialog>` does it. If you
  build a custom modal, you must implement it, and it is easy to get subtly wrong.
- **Programmatic focus.** Give the target `tabindex="-1"` and call `.focus()`. This
  works on any element.
- **Hiding background from assistive tech** while a modal is open: `inert` on the
  background container, which removes it from both focus order and the accessibility
  tree.

```ts
export function openModal(dialog: HTMLDialogElement, appRoot: HTMLElement): void {
  const returnTo = document.activeElement as HTMLElement | null;
  appRoot.inert = true;
  dialog.showModal();

  dialog.addEventListener(
    'close',
    () => {
      appRoot.inert = false;
      returnTo?.focus();
    },
    { once: true },
  );
}
```

`inert` is supported in all current evergreen browsers. Check MDN for your matrix.

## WCAG 2.2 level AA criteria worth knowing

Full list: https://www.w3.org/TR/WCAG22/ . The ones that come up most in frontend work:

| Criterion | Level | Requirement |
| --- | --- | --- |
| 1.1.1 Non-text Content | A | Meaningful images have text alternatives; decorative images use `alt=""` |
| 1.3.1 Info and Relationships | A | Structure is conveyed in markup, not only visually |
| 1.3.4 Orientation | AA | Works in portrait and landscape unless orientation is essential |
| 1.3.5 Identify Input Purpose | AA | Inputs for name, email, address, and payment carry the right `autocomplete` token |
| 1.4.1 Use of Color | A | Color is not the only visual means of conveying information |
| 1.4.3 Contrast (Minimum) | AA | 4.5:1 for normal text, 3:1 for large text |
| 1.4.4 Resize Text | AA | Text scales to 200% without loss of content |
| 1.4.10 Reflow | AA | Usable at 320 CSS pixels wide without two-dimensional scrolling |
| 1.4.11 Non-text Contrast | AA | 3:1 for control boundaries and meaningful graphics |
| 1.4.12 Text Spacing | AA | No clipping when line height, spacing, and letter spacing are overridden |
| 1.4.13 Content on Hover or Focus | AA | Hover content is dismissible, hoverable, and persistent |
| 2.1.1 Keyboard / 2.1.2 No Keyboard Trap | A | All functionality keyboard-operable, no traps |
| 2.4.1 Bypass Blocks | A | Skip link or landmarks let keyboard users skip repeated content |
| 2.4.2 Page Titled | A | Each page has a descriptive title |
| 2.4.3 Focus Order | A | Focus order preserves meaning and operability |
| 2.4.7 Focus Visible | AA | Keyboard focus is visible |
| 2.4.11 Focus Not Obscured (Minimum) | AA | New in 2.2; a focused element is not fully hidden by other content |
| 2.5.3 Label in Name | A | The accessible name contains the visible label text |
| 2.5.7 Dragging Movements | AA | New in 2.2; anything draggable has a single-pointer alternative |
| 2.5.8 Target Size (Minimum) | AA | New in 2.2; targets are at least 24 by 24 CSS pixels, or spaced to qualify |
| 3.1.1 Language of Page | A | `lang` is set; mark language changes inline |
| 3.2.1 On Focus / 3.2.2 On Input | A | Focus and input do not cause unexpected context change |
| 3.2.6 Consistent Help | A | New in 2.2; help mechanisms appear in the same relative order |
| 3.3.1 Error Identification | A | Errors are identified in text |
| 3.3.2 Labels or Instructions | A | Inputs have labels and needed format information |
| 3.3.3 Error Suggestion | AA | When known, the error message suggests a correction |
| 3.3.4 Error Prevention | AA | Submissions that are legal, financial, or destructive are reversible, checked, or confirmable |
| 3.3.7 Redundant Entry | A | New in 2.2; information already entered is auto-populated or selectable |
| 3.3.8 Accessible Authentication (Minimum) | AA | New in 2.2; no cognitive function test for login, and paste is allowed |
| 4.1.2 Name, Role, Value | A | Every UI component exposes name, role, and current value |
| 4.1.3 Status Messages | AA | Status messages are announced without moving focus |

Note: 4.1.1 Parsing, present in 2.1, is obsolete in 2.2 because browsers now handle
malformed markup. Do not implement it.

## Color contrast

| Content | Minimum ratio | Criterion |
| --- | --- | --- |
| Normal text | 4.5:1 | 1.4.3 |
| Large text (18pt, or 14pt bold, and larger) | 3:1 | 1.4.3 |
| UI component boundaries and meaningful graphics | 3:1 | 1.4.11 |
| Focus indicator against adjacent colors | 3:1 | 1.4.11 |

```ts
type Rgb = { r: number; g: number; b: number };

function parseHex(hex: string): Rgb {
  const h = hex.replace('#', '');
  const full = h.length === 3 ? h.split('').map((c) => c + c).join('') : h;
  return {
    r: parseInt(full.slice(0, 2), 16) / 255,
    g: parseInt(full.slice(2, 4), 16) / 255,
    b: parseInt(full.slice(4, 6), 16) / 255,
  };
}

function relativeLuminance({ r, g, b }: Rgb): number {
  const channel = (c: number) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(foreground: string, background: string): number {
  const a = relativeLuminance(parseHex(foreground));
  const b = relativeLuminance(parseHex(background));
  const [high, low] = a > b ? [a, b] : [b, a];
  return (high + 0.05) / (low + 0.05);
}
```

The formula is from the WCAG relative luminance definition. Verify against
https://www.w3.org/TR/WCAG22/#dfn-relative-luminance.

Contrast failures that automated tools miss: text over images, gradient text, disabled
controls (exempt, but still check), placeholder text, icon-only buttons, and hover and
focus states.

## Forms

- Every input has a `<label for="id">`. A placeholder is not a label; it disappears on
  input and usually fails contrast.
- Group related controls in a `<fieldset>` with a `<legend>`. Radio groups especially.
- Use the right `type` and the right `autocomplete` token. `autocomplete` maps directly
  to criterion 1.3.5.
- Mark optional fields explicitly rather than only marking required ones, if users need
  to know.
- Errors: text, adjacent to the field, referenced by `aria-describedby`, with
  `aria-invalid="true"` on the input.
- Never disable the submit button as the only error signal. A disabled button with no
  explanation is a dead end; show why.
- Do not clear user input on an error.

```html
<fieldset>
  <legend>Shipping address</legend>
  <label for="line1">Address line 1</label>
  <input id="line1" name="line1" autocomplete="address-line1" required />
</fieldset>
```

## Testing

Layer the testing. Each layer catches what the others miss.

| Layer | Catches | Tool |
| --- | --- | --- |
| Automated rule checks | Missing names, contrast, invalid ARIA, landmark and heading structure | axe-core |
| Keyboard walkthrough | Traps, missing handlers, focus order, focus return | Manual, plus Playwright keyboard tests |
| Zoom and reflow | Clipped text, horizontal scroll | Manual at 200% zoom and 320px width |
| Screen reader | Announcement quality, live region behavior, verbosity | Manual with NVDA, VoiceOver, or Orca |
| User testing | Whether people can actually complete the task | Real users |

Automated rules catch a minority of real problems. Never report a green automated run
as "accessible".

```bash
# Standalone page check against a running server
npx @axe-core/cli https://example.com/

# In a Playwright test
npm install --save-dev @axe-core/playwright
```

```ts
import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

test('checkout page has no detectable violations', async ({ page }) => {
  await page.goto('/checkout');
  const results = await new AxeBuilder({ page })
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
    .analyze();

  expect(results.violations).toEqual([]);
});
```

Check the current API and tag names for your installed axe version; the Playwright
integration and the WCAG tag list have both changed between releases. Also run the axe
browser extension manually: it detects rules that automated scans cannot, such as
missing `alt` for an image used as a link.

## Checklist

- Every interactive element is a native element with the correct role.
- Every element that takes focus has a visible focus indicator that is not obscured.
- Every input has a persistent, associated label.
- Errors are text, adjacent, referenced with `aria-describedby`, and announced.
- Status messages use a live region that existed before the text was inserted.
- Modals move focus in, trap focus, close on Escape, and return focus to the trigger.
- Every image has an `alt`, empty for decorative.
- Heading levels descend without skipping, and every page has one `<h1>`.
- Color is never the only means of conveying information.
- Text and non-text contrast meets 4.5:1 and 3:1 respectively, in every state.
- The page works at 200% zoom and at 320 CSS pixels wide.
- `lang` is set on the root element.
- There is a skip link and landmarks (`header`, `nav`, `main`, `footer`).
- Automated checks run in CI, and keyboard navigation was tested by hand.

## Common mistakes

- **A `<div>` with `onclick` and `role="button"`.** Symptom: not focusable, Space does
  not activate it, announced as a button but behaves like a link. Fix: use `<button>`.
- **`aria-hidden="true"` on a wrapper containing focusable elements.** Symptom: a
  keyboard user tabs into invisible content. Fix: use `hidden` or `inert`.
- **A live region added at the same time as its message.** Symptom: nothing is
  announced. Fix: render the empty container first, then set its text.
- **Removing the focus outline.** Symptom: 2.4.7 Focus Visible fails outright. Fix:
  replace with `:focus-visible` styling that meets 3:1.
- **Placeholder as the only label.** Symptom: the label vanishes on input and screen
  readers may not announce it consistently. Fix: a real `<label>`.
- **`aria-label` overriding visible text.** Symptom: 2.5.3 Label in Name fails; voice
  control users say the visible name and nothing matches. Fix: include the visible text.
- **A sticky header covering the focused element.** Symptom: 2.4.11 Focus Not Obscured
  fails. Fix: add `scroll-margin-top` equal to the header height, or use
  `scroll-padding-top` on the scroll container.
- **Icon-only buttons with no accessible name.** Symptom: announced as "button" with no
  name. Fix: `aria-label` on the button, `aria-hidden="true"` on the icon.
- **Disabling submit until the form is valid.** Symptom: users cannot find out why. Fix:
  validate on submit and show messages.
- **Color-only error indication.** Symptom: 1.4.1 fails. Fix: add an icon and text.
- **A custom modal without a focus trap.** Symptom: Tab walks out of the dialog into the
  page behind it. Fix: use `<dialog>.showModal()` or a maintained primitive.

## Anti-patterns

- **ARIA instead of semantics.** Symptom: a screen reader announces a div as a button
  that cannot be activated. The rule of thumb: if a native element exists, use it.
- **Automated checks treated as a compliance claim.** They catch a fraction of issues and
  produce false confidence. Say what was and was not tested.
- **A "skip to content" link that is permanently visible on every page load.** Symptom:
  visual clutter and a new focus stop for sighted keyboard users. Fix: visually hide it
  and reveal it on focus.
- **Disabling zoom** with `user-scalable=no` in a viewport meta tag. Symptom: 1.4.4
  fails outright and it blocks users who need magnification.
- **Removing the placeholder to replace it with a floating label** that animates over
  the input. Symptom: overlap at 200% zoom, fails 1.4.12 Text Spacing.
- **Using `title` as a label.** Symptom: not announced reliably, disappears on touch, and
  fails to meet any labelling requirement.
- **Red, amber, green status chips with no text.** Symptom: 1.4.1 Use of Color fails.
  Fix: text plus an icon, not just a color.
- **Building a menu, combobox, or grid from scratch** to save a dependency. The keyboard
  model alone is a project.
- **`tabindex` greater than 0.** Symptom: focus order no longer matches DOM order, which
  fails 2.4.3. Use `0` or `-1` only.
- **Reviewing accessibility once before launch.** It regresses every release. Put checks
  in CI and put keyboard testing in the definition of done.

## References

- WCAG 2.2 (W3C Recommendation): https://www.w3.org/TR/WCAG22/
- How to Meet WCAG: https://www.w3.org/WAI/WCAG22/quickref/
- ARIA Authoring Practices Guide: https://www.w3.org/WAI/ARIA/apg/
- WAI-ARIA specification: https://www.w3.org/TR/wai-aria-1.2/
- MDN accessibility guides: https://developer.mozilla.org/en-US/docs/Web/Accessibility
- HTML accessibility semantics: https://developer.mozilla.org/en-US/docs/Web/HTML/Accessibility
- ARIA live regions: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/ARIA_Live_Regions
- `inert`: https://developer.mozilla.org/en-US/docs/Web/HTML/Global_attributes/inert
- `:focus-visible`: https://developer.mozilla.org/en-US/docs/Web/CSS/:focus-visible
- `aria-hidden`: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Attributes/aria-hidden
- The `dialog` element: https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/dialog
- `autocomplete` attribute: https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Attributes/autocomplete
- axe-core: https://github.com/dequelabs/axe-core
- axe browser extension: https://www.deque.com/axe/core-tools/
- `@axe-core/playwright`: https://github.com/dequelabs/axe-core-playwright
