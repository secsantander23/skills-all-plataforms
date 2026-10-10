# Keyboard interaction patterns reference

Read `../SKILL.md` first. This file gives the concrete keyboard model for the widgets
that come up most, plus focus management code.

## Tab order rules

- Natural document order is the correct order. If Tab order is wrong, fix the DOM.
- `tabindex="0"` puts an element in the natural tab order at its DOM position.
- `tabindex="-1"` makes an element programmatically focusable but removes it from Tab.
  This is the right value for a dialog container or a heading you jump to.
- `tabindex` values above 0 are a bug. They create a second tab sequence that no one
  maintains, and fail Focus Order (2.4.3).
- Never put a positive `tabindex` on anything, ever.

```css
/* Skip link: available to keyboard, hidden until focused */
.skip-link {
  position: absolute;
  inset-inline-start: 0;
  top: -100%;
  background: var(--color-surface);
  color: var(--color-text);
  padding: var(--space-2) var(--space-3);
  z-index: 100;
}

.skip-link:focus {
  top: 0;
}
```

## Dialog with `<dialog>`

`<dialog>` with `showModal()` gives you, in the platform: the top layer, a focus trap,
inertness of the rest of the page, Escape to close, and a `::backdrop` pseudo-element.
You supply the content, the labelling, and the return-focus behavior.

```ts
export function createConfirmDialog(
  root: HTMLElement,
  onConfirm: () => void,
): { open: () => void } {
  const dialog = root.querySelector('dialog');
  if (!(dialog instanceof HTMLDialogElement)) {
    throw new Error('Confirm dialog markup is missing');
  }

  let restoreTo: HTMLElement | null = null;

  return {
    open() {
      restoreTo = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      dialog.showModal();
      // Move focus to the first focusable element inside the dialog.
      const first = dialog.querySelector<HTMLElement>('button, [href], input, select, textarea');
      first?.focus();
    },
  };

  // Note: attach the close handler separately so it can be removed.
  function onClose() {
    restoreTo?.focus();
    if (dialog.returnValue === 'confirm') onConfirm();
  }
  dialog.addEventListener('close', onClose);
}
```

Do not call `focus()` on the page body to "reset" focus. It does not blur the previous
element in all browsers and leaves the caret in an unexpected place.

## Custom dialog fallback

If you must build a modal without `<dialog>`, you own all of this:

1. Store the previously focused element.
2. Move focus inside on open.
3. Trap Tab and Shift+Tab within the dialog.
4. Close on Escape.
5. Return focus to the stored element on close.
6. Mark the rest of the page `inert`.

```ts
const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function trapFocus(container: HTMLElement): () => void {
  function onKeydown(event: KeyboardEvent): void {
    if (event.key !== 'Tab') return;
    const items = Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
      (el) => el.offsetParent !== null || el === document.activeElement,
    );
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;

    if (event.shiftKey && active === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  }

  container.addEventListener('keydown', onKeydown);
  return () => container.removeEventListener('keydown', onKeydown);
}
```

Prefer a maintained primitive library for this. The code above shows the required
behaviors, not a complete implementation.

## Disclosure (show/hide)

```html
<button type="button" aria-expanded="false" aria-controls="panel" id="trigger">
  Details
</button>
<div id="panel" hidden>
  <p>Body</p>
</div>
```

```ts
const trigger = document.getElementById('trigger');
const panel = document.getElementById('panel');

trigger?.addEventListener('click', () => {
  if (!panel) return;
  const open = panel.hasAttribute('hidden');
  if (open) panel.removeAttribute('hidden');
  else panel.setAttribute('hidden', '');
  trigger.setAttribute('aria-expanded', String(open));
});
```

Do not move focus when toggling. The trigger keeps focus; the content simply appears
below it. `hidden` handles the accessibility tree and the layout.

## Tabs

The ARIA tabs pattern: a `tablist` containing `tab` elements, each controlling a
`tabpanel` via `aria-controls`. Arrow keys move between tabs, and the panel is reachable
by Tab.

```html
<div role="tablist" aria-label="Report sections">
  <button role="tab" id="tab-summary" aria-selected="true" aria-controls="panel-summary" tabindex="0">Summary</button>
  <button role="tab" id="tab-detail" aria-selected="false" aria-controls="panel-detail" tabindex="-1">Detail</button>
</div>
<div role="tabpanel" id="panel-summary" aria-labelledby="tab-summary" tabindex="0">...</div>
<div role="tabpanel" id="panel-detail" aria-labelledby="tab-detail" tabindex="0" hidden>...</div>
```

Roving tabindex: the selected tab has `tabindex="0"`, the rest `-1`, so Tab enters the
tablist once and arrow keys move within it.

If the panels are just static content on one page with anchor links, use a navigation
region with links and `aria-current="page"` instead. Tabs are for switching views, not
for jumping anchors.

## Menu

Use a menu only for a list of **commands**. A list of links is navigation, not a menu
role. Common mistake: `role="menu"` on a dropdown of links, which breaks expected
keyboard behavior and confuses screen readers.

Pattern: `role="menu"` on the container, `role="menuitem"` on items, Arrow keys to move,
Enter or Space to activate, Escape to close and return focus to the trigger,
Tab closes the menu.

## Combobox

The combobox pattern is the most commonly mis-implemented widget. The input keeps
focus at all times; a listbox popup is controlled by it.

```html
<label for="fruit">Fruit</label>
<input
  id="fruit"
  role="combobox"
  aria-expanded="false"
  aria-controls="fruit-listbox"
  aria-autocomplete="list"
  aria-activedescendant="fruit-option-2"
  autocomplete="off"
/>
<ul role="listbox" id="fruit-listbox" hidden>
  <li role="option" id="fruit-option-1" aria-selected="false">Apple</li>
  <li role="option" id="fruit-option-2" aria-selected="true">Apricot</li>
</ul>
```

With `aria-activedescendant`, the DOM focus stays on the input while the visual
highlight moves. This is what screen readers announce. Use it, and keep the referenced
id valid. Some implementations move real DOM focus to the options instead; that requires
`aria-activedescendant` to be absent and changes the keyboard model. Pick one and match
the reference pattern in the ARIA Authoring Practices Guide.

## Drag and drop

WCAG 2.2 adds 2.5.7 Dragging Movements (AA): any operation that uses a dragging motion
must have a single-pointer alternative without dragging. That means move and drop needs
keyboard-operable controls: a "move up", "move down", and "move to" set of controls, or
a form with a position field.

`touch-action: none` on a draggable element removes browser panning and zoom. Prefer
`touch-action: manipulation` and do not disable pinch zoom globally.

## Target size

WCAG 2.2 adds 2.5.8 Target Size (Minimum), AA: pointer targets are at least 24 by 24 CSS
pixels, or they are spaced so that a 24-pixel-diameter circle centered on each target
does not intersect another target's circle or its text.

```css
.iconButton {
  /* The visible icon is 16px; the hit area is 32px */
  inline-size: 2rem;
  block-size: 2rem;
  display: inline-grid;
  place-items: center;
}
```

Inline links inside a paragraph are exempt from the target size requirement, as are
targets where a larger equivalent exists, and cases where size is essential to the
content.

## Keyboard testing checklist

Run this by hand on every significant page:

1. Tab from the top of the document. Is there a skip link? Does order match visual order?
2. Is focus always visible, including on the element that has just scrolled into view?
3. Is the focused element ever hidden behind a sticky header or an overlay?
4. Enter, Space, Escape, and the arrow keys do what the widget pattern says.
5. In every dialog: focus enters, is trapped, Escape closes, focus returns.
6. In every dropdown or menu: focus returns to the trigger on Escape.
7. Nothing is reachable by Tab that is invisible.
8. No positive `tabindex` anywhere in the codebase.

Automate what you can. Playwright and Testing Library can drive keyboard interaction and
assert on `document.activeElement`, which catches regressions in focus return and focus
order even when nobody is testing by hand.

## References

- ARIA Authoring Practices Guide: https://www.w3.org/WAI/ARIA/apg/
- APG keyboard interface guidance: https://www.w3.org/WAI/ARIA/apg/practices/keyboard-interface/
- `<dialog>` on MDN: https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/dialog
- `inert` on MDN: https://developer.mozilla.org/en-US/docs/Web/HTML/Global_attributes/inert
- `:focus-visible` on MDN: https://developer.mozilla.org/en-US/docs/Web/CSS/:focus-visible
- `aria-activedescendant`: https://developer.mozilla.org/en-US/docs/Web/Accessibility/ARIA/Attributes/aria-activedescendant
- WCAG 2.2: https://www.w3.org/TR/WCAG22/
- Playwright keyboard support: https://playwright.dev/docs/input
