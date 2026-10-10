# Diagnosing and reducing bundle size

## Build a size budget

A budget without enforcement is a wish. Enforce it in CI by comparing the built output
against a committed baseline and failing when it grows beyond a threshold.

```bash
#!/usr/bin/env bash
# scripts/check-bundle-size.sh
set -euo pipefail

BUDGET_KB="${BUDGET_KB:-250}"

# Compressed size of the largest initial JS chunk.
largest_kb=$(find dist/assets -name '*.js' -printf '%s %p\n' 2>/dev/null |
  sort -rn | head -1 | cut -f1 | awk '{printf "%d", $1/1024}')

if [ -z "${largest_kb:-}" ]; then
  echo "no built assets found; run the build first"
  exit 2
fi

echo "largest initial chunk: ${largest_kb} KiB (budget ${BUDGET_KB} KiB)"
if [ "${largest_kb}" -gt "${BUDGET_KB}" ]; then
  echo "bundle budget exceeded"
  exit 1
fi
```

Adjust the `find` and `sort` incantation for your platform; the contract is: compute the
compressed size of the largest initial chunk, compare against a number, exit non-zero on
failure. Most bundlers also print a size summary at the end of a build, which is enough
for a first pass without a script.

## Read the numbers correctly

| Number | Meaning | Misleads when |
| --- | --- | --- |
| Uncompressed file size | Parse and execute cost | Compared against a transfer budget |
| Compressed size | Transfer cost | Assumed to equal parse cost |
| Per-route initial payload | What one page view costs | Aggregated across routes |
| Lazy chunk size | Cost when that feature is used | Counted against initial load |

Most users never visit every route. The number that matters is the initial payload for
the route with the most traffic, not the sum of everything.

## Attribution workflow

1. Get the composition chart. Most tools can produce one, and guessing which module is
   large is much slower than reading the chart.
2. Sort by compressed size, descending.
3. For each large entry ask: is it in the initial chunk, or lazy, and does the majority
   of traffic trigger it?
4. Classify: your code, a dependency you barely use, a polyfill, a locale file, an asset
   inlined by mistake, duplicated versions.
5. Fix the highest-value item. Re-measure. Stop when the budget is met, not when the
   bundle is small.

## Common findings and their fixes

### A date library for one call

Symptom: a large polyfill in the initial chunk for a single formatted date.

```typescript
// Before: whole library.
import moment from "moment";
const label = moment(iso).format("YYYY-MM-DD");

// After: platform API.
const label = new Intl.DateTimeFormat("en-CA", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
}).format(new Date(iso));
```

`Intl` is built in, already localised, and much smaller than a full date library.

### One helper from a large library

Symptom: importing a lodash-style library for a single function.

Check whether the library ships a per-function entry point before switching. If it does,
import it directly. If it does not, a ten-line local helper is usually smaller and has no
supply chain surface.

### Locale files

Symptom: dozens of locale files in the bundle.

```typescript
// Load only the locale actually used, at runtime.
const messages = (await import(`./locales/${locale}.json`)).default;
```

Guard the dynamic import with a known set of locales, or a bundler that cannot enumerate
will either warn or include everything.

### Icons

Symptom: thousands of SVG nodes.

Use an icon package designed to tree-shake, importing each icon by name from a per-icon
entry point, or subset the set at build time from the ones actually referenced.

### Duplicated versions

Symptom: two copies of the same library in the output.

```bash
npm ls <package>
pnpm why <package>
```

Two copies usually means a major or minor range mismatch between your code and a
dependency. Align the versions, or alias deliberately and test.

### Everything is in the initial chunk

Symptom: one huge entry file with every route eagerly imported.

Route-level dynamic imports are the fix, and they should be driven by the router, not
hand-rolled per component.

## Verifying an optimisation

Every change to bundle shape needs two checks:

1. The size moved in the right direction, measured compressed, for the route you
   intended.
2. Nothing broke. Especially: side effects that were previously preserved, code that
   relied on a module being loaded eagerly, and error monitoring that depended on a
   function name.

For the second check, run the built output, not the dev server. Dev does not reproduce
tree shaking, so an optimisation that works in dev may do nothing in production.

## Long-term practices

- Budgets enforced in CI with a deliberate allowance for growth, reviewed when exceeded.
- A dependency-size review step when adding a large dependency.
- Periodic review of the largest five modules, so slow growth is visible before it is a
  complaint.
- Track initial payload per route as a metric, so a regression is attributable.
- Prefer platform APIs to libraries where the platform has grown the capability. The
  reverse of the usual instinct, and it is usually a win on both size and maintenance.