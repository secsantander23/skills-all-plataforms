# Build tooling notes for bundle and load performance

Commands and options differ between bundlers and change between major versions. Verify
against your own `--help` output. What follows is the shape of the workflow, not a
guaranteed CLI surface.

## 1. Build for production

```bash
npm run build
```

Only a production build is meaningful. Development builds ship unminified code, extra
checks, and no tree shaking.

## 2. Read the build output

Most bundlers print per-chunk sizes and, with the right flag, a warning when a chunk
exceeds a threshold.

```bash
# Vite (Rollup-based) - prints chunk table; --manifest writes stats.json
npx vite build
```

```bash
# Webpack - JSON stats output for external tooling
npx webpack --profile --json=stats.json
```

## 3. Visualize the module graph

```bash
# Vite plugin that serves an interactive treemap in the browser
npx vite-bundle-visualizer

# Webpack: serve the analyzer UI against your stats file
npx webpack-bundle-analyzer stats.json
```

Both produce a treemap where a large block usually means one of:

- a date library with all locales and all time zones included
- a charting or editor library pulled in for one page
- an icon package imported as a namespace instead of per-icon
- a moment of duplicated library versions from conflicting peer ranges

## 4. Trace a symbol back to a file

```bash
# Requires source maps in the build; open the generated HTML report
npx source-map-explorer dist/assets/*.js
```

Useful when you know the package but not the entry point that pulled it in.

## 5. Check a dependency before adding it

Bundlephobia (https://bundlephobia.com) reports the minified and gzipped size of an npm
package and its dependencies. It is a screening tool, not a guarantee: tree shaking in
your build may drop most of it.

## Fixing what you find

| Finding | Fix |
| --- | --- |
| Whole icon set bundled | Import the individual icon component |
| Date library with all locales | Use a modular alternative, or import the specific locale |
| Library imported in a shared component | Move it behind a dynamic `import()` in the route that needs it |
| Large polyfill bundle | Check whether older-browser targets in your browserslist config are still needed |
| Duplicate library versions | Align peer dependency ranges, or add an alias/resolve override in the bundler config |
| Moment of source maps inline | Remove the `source-map` loader's inline output from the production build |

## Load-order checks

Use the browser's network panel with throttling enabled, on a production build:

1. Record a cold load. Note when the HTML, the critical CSS, and the entry chunk
   arrive.
2. Look for requests that start late because they were queued behind other requests
   (the classic waterfall).
3. Confirm the LCP image is not lazy-loaded and is requested early.
4. Confirm route chunks for other pages are not requested.

## Budgets you can enforce

Rather than a specific kilobyte number (which depends on your product), set a budget
you can defend and fail the build when it is exceeded:

- A maximum entry-chunk size per route.
- A maximum total transfer size for the first view.
- A maximum count of round trips before first paint.

Enforce it in whatever check already runs in CI, using the bundler's JSON output
(`stats.json` for webpack, the Vite manifest) as the input.

## Verify before relying on this

Bundler flags, plugin package names, and analyzer commands change across major
versions. Check your bundler's official documentation and `npx <tool> --help` before
adding anything to a script.
