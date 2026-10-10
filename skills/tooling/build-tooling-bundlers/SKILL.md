---
name: build-tooling-bundlers
description: Covers JavaScript build tooling and bundlers, including what a bundler does with a module graph, choosing between Vite, webpack, esbuild, and Rollup, development servers and hot module replacement, tree shaking and side effects, code splitting, asset handling, source maps, browser targets, and measuring and reducing bundle size. Use when the user mentions a build tool, a bundle that is too large, a slow dev server, hot reload, chunking, tree shaking, or a sourcemap problem.
metadata:
  opencode/autoinvoke: true
---

# Build Tooling and Bundlers

A bundler turns a graph of modules into a graph the browser can load. Everything else,
dev server, hot reload, minification, source maps, is a variation on resolving, building,
and emitting that graph. Learn the graph model and the tooling choices become obvious.

Verify before relying on this: this area churns quickly. Vite's bundler backend and
option names have changed across major versions, and webpack and Rollup configuration is
large and stable but version-sensitive. Check the tool's own documentation for the version
in your lockfile.

## When to use this

- A production bundle is too large or loads too slowly.
- The dev server is slow to start or slow to reflect changes.
- Hot module replacement does not apply to a file, forcing a full reload.
- A library ships untranspiled syntax and breaks in a consumer.
- You are choosing a bundler for a new project or migrating an existing one.

## What a bundler does

Four phases, in order:

1. **Entry resolution.** Start at entry points, resolve each specifier to a file using
   the resolver algorithm: extensions, `package.json` fields such as `main`, `module`,
   `exports`, and `browser`, plus aliases and conditional exports.
2. **Graph construction.** Walk imports, producing a graph of modules, and record where
   each module came from.
3. **Transform.** Run each module through loaders or plugins: TypeScript to JavaScript,
   JSX to function calls, CSS to JavaScript or a stylesheet, Svelte or Vue templates to
   render functions, and so on.
4. **Emit and optimise.** Concatenate, minify, resolve imports to chunk references,
   rename symbols, extract CSS, copy or inline assets, and write source maps.

Every debugging question about a bundler is a question about one of these phases. A
module that is duplicated in the output is a resolution or dedup problem, not a
minification problem. A style that is missing is a transform order problem.

## Choosing a tool

| Tool | Use it for | Trade-off |
| --- | --- | --- |
| Vite | Modern web applications and libraries, especially with a framework | Development server and production build are separate pipelines, so behaviour can differ between them |
| webpack | Applications needing deep customisation of the build pipeline, or an existing large configuration | Powerful and enormous; slower to start for simple projects |
| esbuild | Fast transforms and bundling, or a plugin doing the heavy lifting | Deliberately narrow; not the full framework a bundler is |
| Rollup | Libraries that ship unbundled or lightly bundled output | Produces clean ESM but has no dev server |
| Parcel | Zero-config builds in small projects | Little control when you outgrow it |

Decision guidance:

- **New application**: Vite, or the framework's default which is usually built on it.
- **Existing webpack project**: do not migrate for its own sake. Migrate when a concrete
  pain, not a benchmark, justifies the risk.
- **Library**: Rollup or the library mode of the application bundler you already use.
  Consumers of a library bring their own bundler, so shipping bundled output is often
  the wrong call.
- **Just need fast transpilation**: esbuild.

Framework-starters matter: if the project is Next.js, Remix, SvelteKit, Nuxt, or Astro,
the framework owns the bundler configuration. Read the framework's build documentation
first; Vite options will be mediated or ignored.

## Development server and hot module replacement

Dev and prod are usually different pipelines. In a Vite-style setup the dev server serves
source modules over native ES modules and lets the browser build the graph, while the
production build does full bundling ahead of time.

That design explains most dev-server complaints:

- Startup is fast because nothing is bundled up front.
- The first page load is slower in development than in production, because the browser
  makes many requests.
- A dependency that ships unbundled code produces a waterfall of requests, which is why
  pre-bundling exists.
- Behaviour that only appears at build time, such as tree shaking and asset URL
  rewriting, is not reproduced in dev.

Hot module replacement updates one module and its importers without reloading the page.
It fails, and falls back to a full reload, when:

- The module has no `import.meta.hot` accept handler.
- The change crosses a boundary the tool considers stateful, such as a change to a
  dependency in the pre-bundled directory.
- The transform plugin does not declare which of its outputs it owns.

If you own the plugin, the fix is usually to make the plugin invalidate the right module
boundary rather than to disable HMR globally.

Do not let a workaround become the norm. A repository where every change full-reloads is
a repository where developers stop relying on the dev server.

## Tree shaking and side effects

Tree shaking removes exports nobody imports. It only works when the bundler can prove a
module has no side effects.

```json
{
  "name": "@acme/ui",
  "version": "1.0.0",
  "type": "module",
  "sideEffects": [
    "./src/polyfills/*.js",
    "./src/register-sw.js",
    "*.css"
  ],
  "exports": {
    ".": {
      "types": "./dist/index.d.ts",
      "import": "./dist/index.js",
      "default": "./dist/index.js"
    }
  },
  "files": ["dist"]
}
```

What makes shaking fail:

| Symptom | Cause |
| --- | --- |
| Whole library ships despite one import | CommonJS output, or a barrel file that the tool cannot split |
| Polyfill runs twice | `sideEffects` claims the file is pure when it is not |
| A CSS file is dropped | `sideEffects` list does not match the emitted path pattern |
| Class methods keep side effects the tool must preserve | A top-level assignment the tool cannot prove pure |

Barrel files are the single most common cause of a large bundle: an index that re-exports
forty modules can defeat shaking when the module system cannot prove each re-export is
side-effect-free. Import from the concrete path when shaking matters, or measure before
refactoring.

`sideEffects` is a promise you make. Getting it wrong breaks applications, not just your
own bundle. Test it: import one symbol in a scratch project and check the output.

## Code splitting

Splitting is a per-route or per-interaction decision, not a number to hit.

```typescript
// Split at the route. The editor's dependencies never load for a reader.
const editorRoute = {
  path: "/edit/:id",
  loader: () => import("./routes/editor.js"),
};

// Split a heavy dependency that only one interaction needs.
async function exportPdf() {
  const { renderDocument } = await import("./lib/pdf.js");
  return renderDocument();
}
```

Rules:

- Split at a user-visible boundary: route, tab, modal. Users experience the boundary.
- Do not split so aggressively that the request count dominates. Each request has cost,
  and a thousand tiny chunks is a slower page.
- Split vendors you expect to change independently from application code, only if they
  are large enough to matter. Verify the numbers.
- Every async chunk the entry HTML references should be preloaded where the tool supports
  it; otherwise the waterfall is real.

```javascript
// vite.config.js
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    sourcemap: true,
    target: "es2022",
    rollupOptions: {
      output: {
        // Long-term caching works when the vendor hash changes less often than app code.
        manualChunks(id) {
          if (id.includes("node_modules/react")) return "react";
          if (id.includes("node_modules")) return "vendor";
          return undefined;
        },
      },
    },
  },
});
```

Vite renamed this option group to `build.rolldownOptions` in its major release that moved
the bundler backend to Rolldown and kept `build.rollupOptions` as a deprecated alias.
Confirm which name your installed version expects before copying the config above; check
the build options reference for your version.

`manualChunks` is a blunt instrument. A function that returns a chunk name for every
module can create circular chunk references and slow the page down. Start with automatic
splitting and only add manual chunks when a measurement shows a problem.

## Assets

Assets are imports with side effects at build time.

```javascript
import logoUrl from "./logo.svg";
import styles from "./styles.css";

document.body.style.background = `url(${logoUrl})`;
```

Rules of thumb:

- Keep assets under a size threshold so they inline, avoiding a request. Vite's
  `build.assetsInlineLimit` defaults to about four kilobytes and can be set to `0` to
  disable inlining.
- Large images belong in a CDN or a modern image format, not in the bundle.
- Hash filenames for long-lived caching, and make sure your server sets immutable caching
  for hashed assets only.
- Keep the favicon and any critical above-the-fold asset out of the lazy path.

## Source maps

| Mode | Use for |
| --- | --- |
| Off | Development, or when you ship source publicly and accept the exposure |
| `true` | A separate `.map` file, with a reference comment in the bundle |
| `"hidden"` | Separate map file with the reference comment suppressed, for an error-monitoring upload |
| `"inline"` | Map embedded in the bundle. Large, and it publishes your source |

Shipping full source maps to production without deciding who can read them is publishing
your source. Use `"hidden"` plus an upload step to your error tracker, which gives you
readable stack traces without a public map.

Debug a missing map by checking the order of operations: transform must run before the
map is generated, and plugins that rewrite code in `generateBundle` or later can invalidate
it.

## Browser targets

A target decides how far the transformer must lower syntax and built-ins.

```javascript
// vite.config.js
import { defineConfig } from "vite";

export default defineConfig({
  build: {
    // Lower to a specific floor you actually need to support.
    target: "es2022",
  },
});
```

Choosing the lowest target you actually need costs bytes; choosing the highest breaks
older browsers. Decide from your analytics, not from a default, and check whether the
transform lowered only syntax or also added polyfills for built-ins. Lowering syntax does
not add a polyfill for, say, `structuredClone`; you still need to provide one.

## Measuring

Measure before optimising. The sizes that matter are transferred bytes and parse time,
not the uncompressed file size.

```bash
# Compressed size of the emitted assets.
gzip -k dist/assets/*.js
ls -lS dist/assets | head

# Or let the bundler report it; most do by default.
```

Then answer four questions:

1. Which module dominates? A build tool that can draw a bundle composition chart is
   worth the setup.
2. Is it in the initial chunk or a lazy chunk? A large lazy chunk that most users load
   is an initial chunk in practice.
3. Is it your code or a dependency? Fixing a 300-kilobyte dependency you barely use is
   higher leverage than shaving your own module.
4. Is the size real to users? Compare raw bytes against compressed bytes.

Common size anti-patterns:

| Anti-pattern | Symptom | Fix |
| --- | --- | --- |
| Barrel file re-exporting everything | One small import pulls a large module | Import from concrete paths, or verify the barrel is shakeable |
| Importing a whole library for one helper | Unused functions ship | Import the specific entry point, if the library provides one |
| A date library for one format call | Large polyfill for one feature | Platform `Intl`, or a smaller library |
| Shipping source maps publicly | Doubles the payload | Use `"hidden"` and upload |
| A monolithic entry with no route split | Everything is initial | Split at routes |
| Duplicated dependencies at different versions | Two copies of the same library | Align versions, or alias deliberately |
| Icons imported as a full set | Thousands of nodes in the bundle | Use an icon package that tree-shakes, or subset it |
| Dev-only code without `import.meta.env` guards | Test tooling ships | Guard with an environment check the bundler can eliminate |

## Checklist

- Entry resolution produces exactly one copy of each module.
- `sideEffects` is declared and tested with a single-import build.
- Splitting happens at user-visible boundaries and every chunk is loaded somewhere.
- Manual chunk configuration exists only because a measurement demanded it.
- Asset hashes are stable across application-only changes.
- Source maps use `"hidden"` plus upload, or are off.
- The browser target comes from real analytics.
- Transferred bytes per route are tracked, not just total bundle size.
- Dev and prod behaviour differences are known and documented for the project.

## Common mistakes

- **A module appears twice in the output.** Cause: two versions installed, or a
  resolution difference between dev and prod. Symptom: duplicated singleton state,
  broken context providers. Fix: align versions, then verify with `npm ls` or
  `pnpm why`.
- **HMR always full-reloads.** Cause: a plugin does not invalidate its module boundary.
  Fix: implement accept handlers, or invalidate explicitly in the plugin.
- **Tree shaking did nothing.** Cause: CommonJS output or a `sideEffects` field missing
  on the package. Fix: ship ESM, declare `sideEffects`.
- **A CSS import vanished from the build.** Cause: `sideEffects` pattern does not match
  the emitted path. Fix: match the pattern to real output paths and re-test.
- **Production breaks on older browsers, dev works.** Cause: dev does not apply the
  target transformation the same way. Symptom: a syntax error only in production. Fix:
  test the built output, not the dev server.
- **Source maps uploaded but stack traces still minified.** Cause: the bundler emitted a
  map but the minifier renames afterwards, or the map was not uploaded for that release.
  Fix: generate maps in the same step as minification and verify against the deployed
  artefact.

## Anti-patterns

- Optimising total bundle size when only the first route matters.
- Manual chunk rules written from intuition and never re-measured.
- Publishing full source maps because debugging is easier.
- Migrating a working build for benchmark numbers rather than a felt problem.
- Keeping a bundler configuration nobody in the team can explain.
- Trusting a `sideEffects` field you have not tested.

## References

- Vite documentation: https://vite.dev/guide/
- Vite build options: https://vite.dev/config/build-options
- webpack concepts: https://webpack.js.org/concepts/
- Rollup documentation: https://rollupjs.org/introduction/
- esbuild documentation: https://esbuild.github.io/
- Node.js package `exports` field: https://nodejs.org/api/packages.html#exports