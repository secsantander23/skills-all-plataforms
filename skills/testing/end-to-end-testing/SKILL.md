---
name: End to End Testing
description: Covers end-to-end browser testing with Playwright and Cypress, including selector strategy with getByRole and test ids, auto-waiting and the causes of flakiness, authentication and state reuse, page objects and fixtures, parallelism and sharding, CI integration, tracing and screenshot artifacts, and how to keep an E2E suite thin. Use when the user asks about Playwright or Cypress, when browser tests are flaky, when E2E tests are slow in CI, or when designing a browser test suite.
metadata:
  opencode/autoinvoke: true
---

# End to End Testing

End-to-end tests drive a real browser against a running application to prove that the deployed pieces work together. The core mental model is *expensive coverage of critical journeys*. E2E tests are the most expensive tests you own, so they should be the fewest tests you own.

## When to use this

- Proving a critical user journey works end to end: sign up, log in, check out, submit a form, upload a file.
- Verifying deployment wiring: the frontend can reach the backend, the backend can reach the database, feature flags are on.
- Verifying the behavior of a third-party widget you cannot unit test (payment fields, maps, analytics).
- Reproducing a bug that only appears in a real browser (cookie handling, redirects, CORS, layout-driven JS).
- As a smoke test after a deployment.

Not for:

- Business rules. Those belong in unit tests, where they run in milliseconds and give precise failures.
- API contracts. Those belong in integration tests.
- Anything you can assert at a lower layer with a clearer failure message.

## Keep the suite thin

A workable ratio is roughly tens of E2E tests against hundreds of unit tests and tens to hundreds of integration tests. If your E2E suite has more than a few dozen tests, the cost has usually exceeded the value.

Cover at the E2E level only:

- The journeys that generate revenue or that would trigger an incident.
- The wiring between deployable components.
- Third-party embeds and browser-specific behavior.

Everything else belongs lower. A rule of thumb: if a failure would be obvious from any other signal (a unit test, a log line, an alert), do not duplicate it in E2E.

## Quick start with Playwright

```bash
npm init playwright@latest
# or, in an existing project:
npm install --save-dev @playwright/test
npx playwright install
```

```javascript
// e2e/tests/checkout.spec.js
const { test, expect } = require('@playwright/test');

test.describe('checkout', () => {
  test('a signed-in customer can place an order', async ({ page }) => {
    // Arrange - signed in via the storageState project dependency
    await page.goto('/shop/ABC-1');

    await page.getByRole('button', { name: 'Add to cart' }).click();
    await expect(page.getByTestId('cart-count')).toHaveText('1');

    await page.getByRole('button', { name: 'Proceed to checkout' }).click();

    await page.getByLabel('Card number').fill('4242424242424242');
    await page.getByLabel('Expiry').fill('12/30');
    await page.getByLabel('CVC').fill('123');
    await page.getByRole('button', { name: 'Pay' }).click();

    // Assert
    await expect(page).toHaveURL(/\/orders\/ord_[a-z0-9]+$/);
    await expect(page.getByRole('heading', { name: 'Thank you' })).toBeVisible();
  });
});
```

```javascript
// playwright.config.js
const { defineConfig, devices } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './e2e/tests',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 4 : undefined,
  reporter: process.env.CI ? [['html', { open: 'never' }], ['list']] : [['list']],
  use: {
    baseURL: process.env.BASE_URL || 'http://127.0.0.1:3000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run start',
    url: 'http://127.0.0.1:3000/health',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    { name: 'setup', testMatch: /global\.setup\.spec\.js/ },
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'], storageState: 'e2e/.auth/user.json' },
      dependencies: ['setup'],
    },
    {
      name: 'mobile',
      use: { ...devices['Pixel 5'], storageState: 'e2e/.auth/user.json' },
      dependencies: ['setup'],
    },
  ],
});
```

Playwright's configuration surface and the `webServer` options above are stable; confirm current values with `npx playwright test --help` and https://playwright.dev/docs/test-configuration when something looks unfamiliar.

## Quick start with Cypress

```bash
npm install --save-dev cypress
npx cypress open        # interactive, for development
npx cypress run         # headless, for CI
npx cypress run --spec cypress/e2e/checkout.cy.js
```

```javascript
// cypress/e2e/checkout.cy.js
describe('checkout', () => {
  beforeEach(() => {
    cy.session('user', () => {
      cy.visit('/login');
      cy.getByLabel('Email').type('e2e@example.com');
      cy.getByLabel('Password').type(Cypress.env('E2E_PASSWORD'));
      cy.getByRole('button', { name: 'Sign in' }).click();
      cy.url().should('include', '/dashboard');
    });
  });

  it('places an order', () => {
    cy.visit('/shop/ABC-1');
    cy.getByRole('button', { name: 'Add to cart' }).click();
    cy.getByTestId('cart-count').should('have.text', '1');

    cy.getByRole('button', { name: 'Proceed to checkout' }).click();
    cy.getByLabel('Card number').type('4242424242424242');
    cy.getByRole('button', { name: 'Pay' }).click();

    cy.url().should('match', /\/orders\/ord_[a-z0-9]+$/);
    cy.getByRole('heading', { name: 'Thank you' }).should('be.visible');
  });
});
```

`cy.session` caches and restores authentication state across tests within a spec, avoiding a login round trip per test. Verify behavior and options in the Cypress docs for your version: https://docs.cypress.io/api/commands/session

## Selector strategy

Selectors are the single largest source of E2E maintenance. Choose them in this order.

| Selector | Use | Notes |
| --- | --- | --- |
| Role plus accessible name | Default | Matches how assistive technology sees the UI. Tests accessibility at the same time |
| `getByLabel` | Form controls | The label is part of the contract |
| `getByPlaceholder` | Rarely | Placeholders are hints, not labels |
| `getByText` | Static prose | Fragile under copy edits |
| `getByTestId` | Escape hatch | For canvas, or when no role or label exists |
| CSS (class or id) | Last resort | Couples the test to markup structure |
| XPath | Avoid | Slow and unreadable |

```javascript
// Playwright
await page.getByRole('button', { name: 'Save changes' }).click();
await page.getByRole('link', { name: 'Settings' }).first().click();
await page.getByRole('row', { name: /Ada Lovelace/ }).getByRole('cell').nth(1).click();
await page.getByLabel('Email address').fill('ada@example.com');
await page.getByTestId('sparkline').waitFor();

// Cypress (cypress-testing-library adds role and label queries)
cy.getByRole('button', { name: 'Save changes' }).click();
cy.getByTestId('sparkline').should('be.visible');
```

Rules:

- Every interactive element should be reachable by role plus name. If it is not, the UI likely has an accessibility bug; fix the UI and add a test id only as a temporary bridge.
- Test ids should describe the element's purpose (`cart-count`, `invoice-total`), not its location or styling (`div-3`, `blue-box`).
- Never select on generated class names from a utility framework (Tailwind, CSS modules). They change with styling, not behavior.
- Scope locators to a container when a page repeats the same role, using `.filter()`, `.nth()`, or `.within()`.

## Auto-waiting and flakiness

Playwright auto-waits for actionability before click, fill, and check: the element must be attached, visible, stable (not animating), and receive the event. This eliminates most explicit waits. It does not eliminate application-level waits.

Common causes of flakiness, and what to do:

| Cause | Symptom | Fix |
| --- | --- | --- |
| Asserting too early | "expected visible, received hidden" intermittently | Use an auto-retrying assertion, never a fixed sleep |
| Missing test id or role | Locator resolves to nothing | Fix the selector; see the table above |
| Animation or transition | Click lands on a moving element, or is intercepted | Wait for the animation to finish; avoid `force: true` |
| Real network calls in the flow | Timeout under CI load | Stub the boundary with `page.route`, or accept it and set a generous timeout |
| Shared test data | Tests create conflicting records | Create uniquely named data per test |
| Test order dependence | Passes alone, fails in suite | Reset state per test, or declare explicit dependencies |
| Clock and locale drift | Fails at midnight or in another timezone | Freeze the clock, pin the timezone |
| Browser-specific behavior | Fails on one project only | Cover each browser deliberately, not by accident |

```javascript
// Playwright: stub a third-party boundary
await page.route('**/api/recommendations', async (route) => {
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({ items: [] }),
  });
});

// Playwright: freeze time when the UI depends on "today"
await page.clock.install({ time: new Date('2024-03-01T12:00:00Z') });
```

```javascript
// Cypress: intercept and stub
cy.intercept('POST', '/api/checkout', { statusCode: 201, body: { id: 'ord_1' } }).as('checkout');
cy.getByRole('button', { name: 'Pay' }).click();
cy.wait('@checkout').its('request.body.totalCents').should('eq', 3000);
```

Anti-patterns to delete on sight:

- `await page.waitForTimeout(2000)` and `cy.wait(2000)`.
- `force: true` on clicks, to bypass actionability instead of fixing the cause.
- Retrying a whole test to hide a race.
- Looping `for (let i = 0; i < 10; i++) { if (await isReady()) break; }` instead of a real wait condition.

## Authentication and state reuse

Logging in per test is slow and couples every test to the login UI. Log in once and reuse the session.

Playwright: authenticate in a setup project, save the storage state, and reuse it.

```javascript
// e2e/tests/global.setup.spec.js
const fs = require('node:fs');
const path = require('node:path');
const { test: setup, expect } = require('@playwright/test');

const AUTH_FILE = path.join(__dirname, '..', '.auth', 'user.json');

setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill(process.env.E2E_USER);
  await page.getByLabel('Password').fill(process.env.E2E_PASSWORD);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('button', { name: 'Account' })).toBeVisible();
  await page.context().storageState({ path: AUTH_FILE });
});
```

Add `.auth/` and any downloaded artifact directories to `.gitignore`. Add a post-checkout cleanup job that rewrites history if these files were ever committed, because a committed storage state is a committed credential.

Playwright also supports a per-test sign-in through a fixture:

```javascript
const base = require('@playwright/test').test;

const test = base.extend({
  page: async ({ page }, use, testInfo) => {
    await page.goto('/login');
    await page.getByLabel('Email').fill(process.env.E2E_USER);
    await page.getByLabel('Password').fill(process.env.E2E_PASSWORD);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await page.waitForURL('**/dashboard');
    await use(page);
  },
});

module.exports = { test };
```

## Page objects and fixtures

A page object (Playwright) or custom command (Cypress) wraps the selectors for a screen or flow. Use them sparingly.

Use a page object when:

- The same flow appears in several specs, and selector churn would otherwise be repeated.
- The flow has non-trivial sequencing (multi-step checkout) worth naming.

Do not use a page object when:

- It only forwards a selector. That adds a layer without removing duplication.
- It hides the steps from the reader. An E2E test should read like a user story.

```javascript
// e2e/pages/checkout.page.js
const checkout = (page) => ({
  addToCart: (sku) => page.getByTestId(`add-to-cart-${sku}`).click(),
  openCart: () => page.getByRole('button', { name: 'Proceed to checkout' }).click(),
  fillCard: ({ number, expiry, cvc }) => {
    page.getByLabel('Card number').fill(number);
    page.getByLabel('Expiry').fill(expiry);
    page.getByLabel('CVC').fill(cvc);
  },
  pay: () => page.getByRole('button', { name: 'Pay' }).click(),
  waitForConfirmation: () => expect(page).toHaveURL(/\/orders\/ord_[a-z0-9]+$/),
});

module.exports = { checkout };
```

Assertions belong in the spec, not the page object. If assertions move into the object, the test stops reporting what it proved.

Cypress custom commands cover the same ground:

```javascript
// cypress/support/commands.js
Cypress.Commands.add('loginByEmail', (email, password) => {
  cy.session([email, password], () => {
    cy.visit('/login');
    cy.getByLabel('Email').type(email);
    cy.getByLabel('Password').type(password);
    cy.getByRole('button', { name: 'Sign in' }).click();
    cy.url().should('include', '/dashboard');
  });
});
```

## Parallelism and sharding

Both runners execute independent test files in parallel across worker processes. Playwright shards a run across machines.

```bash
# Split across 4 machines
npx playwright test --shard=1/4
npx playwright test --shard=2/4
npx playwright test --shard=3/4
npx playwright test --shard=4/4
```

Sharding assigns test files, so each shard must be independently runnable. A test that depends on a file assigned to a different shard will fail.

```javascript
// playwright.config.js
module.exports = defineConfig({
  fullyParallel: true,
  workers: process.env.CI ? 4 : undefined,
});
```

Caveats:

- Worker count competes with the browser for CPU. More workers than roughly twice the core count usually makes the suite slower, not faster.
- Parallel tests need independent data. Generate unique emails, customer names, or use a per-worker namespace.
- If you must share setup, use `test.describe.serial()` (Playwright) or disable retry-on-failure, and accept that you have coupled tests.

## CI integration

```yaml
# .github/workflows/e2e.yml (structure; adapt actions and secrets to your environment)
name: e2e
on:
  pull_request:

jobs:
  e2e:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        shard: [1, 2, 3, 4]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: npm
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - run: npx playwright test --shard=${{ matrix.shard }}/4
      - uses: actions/upload-artifact@v4
        if: ${{ !cancelled() }}
        with:
          name: playwright-report-${{ matrix.shard }}
          path: playwright-report/
          retention-days: 7
```

Practical guidance:

- Install browsers once and cache the browser directory if your CI platform supports it.
- Set `CI=true` so the config enables retries and disables `forbidOnly` bypass.
- Use secrets from your CI platform's secret store for E2E credentials. Never commit a `.env` with real accounts.
- Keep E2E out of the pull request gate at first; move it to a nightly or pre-merge queue once it is stable enough to trust.
- Time-box the job. A suite that can run forever will eventually do so.

## Debug artifacts

When an E2E test fails in CI, you need to see what the browser saw.

| Artifact | What it gives you | Playwright setting |
| --- | --- | --- |
| Trace | Timeline of actions, network, console, DOM snapshots, per-step screenshots | `use.trace` |
| Screenshot | The page at the moment of failure | `use.screenshot` |
| Video | Continuous recording | `use.video` |
| HTML report | Per-test summary with attachments and error context | `reporter` |

Valid `trace` values: `'off'`, `'on'`, `'retain-on-failure'`, `'on-first-retry'`. Valid `screenshot` values: `'off'`, `'on'`, `'only-on-failure'`. Confirm with the current docs.

```bash
npx playwright show-trace test-results/trace.zip
npx playwright show-report
```

Set up the reporters so artifacts are uploaded even when a job fails or is cancelled (`if: always()` or `!cancelled()` in GitHub Actions).

Capture browser console output and page errors in a fixture so runtime errors appear in the report instead of only in CI logs:

```javascript
test.beforeEach(async ({ page }) => {
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.error('browser console:', msg.text());
  });
  page.on('pageerror', (err) => console.error('page error:', err.message));
});
```

## Checklist

An E2E test is ready when:

- [ ] It covers a journey that a lower-layer test cannot credibly cover.
- [ ] Every element it touches is reachable by role plus name, or by an intentional test id.
- [ ] It contains no fixed sleeps, no `force: true`, and no retry loops.
- [ ] Authentication is reused rather than repeated per test.
- [ ] It creates its own data and cleans up, or is safe to leave behind.
- [ ] It runs identically in CI and locally.
- [ ] Traces and screenshots are captured on failure and uploaded as artifacts.
- [ ] It passes on the first run, repeatedly.

## Common mistakes

- **Assertion immediately after a click.** Symptom: intermittent "element is not visible". Fix: assert with an auto-retrying matcher rather than a bare read.
- **Selecting by CSS class from a utility framework.** Symptom: breaks when someone changes a margin. Fix: role plus name, or a test id.
- **Logging in inside every test.** Symptom: suite time dominated by auth. Fix: setup project plus storage state, or a shared fixture.
- **Sharing a single seeded account across parallel workers.** Symptom: duplicate-key errors, or a test that sees another test's data. Fix: per-worker unique data.
- **Committed `.auth/user.json`.** Symptom: a valid session token in git history. Fix: gitignore it, and scrub history if it landed.
- **`--retries 3` with no artifact capture.** Symptom: a suite that is green but slow and untrustworthy. Fix: capture traces on first retry and track the retry rate as a metric.
- **Asserting on a spinner being hidden.** Symptom: passes before the content arrives. Fix: assert on the content you actually want.

## Anti-patterns

- **An E2E suite that duplicates the unit test suite.** Multiplied cost, multiplied flake, no additional confidence.
- **Page objects with assertions inside.** The test can no longer tell you what it verified.
- **One enormous journey test** covering signup, purchase, upgrade, and cancellation. When it fails, triage takes hours.
- **Running against production.** You have turned your users into test fixtures.
- **Assertions on animation states or exact pixel positions.** They break on every unrelated UI change.
- **Treating a green E2E suite as a release gate while unit coverage is poor.** The gate is measuring the wrong thing.

## References

- Playwright documentation - https://playwright.dev/docs/intro
- Playwright locators - https://playwright.dev/docs/locators
- Playwright auto-waiting - https://playwright.dev/docs/actionability
- Playwright test configuration - https://playwright.dev/docs/test-configuration
- Playwright authentication - https://playwright.dev/docs/auth
- Playwright sharding - https://playwright.dev/docs/test-sharding
- Playwright tracing - https://playwright.dev/docs/trace-viewer
- Playwright debugging and test report - https://playwright.dev/docs/debug
- Cypress documentation - https://docs.cypress.io/app/get-started/why-cypress
- Cypress best practices - https://docs.cypress.io/app/core-concepts/best-practices
- Cypress `cy.session` - https://docs.cypress.io/api/commands/session
- Testing Library guiding principles, queries - https://testing-library.com/docs/queries/about/
- Playwright best practices - https://playwright.dev/docs/best-practices