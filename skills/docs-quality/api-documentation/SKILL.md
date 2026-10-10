---
name: API Documentation
description: Covers authoring and maintaining OpenAPI and Swagger specifications, documenting endpoints accurately with parameters, request bodies, responses, and errors, writing examples that run, documenting authentication, versioning and changelogs, deprecation notices including the Sunset header, keeping specs in sync with code, and avoiding common documentation anti-patterns. Use when the user asks how to write an OpenAPI spec, document a REST endpoint, set up spec linting, publish API reference docs, or fix a specification that does not match the implementation.
metadata:
  opencode/autoinvoke: true
---

# API Documentation

An API contract is executable documentation. Written by hand it drifts from the code and gets
ignored; generated from the code it captures the shape but not the semantics. The goal is a
spec that both reviewers and clients can trust, so it must be validated in CI and compared
against the running service.

## When to use this

- Writing or reviewing an OpenAPI/Swagger specification.
- Documenting a new endpoint, especially its errors and edge cases.
- Choosing a documentation tool (Swagger UI, Redoc, Scalar, Stoplight Elements).
- Setting up spec linting, bundling, or breaking-change detection in CI.
- Publishing deprecation notices and a changelog for an API.
- Debugging "the docs are wrong" — usually a hand-maintained spec that has drifted.

## Quick start

A minimal, valid OpenAPI 3.1 document:

```yaml
openapi: 3.1.0
info:
  title: Orders API
  version: 1.4.0
  summary: Create and manage customer orders.
  license:
    name: Apache-2.0
    identifier: Apache-2.0
  contact:
    url: https://example.com/support
servers:
  - url: https://api.example.com/v1
    description: Production
paths:
  /orders/{orderId}:
    get:
      operationId: getOrder
      summary: Retrieve one order
      parameters:
        - name: orderId
          in: path
          required: true
          schema:
            type: string
            format: uuid
      responses:
        '200':
          description: The order.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Order'
        '404':
          description: No order with that ID exists, or the caller cannot see it.
        default:
          description: Unexpected error.
components:
  schemas:
    Order:
      type: object
      required: [id, status, total]
      properties:
        id:
          type: string
          format: uuid
        status:
          type: string
          enum: [pending, paid, shipped, cancelled]
        total:
          type: object
          required: [amount, currency]
          properties:
            amount:
              type: string
              description: Decimal amount as a string.
              pattern: '^[0-9]+\.[0-9]{2}$'
            currency:
              type: string
              enum: [USD, EUR, GBP]
```

Validate it locally:

```bash
npx @redocly/cli lint openapi.yaml
npx @redocly/cli bundle openapi.yaml -o openapi.bundled.yaml
```

Redocly's CLI package and the `lint` / `bundle` commands are real. If you use a different
tool, check its current CLI surface with `--help` rather than assuming these names carry over.

## Documenting endpoints accurately

### operationId

Every operation needs a stable `operationId`. It becomes the generated client method name, so
it is part of your public API surface. Renaming it is a breaking change for every consumer that
generated a client. Pick it once, keep it, and never reuse a retired name.

### Parameters

Each parameter object needs `name`, `in`, and `schema`. Add `required: true` for path parameters
(always) and for any query or header parameter that has no sensible default.

```yaml
      parameters:
        - name: limit
          in: query
          required: false
          description: Maximum number of orders to return.
          schema:
            type: integer
            minimum: 1
            maximum: 100
            default: 20
        - name: cursor
          in: query
          required: false
          description: Opaque cursor from a previous response's `next_cursor`.
          schema:
            type: string
```

Document constraints in the schema (`minimum`, `maximum`, `enum`, `pattern`, `default`) rather
than only in prose. Constraints in the schema are machine-readable; constraints in prose are
not.

### Request bodies

```yaml
      requestBody:
        required: true
        description: The order to create. Idempotency is provided by `Idempotency-Key`.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/CreateOrderRequest'
            examples:
              minimal:
                summary: Smallest valid request
                value:
                  customerId: 8c2f1e0a-2b8c-4b5a-9f3d-1a2b3c4d5e6f
                  items:
                    - sku: WIDGET-1
                      quantity: 1
```

One `example` per field is noise. One or two named `examples` at the media-type level showing
realistic payloads teach the shape and the required fields at once.

### Responses

Document the failure responses. They are the part readers cannot guess.

```yaml
      responses:
        '201':
          description: Created.
          headers:
            Location:
              description: URL of the created order.
              schema:
                type: string
                format: uri
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Order'
        '400':
          description: The request body failed schema validation.
        '409':
          description: An order with this `Idempotency-Key` already exists.
        '422':
          description: The body is well-formed but semantically invalid (unknown SKU).
```

Rules:

- Every operation has at least one documented response. A missing response means the reader has
  to guess, and guessing produces support tickets.
- Use `default` for the catch-all error shape, and reference it from `components/responses` so
  the error body is defined once.
- Status codes in a spec are strings (`'200'`), because YAML would read `200` as an integer.
  Both are technically accepted, but be consistent.
- Distinguish carefully between `400`, `404`, and `409`. A `404` that is actually an
  authorization failure leaks existence; say so in the description if you do it.

### Descriptions that add information

```yaml
# Useless
        - name: limit
          description: The limit.

# Useful
        - name: limit
          description: |
            Maximum number of orders to return. Values above 100 are
            rejected with `400` rather than clamped. Cursors from one
            filter cannot be used with another; doing so returns `400`.
```

## Authentication documentation

Security schemes live in `components/securitySchemes` and are referenced by name from
`security`. The four you will meet most:

```yaml
components:
  securitySchemes:
    apiKey:
      type: apiKey
      in: header
      name: X-Api-Key
    bearerAuth:
      type: http
      scheme: bearer
      bearerFormat: JWT
    oauth2:
      type: oauth2
      flows:
        authorizationCode:
          authorizationUrl: https://auth.example.com/oauth2/authorize
          tokenUrl: https://auth.example.com/oauth2/token
          refreshUrl: https://auth.example.com/oauth2/token
          scopes:
            orders:read: Read orders
            orders:write: Create and modify orders
    openId:
      type: openIdConnect
      openIdConnectUrl: https://auth.example.com/.well-known/openid-configuration
```

Apply globally, then override per operation:

```yaml
security:
  - oauth2:
      - orders:read
paths:
  /orders:
    get:
      security:
        - oauth2:
            - orders:read
    delete:
      security:
        - oauth2:
            - orders:write
```

Document the flow in prose next to the scheme: how to obtain a credential, where to send it,
what a failure looks like, and how to rotate it. A `securitySchemes` entry tells a generator
where the header goes; it does not tell a human how to authenticate.

If you support OAuth, require PKCE for public clients, and prefer short-lived access tokens
with refresh over long-lived ones. The security best current practice is written down; see the
references.

## Versioning and changelogs

Three workable strategies:

| Strategy | Shape | Cost | Note |
| --- | --- | --- | --- |
| URL path | `https://api.example.com/v2/orders` | Clear routing and caching | Two base URLs to document |
| Header | `Accept: application/vnd.example.v2+json` | One URL | Proxies must not strip it |
| Media type | `Content-Type` / `Accept` versioning | Standards-clean | Least familiar |

Whatever you pick:

- Publish a changelog per version with dated entries describing behavior changes, not just
  refactors. Consumers care what breaks.
- State the deprecation policy in the docs: how long support lasts, and what the migration
  path is.
- Keep old versions documented until they are switched off. Deleting the spec for a version you
  still serve generates "docs are missing" issues, not adoption.

## Deprecation notices

In the spec:

```yaml
paths:
  /legacy/coupons:
    get:
      deprecated: true
      summary: List coupons (deprecated)
      description: |
        Deprecated. Use `GET /v1/coupons` instead.
        Scheduled for removal on 2026-06-30.
```

On the wire, the `Sunset` header (RFC 8594) tells intermediaries when the resource goes away:

```text
Sunset: Wed, 30 Jun 2026 00:00:00 GMT
Link: <https://api.example.com/v1/coupons>; rel="successor-version"
Deprecation: true
```

The `Deprecation` response header is a real header for signalling that a resource is
deprecated, and `Sunset` is its companion. Dates are HTTP-date values, always in GMT.

A deprecation without a migration path is an announcement, not a plan. Always link the
replacement.

## Keeping the spec in sync with code

Hand-written specs rot. Options, in increasing order of automation:

1. **Generate the schema portion** from the code (route decorators, Zod/JSON Schema types,
   ORM models) and write descriptions by hand in an overlay. This is the pragmatic middle.
2. **Lint the spec in CI** so malformed documents fail the build:

```bash
npx @redocly/cli lint openapi.yaml
```

3. **Diff specs against the base branch** to catch breaking changes before release. `oasdiff`
is a real tool for this:

```bash
oasdiff breaking base.yaml head.yaml   # exit non-zero on a breaking change
oasdiff diff base.yaml head.yaml      # print all changes
```

4. **Contract-test the running service** against the spec in a test environment, so drift
   surfaces as a failing test rather than a support ticket.

Spectral is another real linter with an OpenAPI ruleset:

```yaml
# .spectral.yaml
extends:
  - spectral:oas
rules:
  operation-description: warn
  operation-operationId: error
  oas3-api-servers: warn
```

Use whatever your team already has; the important part is that an invalid spec breaks CI.

## Documentation toolchain

| Tool | Notes |
| --- | --- |
| Redoc | Renders a single `openapi.yaml`; good for read-only public reference |
| Swagger UI | Widely embedded, interactive "try it out" |
| Scalar | Modern alternative to Swagger UI/Redoc; check current docs |
| Stoplight Elements | Editor plus viewer in one |
| Docusaurus plugin / Mintlify / ReadMe | Hosted docs with API reference built in |
| Static site generator plus OpenAPI | Full control, more upkeep |

Whatever renders it, publish the raw spec too. Consumers will want to generate their own
clients from it.

## Checklist

- [ ] Every operation has `operationId`, `summary`, `description`, and `responses`.
- [ ] Path parameters are declared and `required: true`.
- [ ] Query, header, and body constraints are in the schema, not only in prose.
- [ ] Success response shape is documented, including `headers` that matter.
- [ ] Every realistic error status is documented with what triggers it.
- [ ] A shared error body schema exists and is referenced from `default`.
- [ ] `securitySchemes` match the real auth flow, with scopes documented.
- [ ] At least one realistic request and response example per major resource.
- [ ] Deprecated operations marked `deprecated: true` with a successor and a date.
- [ ] Spec lints clean in CI and a diff check runs on PRs.
- [ ] Version changelog published alongside the reference.
- [ ] Raw spec downloadable.

## Common mistakes

**Missing error responses.** Symptom: every documented code is 2xx. Readers write clients that
crash on a real 409. Fix: enumerate the statuses the handler can actually return, including
the ones from shared middleware.

**Status codes that do not match the implementation.** Symptom: spec says `404`, service returns
`200` with a null body. Fix: contract-test the service against the spec.

**Prose constraints instead of schema constraints.** Symptom: generated clients accept
`limit=99999` and the server 500s. Fix: move `maximum` into the schema.

**`operationId` churn.** Symptom: "method getOrder2 does not exist" after a client regen.
Fix: treat `operationId` as public API; never rename without a breaking-change process.

**Unbounded strings.** `type: string` with no format or pattern tells a consumer nothing about
length. Add `maxLength`, `format`, or an `enum` where they are real constraints.

**Documenting an internal endpoint set by accident.** Symptom: admin endpoints appear in public
docs. Fix: split specs per audience, and lint for unintended paths.

**Free-form error bodies.** Symptom: `{"message": "..."}` with no schema, so clients cannot
parse it. Fix: define one error schema with a machine-readable `code` plus a human `message`.

**Vendoring a generated spec with no descriptions.** Symptom: field names only, no meaning.
Generation gives you shapes; humans give them meaning.

## Anti-patterns

- **Two sources of truth.** A spec file and a wiki page, diverging. Pick one and link.
- **Documenting the happy path only**, which is what a generated spec from types gives you.
- **Screenshots of JSON responses.** Text diffs break; a schema does not.
- **Marketing copy in `info.description`.** Keep the contract clean; put the pitch on the
  product page.
- **Breaking changes in a minor version.** If consumers auto-upgrade, that is a major.
- **Publishing a spec you do not validate.** An invalid spec breaks every generator.
- **Rewriting `operationId` or field names to be "prettier".** It is a breaking change.
- **Deprecating without a `Sunset` date or a successor link.** Consumers cannot plan against
  "eventually".

## References

- OpenAPI Specification 3.1.0: https://spec.openapis.org/oas/v3.1.0
- OpenAPI Specification 3.0.3: https://spec.openapis.org/oas/v3.0.3
- OpenAPI Specification index (all versions): https://spec.openapis.org/
- OpenAPI Guide (how to structure a document): https://swagger.io/docs/specification/v3_0/guide/
- JSON Schema 2020-12 (the dialect OpenAPI 3.1 uses for schemas):
  https://json-schema.org/specification-2020-12
- Redocly CLI documentation: https://redocly.com/docs/cli/
- Redoc documentation: https://redocly.com/docs/redoc/
- Spectral rulesets and documentation: https://docs.stoplight.io/docs/spectral
- oasdiff (breaking-change detection): https://github.com/oasdiff/oasdiff
- Scalar API reference: https://docs.scalar.com/scalar/scalar-api-reference
- Swagger UI: https://swagger.io/tools/swagger-ui/
- RFC 9110 HTTP Semantics (status codes): https://www.rfc-editor.org/rfc/rfc9110
- RFC 6749 The OAuth 2.0 Authorization Framework: https://www.rfc-editor.org/rfc/rfc6749
- RFC 6750 Bearer Token Usage: https://www.rfc-editor.org/rfc/rfc6750
- RFC 7636 Proof Key for Code Exchange (PKCE): https://www.rfc-editor.org/rfc/rfc7636
- RFC 9700 OAuth 2.0 Security Best Current Practice: https://www.rfc-editor.org/rfc/rfc9700
- RFC 8594 The Sunset HTTP Header Field: https://www.rfc-editor.org/rfc/rfc8594
- MDN, "Deprecation" HTTP header: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Deprecation
- MDN, "Sunset" HTTP header: https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Sunset