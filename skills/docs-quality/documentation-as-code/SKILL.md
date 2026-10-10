---
name: Documentation as Code
description: Covers docs-as-code toolchains such as MkDocs Material, Docusaurus, Sphinx, VitePress, Hugo, and Nextra, MDX and custom components, versioning documentation across tools, search configuration, deployment targets, and CI checks for link integrity and build verification. Use when the user asks to set up a documentation site, choose a docs generator, add search or versioning, deploy docs, or make the docs build fail CI on errors.
metadata:
  opencode/autoinvoke: true
---

# Documentation as Code

Docs-as-code means documentation is source: versioned, reviewed, tested, and shipped by the same
pipeline as the code. The pipeline has four stages — build, lint, deploy, and keep in sync —
and each stage fails in a distinctive way. Knowing the failures tells you which stage is
broken.

## When to use this

- Choosing a documentation generator for a project.
- Setting up MkDocs Material, Docusaurus, Sphinx, VitePress, Hugo, or Nextra.
- Making the docs build fail CI on warnings, broken links, or missing assets.
- Versioning docs alongside a versioned API or library.
- Adding search (local index or hosted).
- Deploying docs to GitHub Pages, Netlify, Cloudflare Pages, or Read the Docs.
- Deciding between a `docs/` directory in the repo and a separate docs repository.

## Quick start

MkDocs Material is the fastest path to a good-looking, searchable site for a code repository.

```bash
pip install mkdocs-material

mkdocs new            # creates mkdocs.yml and a docs/ directory
mkdocs serve -a 0.0.0.0   # live-reloading dev server, reachable from a container or VM
mkdocs build --strict    # build and fail on any warning
```

`mkdocs.yml`:

```yaml
site_name: Acme API
site_url: https://acme.github.io/docs/
docs_dir: docs
site_description: HTTP API documentation for Acme.

theme:
  name: material
  features:
    - navigation.sections
    - navigation.top
    - content.code.copy
    - search.suggest
  palette:
    - scheme: default
      primary: indigo
      toggle:
        icon: material/weather-night
        name: Switch to dark mode
    - scheme: slate
      primary: indigo
      toggle:
        icon: material/weather-sunny
        name: Switch to light mode

nav:
  - Home: index.md
  - Getting started:
      - Quickstart: quickstart.md
      - Authentication: auth.md
  - Reference:
      - Orders: reference/orders.md
      - Webhooks: reference/webhooks.md

markdown_extensions:
  - admonition
  - tables
  - toc:
      permalink: true
  - pymdownx.highlight
  - pymdownx.superfences
  - pymdownx.tabbed:
      alternate_style: true

plugins:
  - search:
      languages: [en]
```

`nav` must list every Markdown file in `docs/`, or MkDocs emits a warning — which `--strict`
turns into a build failure. That is a feature: it catches pages you forgot to link.

## Choosing a toolchain

| Tool | Language | Best for | Build command |
| --- | --- | --- | --- |
| MkDocs Material | Markdown | Python projects, small JS repos, quick setup | `mkdocs build --strict` |
| Docusaurus | MDX + React | React projects, versioned docs, rich components | `npm run build` |
| VitePress | Markdown + Vue | Vue projects, minimal setup, fast | `npx vitepress build` |
| Sphinx | reStructuredText or MyST | Python projects, autodoc from docstrings | `sphinx-build -b html docs docs/_build/html -W` |
| Hugo | Markdown, AsciiDoc | Large sites, fast builds | `hugo --minify` |
| Nextra | MDX + Next.js | Docs sharing an app's stack | `next build` |
| Astro Starlight | Markdown + Astro | Modern static docs, accessible by default | `astro build` |

Decision heuristic: pick what your contributors already write in. A Markdown-based generator
lets non-engineers contribute; Sphinx with autodoc is unbeatable when the code is Python and
the API is the documentation.

## Configuration shapes

### Docusaurus

`docusaurus.config.js` (or `.ts`):

```js
// @ts-check
/** @type {import('@docusaurus/types').Config} */
const config = {
  title: 'Acme API',
  tagline: 'Everything about the Acme API',
  favicon: 'img/favicon.ico',
  url: 'https://acme.github.io',
  baseUrl: '/docs/',
  onBrokenLinks: 'throw',
  onBrokenMarkdownLinks: 'throw',
  i18n: {
    defaultLocale: 'en',
    locales: ['en'],
  },
  presets: [
    [
      'classic',
      /** @type {import('@docusaurus/preset-classic').Options} */
      ({
        docs: {
          sidebarPath: './sidebars.js',
          routeBasePath: '/',
        },
        blog: false,
        theme: {},
      }),
    ],
  ],
  themeConfig: {
    navbar: {
      title: 'Acme',
      items: [
        { type: 'docSidebar', sidebarId: 'docs', position: 'left', label: 'Docs' },
      ],
    },
  },
};

export default config;
```

The `onBrokenLinks` and `onBrokenMarkdownLinks` options are real, and their defaults have
changed between major versions. Set them explicitly rather than relying on the default.

```bash
npx create-docusaurus@latest site classic     # scaffold
npm start                                     # dev server
npm run build                                 # production build; fails on broken links
npm run docusaurus docs:version 1.4.0         # snapshot current docs as version 1.4.0
npm run docusaurus docs:version:list          # list existing versions
npm run docusaurus docs:version:prune         # delete stale version directories
```

The `docs:version:*` scripts are generated by the Docusaurus plugin and are the real
interface to versioning.

### VitePress

`docs/.vitepress/config.ts`:

```ts
import { defineConfig } from 'vitepress';

export default defineConfig({
  title: 'Acme API',
  description: 'HTTP API documentation for Acme',
  cleanUrls: true,
  themeConfig: {
    nav: [{ text: 'Guide', link: '/guide/' }, { text: 'Reference', link: '/reference/' }],
    sidebar: [
      {
        text: 'Guide',
        items: [
          { text: 'Introduction', link: '/guide/' },
          { text: 'Quickstart', link: '/guide/quickstart' },
        ],
      },
    ],
    search: { provider: 'local' },
    socialLinks: [{ icon: 'github', link: 'https://github.com/acme/project' }],
  },
});
```

```bash
npx vitepress dev docs      # dev server
npx vitepress build docs    # static output to docs/.vitepress/dist
```

The local search provider is built in; no extra dependency. Its index is built at build time,
which means content behind client-side rendering is not indexed.

### Sphinx

`docs/conf.py`:

```python
project = "Acme"
copyright = "2026, Acme"
author = "Acme"

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",     # Google/NumPy docstring styles
    "sphinx.ext.viewcode",
    "sphinx_copybutton",
]

source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
myst_enable_extensions = ["colon_fence", "deflist", "linkify"]

html_theme = "alabaster"       # bundled; or furo / pydata_sphinx_theme if installed
```

`docs/index.rst`:

```rst
Acme
====

.. toctree::
   :maxdepth: 2

   quickstart
   reference/orders
```

```bash
sphinx-build -b html docs docs/_build/html     # build
sphinx-build -b html docs docs/_build/html -W --keep-going   # -W fails on warnings
```

`-W` is the flag that turns Sphinx warnings into errors. `--keep-going` collects all warnings
first, which is far more useful in CI than failing on the first one.

### Hugo

`hugo.toml`:

```toml
baseURL = 'https://acme.github.io/docs/'
languageCode = 'en-us'
title = 'Acme API'
enableGitInfo = true

[params]
  description = 'HTTP API documentation for Acme'

[markup]
  [markup.highlight]
    codeFences = true
    style = 'github'
  [markup.goldmark.renderer]
    unsafe = true
    [markup.goldmark.renderer.attribute]
      block = true
      title = true
```

```bash
hugo server -D          # development server with drafts
hugo --minify          # production build into public/
```

## MDX and custom components

Docusaurus, Nextra, and Starlight support MDX: Markdown plus JSX, so you can drop a real React
or Vue component into a page.

````markdown
---
title: Orders
sidebar_position: 2
---

# Orders

<StatusBadge status="stable" />

<Tabs groupId="language">
<TabItem value="python" label="Python" default>

```python
from acme import Client
client = Client(api_key=API_KEY)
```

</TabItem>
<TabItem value="javascript" label="Node.js">

```javascript
import { Client } from '@acme/sdk';
const client = new Client({ apiKey: API_KEY });
```

</TabItem>
</Tabs>
````

Docusaurus built-ins include `<Tabs>`, `<TabItem>`, `<Admonition type="note">`, and
`<Details>`. Import your own from a path relative to the MDX file:

```mdx
import Chart from './components/Chart';

<Chart data={latencyByRegion} />
```

Two caveats that bite in CI:

- Any React component you use in MDX is rendered at build time. A component that touches
  `window` breaks the build, even though it works in the browser.
- MDX treats `{` and `<` as JSX delimiters. Escape them when you mean literal text:
  `\{` or `&lbrace;`.

For toolchains without MDX (MkDocs, Sphinx, VitePress), use the templating the tool provides —
MkDocs Material tabs via `pymdownx.tabbed`, VitePress via its markdown-it container syntax —
rather than raw HTML components.

## Versioning documentation

| Tool | Built-in | Commands / approach |
| --- | --- | --- |
| Docusaurus | Yes | `docusaurus docs:version <version>` snapshots `docs/` into `versioned_docs/version-<v>/` and writes `versions.json` |
| MkDocs Material | No | Use `mike`: `mike deploy --push --update-aliases 2.0 latest` |
| Sphinx | No | `sphinxcontrib-versioning`, or manual directory versioning plus a version switcher |
| VitePress | No | Manual: `docs/version-2.0/` plus a version selector in the theme config |
| Hugo | No | `docs/version-2.0/` plus a version menu (`params.versionMenu`) |

Versioning rules:

- Version the docs at the same cadence you version the software, and tag the commit so a
  released version's docs are reproducible.
- Prune versions you no longer support, or the build gets slow and search results get ambiguous.
- Set the default version explicitly rather than relying on "latest"; ambiguity here is why
  people report against the wrong version.
- Write a migration note per version that lists breaking changes, in a page readers can reach
  from any version.

## Search

| Tool | Built-in search | Hosted alternative |
| --- | --- | --- |
| MkDocs Material | Yes, via the `search` plugin | Algolia DocSearch |
| Docusaurus | Local plugin or Algolia | `@docusaurus/theme-search-algolia` |
| VitePress | Yes, `search: { provider: 'local' }` | Algolia DocSearch |
| Sphinx | Yes, client-side index | Algolia DocSearch |
| Hugo | Yes | Algolia DocSearch |

Local search is free, works offline, and has no external dependency; it is also less capable
(no typo tolerance beyond the index, no per-version scoping without extra work). Hosted search
is better but adds an account, a crawler, and a network dependency at read time.

If readers complain that search misses things, check whether the content is inside a component
that is rendered only in the browser. Client-rendered content is invisible to a build-time
index.

## CI: make the docs build break the build

The four checks that matter, in order of value:

1. **Build verification.** The site must build. Use the strict flag: `mkdocs build --strict`,
   `sphinx-build ... -W`, Docusaurus's default `throw` behavior.
2. **Link checking.** Catch links to pages that do not exist and to anchors that moved.

   ```bash
   # lychee is a real fast link checker; --offline skips network checks
   lychee --offline "docs/**/*.md"
   lychee --no-progress "https://acme.github.io/docs/"

   # markdown-link-check is a real npm alternative
   npx markdown-link-check --config .markdown-link-check.json "docs/**/*.md"
   ```

3. **External link liveness.** Schedule it (nightly), not per-PR; flaky third-party hosts will
   otherwise train people to ignore CI.
4. **Freshness.** Fail if a page's last change predates the API it documents. This is the check
   that stops documentation from silently rotting.

```yaml
name: docs
on:
  pull_request:
    paths: ['docs/**', 'mkdocs.yml']
  schedule:
    - cron: '17 3 * * 1'   # Monday 03:17 UTC: external link check

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install mkdocs-material
      - name: Build (strict)
        run: mkdocs build --strict
      - name: Check internal links
        run: |
          pip install lychee
          lychee --offline --no-progress "site/**/*.html"

  deploy:
    needs: build
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    permissions:
      contents: read
      pages: write
      id-token: write
    environment:
      name: github-pages
      url: ${{ steps.deployment.outputs.page_url }}
    steps:
      - uses: actions/configure-pages@v5
      - uses: actions/upload-pages-artifact@v3
        with:
          path: site
      - id: deployment
        uses: actions/deploy-pages@v4
```

The Actions used here — `configure-pages`, `upload-pages-artifact`, `deploy-pages` — are the
real GitHub Pages deployment actions. Pin their versions and verify against the GitHub Pages
deployment documentation.

Deployment alternatives: Netlify and Cloudflare Pages both build from a repository with a build
command and a publish directory; Read the Docs builds Sphinx repositories from its own config.
Any of them can replace the three-action sequence above.

## In-repo vs separate docs repository

| | `docs/` in the product repo | Separate docs repo |
| --- | --- | --- |
| Docs change with the code | Yes, one PR | Two PRs, or a bot-generated sync |
| Contributor friction | Low | Higher; two repos to fork and know about |
| Access control | Tied to the code | Separate, including public while code is private |
| Website-specific structure | Clutters the product repo | Free to be a website |
| Cross-product consistency | Harder | Easier, one style for many products |

Default to in-repo. Choose a separate repository when the docs cover multiple products, must be
public while the code is private, or need an independent team and release cadence.

If you need both: generate the API reference from the code into a directory, and commit that
directory in the docs repo. Hand-written prose lives with the docs repo; generated artifacts
come from the code.

## Checklist

- [ ] The site builds locally with the strict flag before any PR is opened.
- [ ] CI runs the build on every docs change and fails on warnings.
- [ ] Internal link and anchor checking runs in CI.
- [ ] External link checking is scheduled, not per-PR.
- [ ] Search is either built in and indexed, or hosted and verified.
- [ ] Navigation is explicit (`nav` in MkDocs, sidebar in Docusaurus) so no page is orphaned.
- [ ] A default version is pinned and stale versions are pruned.
- [ ] The repository name and top-level link to the docs site are in the main README.
- [ ] Docs deployment is gated on the same review rules as code.

## Common mistakes

**Shipping docs without the strict flag.** Symptom: a page has been unpublished by accident for
a month. `mkdocs build` without `--strict` only warns; `--strict` fails the build.

**Client-side rendering in a statically indexed site.** Symptom: search finds nothing on the
affected pages. Fix: ensure content is in the static HTML the indexer reads, or exclude those
pages from search expectations.

**MDX breaking on literal braces.** Symptom: a page fails to build with a JSX parse error
around `{port}`. Fix: escape as `\{port\}`.

**Sphinx warnings ignored.** Symptom: autodoc references produce hundreds of warnings nobody
reads. Fix: run with `-W` from day one and fix them, or the warning signal becomes worthless.

**Orphaned pages.** Symptom: content exists but nothing links to it, so nobody finds it.
Fix: an explicit nav/sidebar in every toolchain, and the build warning that tells you.

**Docs versioned separately from the software.** Symptom: `2.4` docs describe `2.5` behavior.
Fix: version the docs in the same commit that changes the code, or generate them.

**Deploying `main` unconditionally.** Symptom: a broken `main` publishes a broken site. Fix:
gate deployment on a successful build job.

## Anti-patterns

- **A docs site with no owner.** It decays within a quarter. Express ownership with CODEOWNERS
  on the docs directory and require that team's review.
- **Writing documentation in a wiki separate from the code.** Two sources of truth.
- **Screenshots for anything that could be text.** They rot and break localization.
- **Copy-pasting the same setup snippet into twenty pages.** One snippet, included or linked.
- **Deploying on every PR.** Deploy on merge; preview the build in the PR instead.
- **Adding a hosted search provider without an offline fallback.** Readers on restricted
  networks lose search entirely.
- **A "docs" link in the README pointing at localhost.** Ship the site.

## References

- MkDocs: https://www.mkdocs.org/
- MkDocs Material documentation: https://squidfunk.github.io/mkdocs-material/
- Docusaurus documentation: https://docusaurus.io/docs
- Docusaurus versioning: https://docusaurus.io/docs/versioning
- Docusaurus MDX: https://docusaurus.io/docs/markdown-features
- VitePress documentation: https://vitepress.dev/
- Sphinx documentation: https://www.sphinx-doc.org/en/master/
- Sphinx command-line options (including `-W`): https://www.sphinx-doc.org/en/master/man/sphinx-build.html
- Hugo documentation: https://gohugo.io/documentation/
- Astro Starlight: https://starlight.astro.build/
- Nextra documentation: https://nextra.site/docs
- Antora documentation: https://antora.org/
- lychee link checker: https://github.com/lycheeverse/lychee
- markdown-link-check: https://github.com/tcort/markdown-link-check
- GitHub Pages custom workflows: https://docs.github.com/en/pages/getting-started-with-github-pages/customizing-github-pages/using-custom-workflows-with-github-pages
- Netlify build configuration: https://docs.netlify.com/build/configure-builds/
- Cloudflare Pages builds: https://developers.cloudflare.com/pages/configuration/build-configuration/
- Keep a Changelog: https://keepachangelog.com/en/1.1.0/