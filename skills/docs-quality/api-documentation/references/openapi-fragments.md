# OpenAPI fragments and CI wiring

Reference material for `api-documentation`. Field names here follow OpenAPI 3.1.0. If you
target 3.0.x, check the differences noted at the bottom of this file.

## Standard error schema

Define once, reference everywhere.

```yaml
components:
  responses:
    BadRequest:
      description: The request failed schema or semantic validation.
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/Error'
    Unauthorized:
      description: Missing or invalid credentials.
    Forbidden:
      description: Credentials are valid but not permitted for this resource.
    NotFound:
      description: The resource does not exist, or the caller cannot see it.
    Conflict:
      description: The request conflicts with the current state of the resource.
    UnprocessableEntity:
      description: Well-formed but semantically invalid.
    TooManyRequests:
      description: Rate limit exceeded. See the `Retry-After` header.
      headers:
        Retry-After:
          description: Seconds to wait before retrying.
          schema:
            type: integer
    Default:
      description: Unexpected error.
      content:
        application/json:
          schema:
            $ref: '#/components/schemas/Error'

  schemas:
    Error:
      type: object
      required: [code, message]
      properties:
        code:
          type: string
          description: Stable, machine-readable error identifier.
          examples: [order_not_found]
        message:
          type: string
          description: Human-readable description. Not stable; do not parse.
        requestId:
          type: string
          description: Correlation ID. Quote this in support requests.
```

Use them per operation:

```yaml
      responses:
        '200':
          description: The order.
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Order'
        '404':
          $ref: '#/components/responses/NotFound'
        default:
          $ref: '#/components/responses/Default'
```

## Pagination, filters, and idempotency

```yaml
    get:
      operationId: listOrders
      parameters:
        - name: limit
          in: query
          schema:
            type: integer
            minimum: 1
            maximum: 100
            default: 20
          description: Page size.
        - name: cursor
          in: query
          schema:
            type: string
          description: Opaque cursor from the previous response's `next_cursor`.
        - name: status
          in: query
          schema:
            type: array
            items:
              type: string
              enum: [pending, paid, shipped, cancelled]
          description: Repeat the parameter to filter on several values.
          style: form
          explode: true
        - name: Idempotency-Key
          in: header
          schema:
            type: string
            format: uuid
          description: |
            Client-generated key. Replaying the same key with the same
            body returns the original response instead of creating a
            second resource.
      responses:
        '200':
          description: A page of orders.
          content:
            application/json:
              schema:
                type: object
                required: [items]
                properties:
                  items:
                    type: array
                    items:
                      $ref: '#/components/schemas/Order'
                  next_cursor:
                    type: string
                    nullable: true
                    description: Null when the last page has been reached.
```

In OpenAPI 3.1 with a JSON Schema 2020-12 dialect, `nullable` is not a keyword. Use
`type: ["string", "null"]` instead. If you must support 3.0.x tooling, `nullable: true` is the
3.0 way.

## Webhooks (OpenAPI 3.1)

```yaml
webhooks:
  invoicePaid:
    post:
      operationId: onInvoicePaid
      summary: Delivered when an invoice is paid.
      requestBody:
        description: The event payload.
        content:
          application/json:
            schema:
              $ref: '#/components/schemas/InvoicePaidEvent'
      responses:
        '200':
          description: |
            Return a 2xx within your stated timeout to acknowledge.
            Non-2xx responses are retried with exponential backoff.
```

`webhooks` is a top-level field added in 3.1. If you target 3.0.x, document webhooks as a
callbacks entry on the subscription endpoint or in prose.

## Callbacks

```yaml
paths:
  /webhooks:
    post:
      operationId: createWebhook
      requestBody:
        required: true
        content:
          application/json:
            schema:
              type: object
              required: [url, events]
              properties:
                url:
                  type: string
                  format: uri
                events:
                  type: array
                  items:
                    type: string
      responses:
        '201':
          description: Registered.
      callbacks:
        invoicePaid:
          '{$request.body#/callbackUrl}':
            post:
              requestBody:
                content:
                  application/json:
                    schema:
                      $ref: '#/components/schemas/InvoicePaidEvent'
              responses:
                '204':
                  description: Acknowledged.
```

The runtime expression key `{$request.body#/callbackUrl}` is the standard way to express a
dynamic callback URL.

## Contract testing the service against the spec

The pattern: point the spec at a test environment, run the suite, and fail on drift.

```bash
# Serve a test environment
npm run start:test

# Validate the served document parses and is structurally sound
npx @redocly/cli lint ./openapi.yaml

# With oasdiff, compare what you are about to release against what is live
oasdiff breaking https://api.example.com/openapi.yaml ./openapi.yaml
```

`oasdiff` accepts file paths; whether it accepts URLs depends on your build. Check its README
for the version you install. The reliable approach is to fetch the live spec and diff the
files:

```bash
curl -fsSL https://api.example.com/openapi.yaml -o /tmp/live.yaml
oasdiff breaking /tmp/live.yaml ./openapi.yaml
```

## CI pipeline

```yaml
name: api-contract
on:
  pull_request:
    paths:
      - 'openapi.yaml'
      - 'src/api/**'

jobs:
  spec:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Lint the specification
        run: npx --yes @redocly/cli lint openapi.yaml

      - name: Bundle for publishing
        run: npx --yes @redocly/cli bundle openapi.yaml -o dist/openapi.yaml

      - name: Detect breaking changes
        run: |
          git fetch --depth=1 origin "${{ github.base_ref }}"
          curl -fsSL "https://raw.githubusercontent.com/${{ github.repository }}/${{ github.base_ref }}/openapi.yaml" -o /tmp/base.yaml
          oasdiff breaking /tmp/base.yaml openapi.yaml
```

Pin the tool versions in a dev dependency rather than using `npx --yes` for reproducible runs,
and install `oasdiff` through your platform's package manager or container image. The commands
above are illustrative of the sequence, not a pinned toolchain.

## Generating clients

```bash
npx @openapitools/openapi-generator-cli generate \
  -i openapi.yaml \
  -g typescript-fetch \
  -o ./generated/client
```

Generator names are exact strings and change between generator versions; list them with the
tool's own help rather than guessing. The important practice: generate the client in CI, diff
it against the committed one, and fail if it changed. That is how you detect an accidental
breaking change before consumers do.

## OpenAPI 3.0 vs 3.1 differences that matter

| Concern | 3.0.x | 3.1.0 |
| --- | --- | --- |
| Schema dialect | A modified JSON Schema draft-04-ish | JSON Schema 2020-12 |
| Nullable | `nullable: true` | `type: ["string", "null"]` |
| Single schema example | `example: {...}` | `examples: [...]` |
| Webhooks | Not supported | Top-level `webhooks` |
| License identifier | `license.name` free text | `license.identifier` (SPDX) |
| `info.summary` | Not present | Present |
| `jsonSchemaDialect` | Not present | Present |

If your renderer, gateway, or client generator only supports 3.0.x, target 3.0.3 rather than
publishing 3.1 that half the toolchain cannot read.

## References

- OpenAPI Specification 3.1.0: https://spec.openapis.org/oas/v3.1.0
- OpenAPI Specification 3.0.3: https://spec.openapis.org/oas/v3.0.3
- OpenAPI Guide: https://swagger.io/docs/specification/v3_0/guide/
- JSON Schema 2020-12: https://json-schema.org/specification-2020-12
- Redocly CLI: https://redocly.com/docs/cli/
- oasdiff: https://github.com/oasdiff/oasdiff
- OpenAPI Generator CLI: https://openapi-generator.tech/docs/installation/
- Spectral: https://docs.stoplight.io/docs/spectral
- RFC 9110 HTTP Semantics: https://www.rfc-editor.org/rfc/rfc9110
- RFC 8594 Sunset header: https://www.rfc-editor.org/rfc/rfc8594