---
name: GraphQL API Design and Performance
description: Covers GraphQL schema design, resolver and DataLoader patterns to avoid N+1, query depth and complexity limits, Relay cursor pagination, error handling with extensions, subscriptions over WebSocket or SSE, and schema versioning with deprecation. Use when the user asks to design or review a GraphQL schema, fix slow nested queries, add pagination to a connection, limit query cost, handle GraphQL errors, or set up subscriptions.
metadata:
  opencode/autoinvoke: true
---

# GraphQL API Design and Performance

GraphQL moves query composition from the server to the client, which buys flexibility and charges you with cost control. The client can ask for any tree it likes, so the server must bound depth, breadth, and total cost, and must resolve each object once regardless of how many times it appears. Design nullability deliberately, paginate every list connection, and put authorization in resolvers where field-level context exists.

## When to use this

- Designing or reviewing a GraphQL schema, especially nullability and list shapes.
- Debugging a query that issues hundreds of SQL statements (N+1).
- Adding depth limits, cost analysis, or complexity scoring to a public endpoint.
- Implementing Relay-style cursor pagination and connection types.
- Deciding error shape, partial responses, and what to put in `extensions`.
- Setting up subscriptions over WebSocket, SSE, or a message bus.
- Deciding how to version a schema without breaking clients.

## Quick start

A schema with typed connections, nullable-by-default fields, and a per-request DataLoader:

```ts
import { createServer } from "node:http";
import DataLoader from "dataloader";
import { makeExecutableSchema } from "@graphql-tools/schema";
import { graphql } from "graphql";

type Db = {
  users: Map<string, { id: string; name: string; tenantId: string }>;
  posts: Map<string, { id: string; authorId: string; title: string; publishedAt: string | null }>;
};
declare const db: Db;

// --- Schema -------------------------------------------------------------
export const typeDefs = /* GraphQL */ `
  scalar DateTime
  scalar JSON

  type User {
    id: ID!
    tenantId: ID!
    name: String!
    posts(first: Int = 20, after: String): PostConnection!
  }

  type Post {
    id: ID!
    author: User!
    title: String!
    publishedAt: DateTime
  }

  type PageInfo {
    hasNextPage: Boolean!
    hasPreviousPage: Boolean!
    startCursor: String
    endCursor: String
  }

  type PostEdge {
    cursor: String!
    node: Post
  }

  type PostConnection {
    edges: [PostEdge!]!
    nodes: [Post!]!
    pageInfo: PageInfo!
    totalCount: Int
  }

  type Query {
    viewer: User
    user(id: ID!): User
    posts(first: Int = 20, after: String, status: PostStatus = PUBLISHED): PostConnection!
  }

  input CreatePostInput {
    title: String!
    body: String
    draft: Boolean = false
  }

  type CreatePostPayload {
    post: Post
    userErrors: [UserError!]!
  }

  type UserError {
    field: [String!]
    message: String!
    code: ErrorCode!
  }

  enum ErrorCode {
    BAD_USER_INPUT
    NOT_FOUND
    FORBIDDEN
    INTERNAL_SERVER_ERROR
  }

  enum PostStatus {
    PUBLISHED
    DRAFT
  }

  type Mutation {
    createPost(input: CreatePostInput!): CreatePostPayload!
  }
`;

// --- Per-request context ------------------------------------------------
type Context = { userId: string | null; tenantId: string | null; loaders: Loaders };

type Loaders = {
  userById: DataLoader<string, { id: string; name: string; tenantId: string } | null>;
  postsByAuthor: DataLoader<string, { id: string; authorId: string; title: string; publishedAt: string | null }[]>;
};

// A DataLoader must be created per request, never at module scope.
// Sharing one leaks data between users and caches forever.
function createLoaders(): Loaders {
  return {
    userById: new DataLoader(async (ids) => {
      const rows = [...db.users.values()].filter((u) => ids.includes(u.id));
      const byId = new Map(rows.map((r) => [r.id, r]));
      return ids.map((id) => byId.get(id) ?? null);
    }),
    postsByAuthor: new DataLoader(async (authorIds) => {
      const grouped = new Map<string, typeof db.posts extends Map<string, infer T> ? T[] : never>();
      for (const p of db.posts.values()) {
        if (!authorIds.includes(p.authorId)) continue;
        const list = grouped.get(p.authorId) ?? [];
        list.push(p);
        grouped.set(p.authorId, list);
      }
      return authorIds.map((id) => grouped.get(id) ?? []);
    }),
  };
}

const resolvers = {
  Query: {
    viewer: (_p: unknown, _a: unknown, ctx: Context) =>
      ctx.userId ? ctx.loaders.userById.load(ctx.userId) : null,
  },
  User: {
    posts: (parent: { id: string }, args: { first?: number }, ctx: Context) =>
      ctx.loaders.postsByAuthor.load(parent.id).then((all) => all.slice(0, args.first ?? 20)),
  },
  Post: {
    author: (parent: { authorId: string }, _a: unknown, ctx: Context) =>
      ctx.loaders.userById.load(parent.authorId),
  },
};

export function buildSchema() {
  return makeExecutableSchema({ typeDefs, resolvers });
}

async function handle(req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse) {
  const body = JSON.parse((await readBody(req)) || "{}") as {
    query: string;
    variables?: Record<string, unknown>;
    operationName?: string;
  };
  const result = await graphql({
    schema: buildSchema(),
    source: body.query,
    variableValues: body.variables,
    operationName: body.operationName,
    contextValue: { userId: "u_1", tenantId: "t_1", loaders: createLoaders() } satisfies Context,
  });
  res.setHeader("content-type", "application/json");
  res.end(JSON.stringify(result));
}

const server = createServer(handle);
server.listen(4000, () => console.log("GraphQL on :4000"));
```

Replace the simple resolver map above with the full one, including connection resolvers and per-argument authorization, in `references/resolvers.md`.

## Schema design

### Nullability is the decision that matters most

Every nullable field is a branch that every client must handle. Every non-null field is a promise that a resolver will break. Default new fields to **nullable** unless you control every client and can accept the coupling.

| Field type | Client must handle null | Meaning |
| --- | --- | --- |
| `String!` | no | Always present. A resolver error nulls out the parent. |
| `String` | yes | Optional. Absent is normal. |
| `[String!]!` | no | List and its elements are always present. |
| `[String]!` | elements only | List is always present, elements may be null. |
| `[String!]` | list and elements | Rarely what you want. |

For mutations, never return a bare nullable type. A `createPost: Post` field that errors forces the client into ambiguous handling: did it fail, or did it succeed with no result? Use the Relay-style payload with an explicit error list:

```graphql
type Mutation {
  createPost(input: CreatePostInput!): CreatePostPayload!
}

type CreatePostPayload {
  post: Post
  userErrors: [UserError!]!
}
```

Rule of thumb: `userErrors` is business-logic failure (validation, permission, not found), handled with an HTTP `200` and a populated error list. Transport-level and unexpected failures propagate as top-level GraphQL errors.

### Naming and shape conventions

- Types `PascalCase`, fields and arguments `camelCase`, enum values `SCREAMING_SNAKE_CASE`.
- Fields return objects, not booleans about objects. `post.isPublished` forces a follow-up query for `publishedAt`; `post { state publishedAt }` does not.
- Avoid booleans as arguments that change shape (`includeDrafts: Boolean`). Prefer a filter enum or an input object that extends cleanly.
- Expose `node(id: ID!)` and implement `Node` on every type if you want Relay-style client caching and normalized stores.
- Use input objects for mutations with more than three arguments, and give every input field a default where the default matters.
- Follow the schema-design conventions in the official GraphQL guide rather than inventing house style; see References.

### Connections

Every list field should be a connection with `first`/`after` (and `last`/`before`) plus `pageInfo`. This is not ceremony: without cursors, clients cannot paginate a list that changes during traversal, and they will request the entire table.

```graphql
type Query {
  posts(first: Int = 20, after: String): PostConnection!
}

type PostConnection {
  edges: [PostEdge!]!      # each edge carries a cursor
  nodes: [Post!]!          # convenience view for clients that do not need cursors
  pageInfo: PageInfo!      # hasNextPage, hasPreviousPage, startCursor, endCursor
  totalCount: Int          # expensive; compute only when the client asks for it
}
```

`pageInfo.hasNextPage` is mandatory. Without it the client must issue an extra query to discover whether more data exists, doubling round trips on every list fetch.

Decoding the cursor server-side:

```ts
function decodeCursor(cursor: string): { createdAt: string; id: string } {
  const raw = Buffer.from(cursor, "base64url").toString("utf8");
  const idx = raw.lastIndexOf("|");
  if (idx < 1) throw new GraphQLError("Malformed cursor", { extensions: { code: "BAD_USER_INPUT" } });
  return { createdAt: raw.slice(0, idx), id: raw.slice(idx + 1) };
}
```

Always clamp `first` server-side, for example to `[1, 100]`. An unbounded `first` is an unbounded query no matter what the schema's default says, because the client can pass `first: 1000000`.

## Resolvers and N+1

The N+1 problem is the defining performance failure of GraphQL. A query like this returns 10 users and issues 11 database round trips:

```graphql
query {
  users {
    id
    posts { title }     # one query per user
  }
}
```

DataLoader collapses all sibling `posts` resolutions in the same tick into one batched call. Two rules make it work:

1. **One loader per request.** A module-level loader caches forever and serves one user's data to the next request. This is both an N+1 regression and a data-leak vulnerability.
2. **Batch by cache key.** The batch function receives all distinct keys and must return results in exactly the same order.

```ts
const postsByAuthor = new DataLoader<string, Post[]>(async (authorIds) => {
  // One round trip for every author requested in this tick.
  const rows = await db.query(
    `SELECT id, author_id, title, published_at
       FROM posts
      WHERE author_id = ANY($1::uuid[])
      ORDER BY created_at DESC`,
    [authorIds],
  );
  const grouped = new Map<string, Post[]>();
  for (const r of rows) {
    const list = grouped.get(r.author_id) ?? [];
    list.push(r);
    grouped.set(r.author_id, list);
  }
  // Order and length must match the incoming keys.
  return authorIds.map((id) => grouped.get(id) ?? []);
});
```

Load behind objects rather than on the type. A field resolver that returns the parent row and lets a `posts` resolver batch keeps batching within one request. A resolver that calls `fetch()` directly cannot be batched because nothing coordinates the sibling calls.

When a resolver needs fresh data after a mutation, invalidate rather than guess: `loaders.postsByAuthor.clear(authorId)` after a write, so the next read repopulates the batch cache.

### Depth and complexity limits

A flat query with `first: 100` and three nested lists can already be expensive. Nested repetition multiplies: at depth 10 with three branches, worst-case node count is in the thousands. Set limits at the edge, before parsing or execution.

Three complementary controls:

| Control | What it stops | How |
| --- | --- | --- |
| Depth limit | `a { b { c { d { ... } } } }` bombs | Static AST walk counting selection set depth |
| Complexity/cost | Wide fan-out: `a { x y z }` and large `first` values | Score each field; multiply by `first` for lists |
| Timeouts | Slow-but-cheap queries | Abort the request after a wall-clock budget |

The depth limit is a straightforward static walk over the document AST:

```ts
import { type DocumentNode, Kind, type SelectionSetNode, GraphQLError } from "graphql";

function selectionSetDepth(set: SelectionSetNode, current: number, maxAllowed: number): number {
  if (current > maxAllowed) {
    throw new GraphQLError(`Query depth exceeds the limit of ${maxAllowed}`, {
      extensions: { code: "QUERY_TOO_DEEP", maxAllowed },
    });
  }
  let deepest = current;
  for (const sel of set.selections) {
    if (sel.kind === Kind.FIELD && sel.selectionSet) {
      deepest = Math.max(deepest, selectionSetDepth(sel.selectionSet, current + 1, maxAllowed));
    } else if (sel.kind === Kind.INLINE_FRAGMENT) {
      deepest = Math.max(deepest, selectionSetDepth(sel.selectionSet, current, maxAllowed));
    }
    // Named fragments are resolved by their own definition; a complete
    // implementation must also walk fragment definitions and expand them.
  }
  return deepest;
}

function assertDepthWithin(doc: DocumentNode, maxAllowed: number): void {
  for (const def of doc.definitions) {
    if (def.kind === Kind.OPERATION_DEFINITION && def.selectionSet) {
      selectionSetDepth(def.selectionSet, 0, maxAllowed);
    }
  }
}
```

Note that named fragments must be expanded too: a client hides depth behind `...BigFragment` and a naive walker that only follows inline selections misses it entirely.

In practice, most servers expose depth and cost analysis through the GraphQL server library you already use (Yoga, Apollo Server, GraphQL Java, Strawberry, gqlgen). Use the library's built-in analyzer instead of a hand-rolled walker, and check its documentation for the exact configuration key, since names differ between servers.

For cost analysis, the multiplier rule is the useful part: a list field's cost is its child's cost times `first`, so `first: 100` on a nested connection dominates the budget. Require a lower cost budget for authenticated anonymous callers than for logged-in ones.

## Error handling

GraphQL has two error channels and conflating them is the most common design mistake.

| | Top-level `errors` | `payload.userErrors` |
| --- | --- | --- |
| Where | `errors` array next to `data` | Inside the mutation payload |
| When | Field resolution failed unexpectedly | Business rule rejected the input |
| HTTP status | Often `200`, or `400` for malformed requests | `200` |
| Client handling | Show generic message, log `extensions` | Show field-level message next to the input |

Design rules:

- Always include a machine-readable `extensions.code`. Clients branch on code, never on message text.
- Never leak internals. Map internal exceptions to `INTERNAL_SERVER_ERROR` with a generic detail and log the original with a correlation id you return in `extensions.traceId`.
- Partial success is normal. `data` and `errors` both populated means some fields resolved. A client that discards `data` whenever `errors` is present is treating GraphQL as REST.
- A non-null field whose resolver throws nulls its **parent**. A throw in `User.name!` nulls the whole `User` object; a throw in `User.posts[3].title!` nulls that single element. This propagation is why nullability choices have blast radius.

```ts
import { GraphQLError } from "graphql";

function publicError(err: unknown, traceId: string): GraphQLError {
  if (err instanceof GraphQLError) return err;
  // Unexpected: log with context, return nothing sensitive.
  logger.error({ err, traceId }, "resolver failure");
  return new GraphQLError("Something went wrong. Reference " + traceId, {
    extensions: { code: "INTERNAL_SERVER_ERROR", traceId },
  });
}
```

Extensions code conventions: the GraphQL over HTTP working group and Apollo popularized `extensions.code` names such as `UNAUTHENTICATED`, `FORBIDDEN`, `BAD_USER_INPUT`, `GRAPHQL_VALIDATION_FAILED`, and `GRAPHQL_PARSE_FAILED`. Reusing them means existing clients handle yours correctly.

Introspection: disable `__schema` and `__type` in production. Introspection is how an attacker enumerates your entire schema in one request, which is exactly the reconnaissance you do not want to publish.

## Authorization

Field-level authorization belongs in resolvers, because the resolver knows the parent row and the caller's identity. Middleware can authenticate; it cannot authorize `thisOrder.viewerCanEdit` without fetching the object.

```ts
type Context = { userId: string | null; tenantId: string | null; loaders: Loaders };

function requireUser(ctx: Context): string {
  if (!ctx.userId) {
    throw new GraphQLError("Sign in required", { extensions: { code: "UNAUTHENTICATED" } });
  }
  return ctx.userId;
}

const resolvers = {
  Query: {
    viewer: (_p: unknown, _a: unknown, ctx: Context) =>
      ctx.userId ? ctx.loaders.userById.load(ctx.userId) : null,

    post: async (_p: unknown, { id }: { id: string }, ctx: Context) => {
      const post = await db.posts.get(id);
      if (!post) return null;                      // hide existence
      // Multi-tenant isolation enforced at the resolver boundary.
      const author = await ctx.loaders.userById.load(post.authorId);
      if (!author || author.tenantId !== ctx.tenantId) return null;
      return post;
    },
  },

  User: {
    // Never accept a caller-supplied tenantId; derive it from the token.
    posts: (parent: { id: string }, args: { first?: number; after?: string }, ctx: Context) =>
      ctx.loaders.postsByAuthor.load(parent.id).then((all) => paginate(all, args)),
  },

  Mutation: {
    createPost: async (_p: unknown, { input }: { input: CreatePostInput }, ctx: Context) => {
      const userId = requireUser(ctx);
      const title = input.title.trim();
      if (title.length === 0 || title.length > 200) {
        return {
          post: null,
          userErrors: [{ field: ["title"], message: "Title must be 1-200 characters.", code: "BAD_USER_INPUT" }],
        };
      }
      const post = await postsRepo.create({ authorId: userId, title, draft: input.draft ?? false });
      ctx.loaders.postsByAuthor.clear(userId);
      return { post, userErrors: [] };
    },
  },
};

export { resolvers };
```

Return `null` rather than throwing `FORBIDDEN` for resources the caller must not know exist. Throwing on every unauthorized lookup turns your API into an enumeration oracle: a `FORBIDDEN` for order 1001 and `NOT_FOUND` for order 1002 tells the attacker exactly which IDs are real.

Also cap returned collections by authorization. A `users(first: 100)` root field that returns every tenant's users is a cross-tenant leak even when no individual field resolver throws.

## Subscriptions

Subscriptions are long-lived operations with three requirements queries do not have: authentication at connect time, a per-connection authorization story, and lifecycle management.

Pick a transport:

| Transport | Bidirectional | Proxy-friendly | Notes |
| --- | --- | --- | --- |
| `graphql-ws` over WebSocket | yes | poor | Most common. Needs sticky sessions or a shared registry. |
| SSE (`graphql-sse`) | server to client only | good | Works through HTTP proxies; reconnection is built in. |
| WebTransport | yes | limited | Newer, less deployed tooling. |

The protocol packages are `graphql-ws` and `graphql-sse`; most servers implement one or both. Check your server's docs for the exact option name, because enabling subscriptions and choosing a protocol are separate configuration steps that changed names across major versions.

Rules for subscription resolvers:

- Authenticate during the `connection_init` / connection-accept handshake, not on first message. A subscription without a validated token at connect time is an open pipe.
- Re-authorize on subscribe: the client may have lost permission between connect and subscribe.
- Scope every subscription by tenant and user id at the publish site. Publishing to a topic named `order.updated` and filtering in the subscriber leaks data through timing and error differences.
- Always implement a heartbeat or ping and clean up on unsubscribe. Idle sockets held open by disconnected clients accumulate until the broker or load balancer drops them.

```graphql
type Mutation {
  orderPlaced: Order!
}

type Subscription {
  orderUpdated(orderId: ID!): Order!
}
```

The `orderUpdated` subscription takes the `orderId` so the server can check the caller may view it before streaming anything. A subscription with no arguments is a firehose of the entire dataset to every authenticated subscriber.

## Versioning

GraphQL has no version field in the URL, so schema change management is the whole game. The answer is additive change plus deprecation, not versions.

Safe changes:

- Adding a field to a type.
- Adding an optional argument to a field with a default that preserves previous behavior.
- Adding an enum value to an enum used only as an output (breaking for clients with exhaustive switches, so document it).
- Adding a new type or query field.

Breaking changes:

- Removing or renaming a field.
- Changing a field's type.
- Making a nullable field non-null.
- Adding a required argument with no default.
- Removing an enum value.
- Changing a mutation's payload shape.

Deprecate before you remove. `@deprecated` on a field shows in introspection and in every schema-aware client's IDE, so developers find out while they still have a working code path:

```graphql
type User {
  displayName: String!
    @deprecated(reason: "Use `name`. Removal after 2026-03-01.")
  name: String!
}
```

Track field usage from the gateway or server metrics, then delete fields nobody reads after the deprecation window. A deprecation nobody measures is a comment. The official GraphQL guide covers schema governance and versioning practice in detail; see References.

Never expose `__typename`-based branching as your versioning strategy (`... on UserV2`). It puts version dispatch inside every client and guarantees you maintain all versions forever.

## Checklist

Before shipping a GraphQL schema:

- [ ] Every list field is a connection with `first`/`after` and a `pageInfo.hasNextPage`.
- [ ] `first`/`last` are clamped server-side to a documented maximum.
- [ ] DataLoader instances are created per request, never at module scope.
- [ ] Every loader batch function returns results in the same order and length as its keys.
- [ ] Depth and complexity limits are enforced at the edge, before execution.
- [ ] Mutations return a payload type with `userErrors`, not a bare nullable type.
- [ ] Every error carries `extensions.code`; internal errors never leak messages or stack traces.
- [ ] Introspection is disabled in production.
- [ ] Authorization is enforced in resolvers, including tenant isolation on every root field.
- [ ] Unauthorized lookups return `null` rather than a distinguishable `FORBIDDEN`.
- [ ] Subscriptions authenticate at connect time and re-authorize per subscription.
- [ ] Deprecated fields carry `@deprecated(reason: "... removal date ...")` and have usage telemetry.
- [ ] Schema changes are diffed in CI against the published schema.

## Common mistakes

- **Module-scope DataLoader.** Symptom: the second user to hit the API sees the first user's cached rows; in tests, results leak between cases. Fix: construct loaders inside the per-request context factory.
- **Loader returning the wrong order.** Symptom: intermittent, data-dependent mix-ups where user A gets user B's posts; only appears under concurrency. Fix: always map over the incoming keys array when constructing the return value.
- **Throwing from a non-null field.** Symptom: one failed lookup nulls an entire nested object and the client shows a blank panel instead of a partial result. Fix: make fields that can fail nullable, or push errors into a payload.
- **Unbounded `first`.** Symptom: a single query with `first: 100000` saturates the database. Fix: clamp arguments in the resolver before querying.
- **Errors-only responses.** Symptom: a client discards `data` whenever `errors` is non-empty and loses every successfully resolved field. Fix: handle partial responses properly, and reserve top-level errors for genuine failures.
- **Introspection left on in production.** Symptom: your full schema is enumerable by any unauthenticated caller. Fix: disable `__schema` and `__type` outside development.
- **Leaking internal error text.** Symptom: SQL fragments and file paths appear in client-visible messages. Fix: map unknown exceptions to a generic `INTERNAL_SERVER_ERROR` with a trace id.
- **Loader never invalidated after a mutation.** Symptom: after creating a post, the sidebar list still shows the old set until the request context is rebuilt. Fix: `clear()` the relevant loader keys inside the mutation resolver.
- **Unbounded subscription topic.** Symptom: every subscriber receives every event and filters client-side. Fix: scope the topic by tenant and object id at publish time.

## Anti-patterns

- **Field-per-boolean instead of an input object.** `createPost(title: String!, body: String, draft: Boolean, featured: Boolean, scheduledAt: String, ...)`. Every new field is a breaking change for clients that enumerate arguments, and the server cannot evolve defaults coherently. Use `input CreatePostInput` with fields.
- **`String` for everything.** A JSON blob stored in `String` moves validation out of the schema and into every client. Use custom scalars with parse and serialize functions when you need a format GraphQL does not model.
- **Resolvers that return booleans about objects** (`post.hasAuthor`, `user.isAdmin`). Forces a follow-up round trip and produces fields that can contradict each other. Return the object or an explicit state enum.
- **Rejecting unknown fields with a custom error message.** In the GraphQL spec, queries containing fields not in the schema fail validation. Do not disable that to "be lenient"; you lose typo detection across every client in the ecosystem.
- **Fetching in the root resolver and returning a fully denormalized tree**, which is a REST API wearing a GraphQL costume. Clients cannot compose, and you pay to serialize fields nobody asked for.
- **Per-connection subscriptions with global topic names** (`orderUpdated(orderId: ID!)` implemented by broadcasting to `all-orders` and filtering). Filtering after receipt means unauthorized data has already crossed the process boundary.
- **`@deprecated` with no removal date and no usage tracking.** Clients never migrate, and when you finally delete the field it is a breaking change.
- **Running a separate endpoint per GraphQL "version"** with hand-maintained schemas that drift. Additive change plus measured deprecation avoids the drift entirely.

## References

- GraphQL Learn — https://graphql.org/learn/
- Schema definition — https://graphql.org/learn/schema
- Schema design best practices — https://graphql.org/learn/schema-design
- Naming conventions — https://graphql.org/learn/naming-design
- Pagination — https://graphql.org/learn/pagination
- Queries — https://graphql.org/learn/queries
- Mutations — https://graphql.org/learn/mutations
- Subscriptions — https://graphql.org/learn/subscriptions
- Validation — https://graphql.org/learn/validation
- Execution — https://graphql.org/learn/execution
- Errors and partial responses — https://graphql.org/learn/response
- Error handling — https://graphql.org/learn/error-handling
- Authorization — https://graphql.org/learn/authorization
- Security — https://graphql.org/learn/security
- Caching — https://graphql.org/learn/caching
- Performance — https://graphql.org/learn/performance
- Serving over HTTP — https://graphql.org/learn/serving-over-http
- Schema review — https://graphql.org/learn/schema-review/
- Schema governance and versioning — https://graphql.org/learn/governance-versioning
- GraphQL specification — https://spec.graphql.org/
- Relay Cursor Connections Specification — https://relay.dev/graphql/connections.htm
- DataLoader (the original library and rationale) — https://github.com/graphql/dataloader
- OWASP GraphQL Cheat Sheet — https://cheatsheetseries.owasp.org/cheatsheets/GraphQL_Cheat_Sheet.html
- GraphQL Yoga documentation (server, subscriptions, plugins) — https://the-guild.dev/graphql/yoga-server/docs
- Apollo Server documentation — https://www.apollographql.com/docs/