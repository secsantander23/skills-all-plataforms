---
name: Web Performance Core
description: Covers Core Web Vitals including LCP, INP, and CLS with their definitions and improvement strategies, TTFB and server response time, image and font optimization, code splitting, preloading and prefetching, CDN and browser caching layers, performance budgets, and measurement with Lighthouse, CrUX, and real-user monitoring. Use when the user asks about page speed, Core Web Vitals, LCP, CLS, INP, Lighthouse scores, performance budgets, or how to make a site load and respond faster.
metadata:
  opencode/autoinvoke: true
---

# Web Performance Core

Performance is not a score; it is four measurable things a user experiences: how quickly
something appears (**LCP**), how responsive the page is when they touch it (**INP**), how
much the layout jumps around (**CLS**), and how long the server takes to answer at all
(**TTFB**). Everything else is a diagnostic on the way to improving one of them.

Field data, aggregated at the **75th percentile** of real page loads, is the ground truth. Lab
tools are for finding causes, not for grading yourself.

## When to use this

- Diagnosing a slow page and deciding what to fix first.
- Working on LCP, INP, or CLS specifically.
- Setting performance budgets and enforcing them in CI.
- Setting up Lighthouse, CrUX, or real-user monitoring.
- Choosing image formats, font strategies, caching, or code splitting.

## Quick start

Measure in production with real users first:

```bash
npm install web-vitals
```

```javascript
import { onCLS, onINP, onLCP } from 'web-vitals';

function sendToAnalytics(metric) {
  navigator.sendBeacon(
    '/rum',
    JSON.stringify({
      name: metric.name,
      value: metric.value,
      rating: metric.rating,
      id: metric.id,
      navigationType: metric.navigationType,
      page: location.pathname,
    })
  );
}

onCLS(sendToAnalytics);
onINP(sendToAnalytics);
onLCP(sendToAnalytics);
```

The `web-vitals` library and its `onCLS`, `onINP`, `onLCP` exports are real. Check the current
version's documentation for the exact export names before shipping, because this library has
changed its API across major versions.

For a local lab check:

```bash
npx lighthouse https://example.com --view
```

`lighthouse` and its `--view` flag are real. Run it against a production build over a real
network when possible; a dev build measures the bundler as much as the page.

## Core Web Vitals

Official thresholds, at the 75th percentile of page loads:

| Metric | What it measures | Good | Needs improvement | Poor |
| --- | --- | --- | --- | --- |
| LCP | Render time of the largest visible element | <= 2.5 s | 2.5 - 4.0 s | > 4.0 s |
| INP | Latency of interactions, measured to the next painted frame | <= 200 ms | 200 - 500 ms | > 500 ms |
| CLS | Largest unexpected layout shift | <= 0.1 | 0.1 - 0.25 | > 0.25 |

Both desktop and mobile are reported separately, and both must be good. Do not average them.

### LCP: Largest Contentful Paint

LCP is the paint time of the largest image, text block, or poster visible in the viewport, and
it stops recording at the first user interaction. "Largest" is by rendered size, and it is
re-evaluated as content loads, so a late-loading hero image can *displace* an earlier LCP
candidate.

LCP is usually a text block on most pages and often the hero image on content pages. Diagnose
before optimizing: read the LCP element out of the field data rather than guessing.

LCP time is composed of:

```text
LCP ≈ TTFB + resource load delay + resource load duration + render delay
```

Attacking each part:

| Cause | Fix |
| --- | --- |
| High TTFB | Faster origin, caching, edge rendering, connection reuse |
| Resource load delay | The request started too late: preload or discover it earlier in the HTML |
| Long load duration | Smaller and better-formatted asset, better CDN, better cache headers |
| Render delay | The browser could not paint: main-thread work, font blocking, client-side rendering |

Concrete measures:

- Server-render the LCP content. A client-rendered app paints a spinner first.
- Put the LCP image in the initial HTML with `fetchpriority="high"` so the preload scanner finds
  it before the parser reaches it.
- Do not lazy-load the LCP element. `loading="lazy"` on the hero is a self-inflicted delay.
- Inline critical CSS so the first paint is not waiting on a stylesheet.
- Preload fonts that the LCP text uses, and set `font-display: swap` or `optional` so text is
  not invisible while the font loads.

```html
<img
  src="/hero-1600.avif"
  srcset="/hero-800.avif 800w, /hero-1600.avif 1600w"
  sizes="100vw"
  width="1600"
  height="900"
  fetchpriority="high"
  alt="Dashboard showing monthly revenue"
/>
```

### INP: Interaction to Next Paint

INP measures the full latency of a user interaction: from the moment the input occurs until the
browser has painted the frame showing its result. It covers clicks, taps, key presses, and
pointer interactions.

INP is reported as a high percentile of interactions (approximately the 98th) rather than an
average, because users remember the worst interaction. A page with a fast median and one
400 ms handler has a bad INP.

Break INP into three phases:

```text
INP = input delay + processing time + presentation delay
```

| Phase | Meaning | Usual causes |
| --- | --- | --- |
| Input delay | Before your handler runs | Long main-thread tasks, busy main thread, hydration work |
| Processing time | Time inside your handler | Heavy synchronous work in the handler |
| Presentation delay | After the handler, before the frame | Layout, paint, and the size of the DOM being updated |

Fixes, in order of impact:

1. **Give the main thread back.** Break up long tasks with yields, move computation to a Web
   Worker, and stop doing work during hydration that can happen later.
2. **Make handlers do less before the first update.** Render a pending state synchronously;
   defer expensive work with `requestAnimationFrame` or after the interaction's paint.
3. **Keep event handlers off the critical path for validation.** Validate optimistically, send
   the request, correct on failure.
4. **Reduce the DOM you re-render.** A single element update is cheap; re-rendering a large list
   synchronously is not.
5. **Remove long tasks that run periodically.** Polling and animation loops on the main thread
   collide with user input.

Measure which phase dominates before optimizing:

```javascript
const observer = new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) {
    // PerformanceEventTiming exposes startTime, processingStart, processingEnd, duration
    const { startTime, processingStart, processingEnd, duration } = entry;
    console.log({
      inputDelay: processingStart - startTime,       // before your handler ran
      processing: processingEnd - processingStart,  // inside your handler
      presentation: duration - processingEnd,       // handler done, frame not yet painted
    });
  }
});
observer.observe({ type: 'event', durationThreshold: 100, buffered: true });
```

`processingEnd` is the real `PerformanceEventTiming` property for the end of the event handler.
The three phases above are an approximation: presentation delay as computed here stops at the
end of `duration`, which is the next paint, so it already includes the rendering work. When you
need to know exactly what the browser did in that window, take a performance recording rather
than inferring it from timing fields.

### CLS: Cumulative Layout Shift

CLS sums the scores of every unexpected layout shift in a page's session. A shift's score is:

```text
impact fraction x distance fraction
```

- **Impact fraction**: the area of the viewport that moved, as a fraction of the viewport.
- **Distance fraction**: how far the moved elements travelled, as a fraction of the viewport's
  largest dimension.

A banner that slides down 200 px pushing the whole page is scored by both terms, which is why
small-looking movements can add up.

Fixes:

- Reserve space for anything that loads late: `width` and `height` on images and iframes,
  `aspect-ratio` for media, and a fixed or minimum height on ad slots and embeds.
- Render dynamic content above existing content, or in a container whose height you already
  know.
- Avoid inserting content above the current scroll position after load.
- Use font-display strategies that do not swap to a metrically different font, or apply
  `size-adjust`, `ascent-override`, and `descent-override` so the fallback occupies the same
  space as the web font.
- Reserve space for consent banners and notification bars rather than overlaying them late.

```css
.card img {
  width: 100%;
  aspect-ratio: 16 / 9;   /* reserves space before the image arrives */
}
```

```css
@font-face {
  font-family: 'Inter Fallback';
  src: local('Arial');
  size-adjust: 107%;
  ascent-override: 90%;
}
```

Transitions and transforms applied after a user interaction are excluded from CLS, which is
why an animated reveal triggered by a click does not count.

### TTFB: Time to First Byte

TTFB is the time from the navigation request to the first byte of the response body. It is a
**diagnostic**, not a Core Web Vital, but it is the ceiling on LCP.

TTFB decomposes into:

```text
redirect time + DNS + TCP + TLS + connection queueing + server processing
```

Fixes, in order:

1. **Stop redirecting.** Every redirect is a full round trip. Serve the final URL directly, or
   reduce redirect chains to one hop.
2. **Reuse connections.** HTTP/2 and HTTP/3 keep connections warm; make sure the server sends
   keep-alive and does not close idle connections too aggressively.
3. **Cache at the edge.** A CDN-cached response can be served without touching the origin.
4. **Make the server do less.** Slow database queries and synchronous calls to other services
   dominate most high-TTFB origins.
5. **Move work off the request path.** Render at the edge, or stream a shell while the rest is
   prepared.

## Images

The largest bytes on most pages. The checklist:

- Serve modern formats (AVIF, WebP) with a fallback; check real encoding cost for your images.
- Size correctly with `srcset` and `sizes`. Serving a 2000 px asset to a 400 px slot wastes
  bytes and decode time.
- Always set `width` and `height`, or `aspect-ratio`, so the slot is reserved and CLS stays at
  zero.
- `loading="lazy"` for below-the-fold images only. Never for the LCP element.
- `fetchpriority="high"` on the LCP image; `fetchpriority="low"` on below-the-fold hero
  carousels.
- `decoding="async"` to keep decode off the critical path.
- Use SVG for icons and vector art; it scales and is small.
- Do not serve animated formats for static content.

```html
<picture>
  <source srcset="/hero.avif" type="image/avif" />
  <source srcset="/hero.webp" type="image/webp" />
  <img src="/hero.jpg" width="1600" height="900" fetchpriority="high" alt="..." />
</picture>
```

## Fonts

Fonts block text rendering and cause layout shift when they swap in.

```html
<link rel="preload" href="/inter-400.woff2" as="font" type="font/woff2" crossorigin />
```

```css
@font-face {
  font-family: 'Inter';
  src: url('/inter-400.woff2') format('woff2');
  font-weight: 400;
  font-display: swap;   /* or optional, or fallback */
  unicode-range: U+0000-00FF;   /* subset by range */
}
```

| `font-display` | Behaviour | Use when |
| --- | --- | --- |
| `block` | Text invisible for up to 3 s | Rarely |
| `swap` | Fallback shown immediately, swap when the font arrives | Most sites; risks CLS |
| `optional` | Font used only if it arrives very fast; otherwise the fallback stays | Best for LCP and CLS, at the cost of not always using your font |
| `fallback` | Short block, then fallback, then swap | Middle ground |

Additional measures: subset the font to the ranges you need, serve `woff2`, preload only the
one or two faces used above the fold, and consider a variable font to reduce the number of
requests.

## Code splitting and loading strategies

Split by route, not by file size:

```javascript
// Load the heavy dependency only when the feature is used
async function exportReport() {
  const { exportToPdf } = await import('./pdf-export.js');
  return exportToPdf(currentReport);
}
```

```html
<!-- Warm the cache for a likely next navigation, at low priority -->
<link rel="prefetch" href="/reports" />
```

```html
<!-- Speculation rules: prerender or prefetch URLs as the user navigates -->
<script type="speculationrules" id="speculation">
{
  "prerender": [
    { "egress": "same-origin", "where": { "href_matches": "/reports/*" } },
    { "egress": "same-origin", "where": { "href_matches": "/settings" } }
  ]
}
</script>
```

The Speculation Rules API is real and shipped in Chromium-based browsers, and support elsewhere
varies. Treat it as an enhancement: it must be served over HTTPS, and browsers apply activation
rules before they will act on it, so a fresh site may see no effect. Check current support in
the MDN page before depending on it, and always verify that a prerendered page is still
navigation-safe (authentication state, cart contents) before enabling it broadly.

Third-party scripts deserve their own line in the budget: they are usually the largest
transfer and the largest source of main-thread work, and you do not control their code.

## Caching layers

```text
Browser cache  ->  CDN / edge cache  ->  application cache  ->  origin
```

Layer by layer:

| Layer | For | Directive |
| --- | --- | --- |
| Browser | Static, fingerprinted assets | `Cache-Control: public, max-age=31536000, immutable` |
| CDN | HTML and data, revalidated | `Cache-Control: public, max-age=0, must-revalidate` or `s-maxage` |
| Application | Expensive computed responses | Cache with an explicit TTL and an invalidation strategy |
| Origin | Everything else | Short TTL, HTTP caching |

Rules:

- Fingerprint filenames or query strings (`app.9f2c1b.js`). Never `immutable` an unversioned
  URL, or users stay on an old build until the cache expires.
- HTML should revalidate (`no-cache`), so it stays fresh while keeping 304 responses cheap.
- Use `s-maxage` for shared caches when browser and edge should behave differently.
- `Vary` must list every request header that changes the response, or a cache will serve the
  wrong variant.
- Authenticated responses must not be stored in a shared cache: `Cache-Control: private,
  no-store`.

## Performance budgets

A budget turns "it got slow" into a CI failure. Lighthouse CI supports a budget file:

```json
[
  {
    "path": "/*",
    "timings": [
      { "metric": "largest-contentful-paint", "budget": 2500, "tolerance": 200 },
      { "metric": "total-blocking-time", "budget": 300, "tolerance": 50 }
    ],
    "resourceSizes": [
      { "resourceType": "script", "budget": 300 },
      { "resourceType": "total", "budget": 500 }
    ],
    "resourceCounts": [
      { "resourceType": "third-party", "budget": 10 }
    ]
  }
]
```

Budgets work best as byte and count limits on resources, which are stable across runs, plus a
small number of timing budgets with a tolerance so ordinary variance does not cause flakes.

Enforce them:

```bash
npm install --save-dev @lhci/cli

npx lhci autorun          # builds the config, collects results, compares to the base branch
npx lhci collect --url=https://example.com
npx lhci upload            # store results for trend tracking
```

`@lhci/cli` and the `lhci collect`, `lhci upload`, and `lhci autorun` commands are real. Check
your installed version's documentation for configuration file details.

## Measurement: lab versus field

| | Lab (Lighthouse, WebPageTest) | Field (CrUX, RUM) |
| --- | --- | --- |
| Data | Simulated, one visit, controlled | Real users, many visits, real devices |
| Strength | Reproducible, shows the causes | The metric that actually counts |
| Weakness | Simulation is not reality | Hard to attribute; needs segmenting |

Use lab to find *why* a metric is bad; use field to know *whether* it is bad.

### CrUX (Chrome UX Report)

Aggregated, privacy-preserved field data from real Chrome users, keyed by origin. Available via:

- The CrUX API, which needs an API key, returning percentiles for the core metrics.
- The CrUX Dashboard and BigQuery's public dataset
  (`bigquery-public-data.chrome_ux_report.materialized`), which allows much deeper analysis.

CrUX is Chromium-only. If your audience is not on Chrome, CrUX alone will not represent them,
which is the main reason to run your own RUM.

### Real-user monitoring

Instrument the metrics you cannot get from the server, segmented by page, device class, and
release:

```javascript
const observer = new PerformanceObserver((list) => {
  for (const entry of list.getEntries()) {
    if (entry.name === 'navigation-type' || entry.name === 'next-navigation-type') continue;
    navigator.sendBeacon('/rum', JSON.stringify({
      name: entry.entryType,
      value: entry.duration,
      path: location.pathname,
      release: window.APP_RELEASE,
      device: matchMedia('(pointer: coarse)').matches ? 'mobile' : 'desktop',
    }));
  }
});
observer.observe({ type: 'event', durationThreshold: 40, buffered: true });
```

The point of `buffered: true` is that interactions that happened before the observer was
registered are still delivered, so you do not lose the ones during startup.

Always report a `release` or version identifier. Without it, you cannot tell whether a
regression came from your deploy or from something else.

## Choosing what to fix first

```text
High TTFB?        -> origin and edge work; it caps everything else.
LCP bad?          -> check whether the LCP element is the hero image or the main text.
                    Then attack load delay, then load duration, then render delay.
INP bad?          -> find long tasks; split them; move them to workers.
CLS bad?          -> find elements without reserved space, in the CrUX or RUM attribution.
Everything bad?   -> ship less. Every metric usually improves when the page does.
```

## Checklist

- [ ] Field data exists for LCP, INP, and CLS at p75, split by device.
- [ ] The LCP element is identified from real data, not assumed.
- [ ] LCP content is server-rendered and its image is preloaded with `fetchpriority="high"`.
- [ ] The LCP image is not lazy-loaded.
- [ ] Every image has explicit dimensions or `aspect-ratio`.
- [ ] Fonts are subset, preloaded, and use an explicit `font-display` strategy.
- [ ] Long tasks measured and reduced; heavy work moved to workers.
- [ ] Interaction handlers update the UI before doing anything slow.
- [ ] Routes and heavy features are dynamically imported.
- [ ] Fingerprinted assets served `immutable`; HTML revalidates.
- [ ] Authenticated responses are `private, no-store`.
- [ ] Performance budgets enforced in CI with a tolerance.
- [ ] RUM reports include a release identifier.

## Common mistakes

**Optimizing Lighthouse's score instead of field metrics.** Symptom: the score improves and
users see no change. Lab scores are simulated; the field percentile is the target.

**Lazy-loading the hero image.** Symptom: LCP gets worse after an "optimization". Lazy images
below the fold are correct; the LCP element is above it by definition.

**Setting `immutable` on a non-fingerprinted asset.** Symptom: users pinned to an old build
until the cache TTL expires. Fix: fingerprint first, then set the long TTL.

**Fixing CLS with `visibility: hidden` or a fixed page height.** Symptom: the shift is hidden
but content is invisible or the page does not scroll properly. Reserve space instead.

**Reducing a bundle by moving code to the client that is not requested.** Symptom: LCP and INP
improve on the benchmark page and degrade on real pages. Fix: measure the actual routes.

**Adding an analytics script to improve engagement metrics.** Every tag costs bytes and main
thread time; measure the tag's cost like any other dependency.

**Treating p75 as an average.** Symptom: the report looks fine while one in twenty users has a
terrible time. Segment by device, connection, and page.

**Fixing only the page you tested.** Symptom: the improvement does not show up in CrUX because
80% of traffic is a different route. Fix: aggregate field data by template, not by URL.

## Anti-patterns

- **Unlimited image sizes.** `srcset` with a single candidate is a comment, not an optimization.
- **Client-side rendering the LCP content.** It adds a request, a bundle parse, and a render pass
  before anything appears.
- **Preloading everything.** `preload` competes for bandwidth with the resources you actually
  need first; a wrong preload makes LCP worse.
- **Setting a huge cache TTL to "fix" performance.** It hides the problem and creates the
  staleness problem.
- **Vendor scripts injected at the last minute.** They are added after the budget was set.
- **Blocking on web fonts with `font-display: block`.** Invisible text is worse than a fallback.
- **Measuring only on a fast laptop and fast connection.** Check the p75 for mobile on a slow
  connection, which is where most real users are.
- **A performance budget with no enforcement.** It will be exceeded and then renegotiated.
- **Confusing a perfect lab run with a good user experience.** Ship, measure in the field, and
  iterate.

## References

- Core Web Vitals (web.dev): https://web.dev/articles/vitals
- LCP (web.dev): https://web.dev/articles/lcp
- INP (web.dev): https://web.dev/articles/inp
- CLS (web.dev): https://web.dev/articles/cls
- TTFB (web.dev): https://web.dev/articles/ttfb
- web-vitals library: https://github.com/GoogleChrome/web-vitals
- Chrome UX Report: https://developer.chrome.com/docs/crux
- CrUX API: https://developer.chrome.com/docs/crux/api
- CrUX BigQuery dataset: https://developer.chrome.com/docs/crux/bigquery
- Lighthouse documentation: https://developer.chrome.com/docs/lighthouse/overview
- Lighthouse CI: https://github.com/GoogleChrome/lighthouse-ci
- Performance budgets with Lighthouse CI: https://developer.chrome.com/docs/lighthouse/performance/performance-budgets
- MDN, Performance API: https://developer.mozilla.org/en-US/docs/Web/API/Performance_API
- MDN, `PerformanceObserver`: https://developer.mozilla.org/en-US/docs/Web/API/PerformanceObserver
- MDN, Navigation Timing: https://developer.mozilla.org/en-US/docs/Web/API/Performance_API/Navigation_timing
- MDN, Resource Timing: https://developer.mozilla.org/en-US/docs/Web/API/Performance_API/Resource_timing
- MDN, `font-display`: https://developer.mozilla.org/en-US/docs/Web/CSS/@font-face/font-display
- MDN, Speculation Rules API: https://developer.mozilla.org/en-US/docs/Web/API/Speculation_Rules_API
- MDN, `fetchpriority`: https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/img#fetchpriority
- MDN, HTTP caching: https://developer.mozilla.org/en-US/docs/Web/HTTP/Caching
- RFC 9111, HTTP Caching: https://www.rfc-editor.org/rfc/rfc9111
- web.dev, image performance: https://web.dev/articles/fast#optimize-your-images