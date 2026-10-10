# Toolchain reference and CI recipes

Supporting detail for `documentation-as-code`. Commands and keys change between tool majors;
check each tool's own documentation URL before adopting a snippet.

## MkDocs Material

Common additional options in `mkdocs.yml`:

```yaml
site_name: Acme
site_url: https://acme.github.io/docs/
site_dir: site
repo_url: https://github.com/acme/project
repo_name: acme/project
copyright: Copyright &copy; 2026 Acme

theme:
  name: material
  palette: false          # disable the palette toggle entirely
  icon:
    repo: fontawesome/brands/github

extra:
  social:
    - icon: fontawesome/brands/github
      link: https://github.com/acme/project

plugins:
  - search:
      lang: en            # newer versions use a `lang` key; older ones use `languages`
  - minify:
      minify_html: true

extra_css:
  - css/extra.css
```

The search plugin's language key was renamed in Material 9; check the Material docs for your
installed version. Set it to the index language of your content, not `en`, or search silently
stops matching for other languages.

Admonitions in Markdown:

```markdown
!!! warning "Rolling deploys"

    The `v2` endpoint is unavailable for roughly 30 seconds during a
    rolling deploy. Retry with backoff.

!!! note ""
    A blank title renders as a bare note.
```

The four-level admonition styles are `note`, `abstract`, `info`, `tip`, `success`, `warning`,
`failure`, `danger`, `example`, `quote`.

Directory URLs:

```yaml
use_directory_urls: true    # default; /guide/quickstart/ instead of /guide/quickstart.html
```

## Docusaurus: versioning mechanics

```bash
npm run docusaurus docs:version 1.4.0
```

This copies the current `docs/` into `versioned_docs/version-1.4.0/` and `versioned_sidebars/version-1.4.0-sidebars.json`, adds the version to `versions.json`, and updates `docusaurus.config.js`. The current `docs/` becomes the "Next" (unreleased) version.

Supported commands, provided by `@docusaurus/plugin-content-docs`:

```bash
npm run docusaurus docs:version 1.5.0          # snapshot
npm run docusaurus docs:version:list           # list
npm run docusaurus docs:version:current        # print the current version label
npm run docusaurus docs:version:switch 1.4.0   # copy an old version back into docs/
npm run docusaurus docs:version:prune          # delete versions not in versions.json
```

Front matter fields available on a doc page:

```yaml
---
id: orders            # stable document id; changing it breaks links
title: Orders API
sidebar_label: Orders
sidebar_position: 2
slug: /reference/orders
description: Create and retrieve orders.
tags: [api, orders]
---
```

## Sphinx with MyST

`requirements-docs.txt`:

```text
sphinx
furo
myst-parser
sphinx-copybutton
```

`docs/conf.py`:

```python
project = "Acme"
extensions = ["myst_parser", "sphinx.ext.autodoc", "sphinx_copybutton"]
source_suffix = {".rst": "restructuredtext", ".md": "markdown"}
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]
html_theme = "furo"
autodoc_typehints = "description"
nitpicky = False
```

Build with strictness:

```bash
sphinx-build -b html docs docs/_build/html -W --keep-going
```

`-W` turns warnings into errors; `--keep-going` reports all of them before failing. Use both.

## CI matrix for a docs build

```yaml
name: docs-ci
on:
  pull_request:
    paths:
      - 'docs/**'
      - 'mkdocs.yml'
      - '.github/workflows/docs.yml'

jobs:
  mkdocs:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install mkdocs-material lychee
      - name: Build strictly
        run: mkdocs build --strict
      - name: Internal link check
        run: lychee --offline --no-progress "site/**/*.html"

  sphinx:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: pip install -r docs/requirements-docs.txt
      - name: Build with warnings as errors
        run: sphinx-build -b html docs docs/_build/html -W --keep-going
```

`lychee --offline` validates links against the built site without network calls, so it does not
flakily fail on third-party hosts. The `--offline` flag and the glob argument style should be
confirmed with `lychee --help` for your installed version.

## Caching builds in CI

```yaml
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
          cache-dependency-path: docs/requirements-docs.txt
      - uses: actions/setup-node@v4
        with:
          node-version: '20'
          cache: npm
          cache-dependency-path: package-lock.json
```

## Freshness check

A small script that fails when a doc page has not been touched in a year, or when a documented
API path has no matching source:

```python
#!/usr/bin/env python3
"""Fail if a doc page's mtime is older than the source file it documents."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PAIRS = [
    ("docs/reference/orders.md", "src/api/orders.py"),
    ("docs/reference/webhooks.md", "src/api/webhooks.py"),
]
MAX_AGE = timedelta(days=365)

failed = False
for doc_rel, src_rel in PAIRS:
    doc, src = Path(doc_rel), Path(src_rel)
    if not doc.exists():
        print(f"MISSING doc: {doc_rel}")
        failed = True
        continue
    doc_mtime = datetime.fromtimestamp(doc.stat().st_mtime, tz=timezone.utc)
    src_mtime = datetime.fromtimestamp(src.stat().st_mtime, tz=timezone.utc)
    if src_mtime > doc_mtime:
        print(f"STALE doc: {doc_rel} predates {src_rel}")
        failed = True

sys.exit(1 if failed else 0)
```

Adapting it to your repository's source-to-doc mapping is the point; the timestamps are a
proxy for "did anyone update this when the behavior changed".

## Verify before relying on this

- MkDocs Material configuration reference: https://squidfunk.github.io/mkdocs-material/setup/
- Docusaurus configuration and versioned docs: https://docusaurus.io/docs/api/docusaurus-config
- Sphinx configuration and command line: https://www.sphinx-doc.org/en/master/usage/configuration.html
- VitePress config: https://vitepress.dev/reference/site-config
- Hugo configuration: https://gohugo.io/configuration/
- GitHub Pages deployment actions: https://docs.github.com/en/pages/getting-started-with-github-pages/customizing-github-pages/using-custom-workflows-with-github-pages