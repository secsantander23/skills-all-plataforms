# Full GraphQL Resolver Reference

Companion to `SKILL.md`. Contains the complete resolver map (connections, cursors, authorization) that `SKILL.md` summarizes, plus the error-handling and cost-analysis helpers.

## Complete schema and resolver map

```ts
import DataLoader from "dataloader";
import { makeExecutableSchema } from "@graphql-tools/schema";
import { GraphQLError } from "graphql";

type Post = {
  id: string;
  authorId: string;
  title: string;
  publishedAt: string | null;
  state: "PUBLISHED" | "DRAFT";
};

type User = { id: string; name: string; tenantId: string };

interface Repos {
  users: { byId(id: string): Promise<User | null> };
  posts: {
    byId(id: string): Promise<Post | null>;
    byAuthorIds(ids: readonly string[]): Promise<Post[]>;
    connection(args: { first?: number | null; after?: string | null; state: string }): Promise<Post[]>;
    create(input: { authorId: string; title: string; state: string }): Promise<Post>;
  };
}
declare const repos: Repos;

export const typeDefs = /* GraphQL */ `
  scalar DateTime

  type User {
    id: ID!
    name: String!
    posts(first: Int = 20, after: String): PostConnection!
  }

  type Post {
    id: ID!
    author: User!
    title: String!
    state: PostState!
    publishedAt: DateTime
  }

  enum PostState { PUBLISHED DRAFT }

  type PageInfo {
    hasNextPage: Boolean!
    hasPreviousPage: Boolean!
    startCursor: String
    endCursor: String
  }

  type PostEdge { cursor: String! node: Post }
  type PostConnection {
    edges: [PostEdge!]!
    nodes: [Post!]!
    pageInfo: PageInfo!
  }

  type Query {
    viewer: User
    posts(first: Int = 20, after: String, state: PostState = PUBLISHED): PostConnection!
  }

  input CreatePostInput {
    title: String!
    body: String
    draft: Boolean = false
  }

  type UserError { field: [String!] message: String! code: ErrorCode! }
  enum ErrorCode { BAD_USER_INPUT NOT_FOUND FORBIDDEN CONFLICT INTERNAL_SERVER_ERROR }

  type CreatePostPayload {
    post: Post
    userErrors: [UserError!]!
  }

  type Mutation { createPost(input: CreatePostInput!): CreatePostPayload! }
`;

export type Context = {
  userId: string | null;
  tenantId: string | null;
  traceId: string;
  loaders: {
    userById: DataLoader<string, User | null>;
    postsByAuthor: DataLoader<string, Post[]>;
  };
};

export function createContext(userId: string | null, tenantId: string | null, traceId: string): Context {
  return {
    userId,
    tenantId,
    traceId,
    loaders: {
      userById: new DataLoader<string, User | null>(async (ids) => {
        const rows = await Promise.all([...ids].map((id) => repos.users.byId(id)));
        return rows; // same order and length as ids
      }),
      postsByAuthor: new DataLoader<string, Post[]>(async (authorIds) => {
        const rows = await repos.posts.byAuthorIds(authorIds);
        const grouped = new Map<string, Post[]>();
        for (const r of rows) {
          const list = grouped.get(r.authorId) ?? [];
          list.push(r);
          grouped.set(r.authorId, list);
        }
        return authorIds.map((id) => grouped.get(id) ?? []);
      }),
    },
  };
}
```

## Cursors and connections

Encode the cursor as the sort-key tuple. Sorting by `(publishedAt, id)` gives a total order, which is what makes keyset pagination stable when timestamps collide.

```ts
const MAX_PAGE_SIZE = 100;

type PageArgs = { first?: number | null; after?: string | null };

type Connection<T> = {
  edges: Array<{ cursor: string; node: T }>;
  nodes: T[];
  pageInfo: {
    hasNextPage: boolean;
    hasPreviousPage: boolean;
    startCursor: string | null;
    endCursor: string | null;
  };
};

function clampPageSize(first: number | null | undefined): number {
  const n = first ?? 20;
  if (!Number.isInteger(n) || n < 1) {
    throw new GraphQLError("`first` must be a positive integer", {
      extensions: { code: "BAD_USER_INPUT", argument: "first" },
    });
  }
  return Math.min(n, MAX_PAGE_SIZE);
}

function encodeCursor(post: Post): string {
  return Buffer.from(`${post.publishedAt ?? ""}|${post.id}`).toString("base64url");
}

function decodeCursor(cursor: string): { publishedAt: string; id: string } {
  const raw = Buffer.from(cursor, "base64url").toString("utf8");
  const idx = raw.lastIndexOf("|");
  if (idx < 1) {
    throw new GraphQLError("Malformed cursor", { extensions: { code: "BAD_USER_INPUT" } });
  }
  return { publishedAt: raw.slice(0, idx), id: raw.slice(idx + 1) };
}

function toConnection(rows: Post[], hasMore: boolean): Connection<Post> {
  const edges = rows.map((node) => ({ cursor: encodeCursor(node), node }));
  return {
    edges,
    nodes: rows,
    pageInfo: {
      hasNextPage: hasMore,
      hasPreviousPage: false, // set true when a `before` cursor was supplied
      startCursor: edges.length ? edges[0].cursor : null,
      endCursor: edges.length ? edges[edges.length - 1].cursor : null,
    },
  };
}
```

## Resolvers

```ts
function requireUser(ctx: Context): string {
  if (!ctx.userId) {
    throw new GraphQLError("Sign in required", { extensions: { code: "UNAUTHENTICATED" } });
  }
  return ctx.userId;
}

function requireTenant(ctx: Context): string {
  if (!ctx.tenantId) {
    throw new GraphQLError("No tenant on this session", { extensions: { code: "UNAUTHENTICATED" } });
  }
  return ctx.tenantId;
}

export const resolvers = {
  Query: {
    viewer: (_parent: unknown, _args: unknown, ctx: Context) =>
      ctx.userId ? ctx.loaders.userById.load(ctx.userId) : null,

    posts: async (_parent: unknown, args: PageArgs & { state: string }, ctx: Context) => {
      const tenantId = requireTenant(ctx);
      const first = clampPageSize(args.first);
      // Fetch one extra row to learn hasNextPage without a second count query.
      const rows = await repos.posts.connection({
        first: first + 1,
        after: args.after ?? null,
        state: args.state,
      });
      return toConnection(rows.slice(0, first), rows.length > first);
    },
  },

  User: {
    posts: async (parent: User, args: PageArgs, ctx: Context) => {
      const first = clampPageSize(args.first);
      const all = await ctx.loaders.postsByAuthor.load(parent.id);
      const start = args.after ? all.findIndex((p) => encodeCursor(p) === args.after) + 1 : 0;
      const slice = all.slice(start < 0 ? 0 : start, (start < 0 ? 0 : start) + first + 1);
      return toConnection(slice.slice(0, first), slice.length > first);
    },
  },

  Post: {
    author: (parent: Post, _args: unknown, ctx: Context) =>
      ctx.loaders.userById.load(parent.authorId),
  },

  Mutation: {
    createPost: async (
      _parent: unknown,
      { input }: { input: { title: string; body?: string | null; draft?: boolean | null } },
      ctx: Context,
    ) => {
      const userId = requireUser(ctx);

      const title = (input.title ?? "").trim();
      if (title.length === 0 || title.length > 200) {
        return {
          post: null,
          userErrors: [
            {
              field: ["title"],
              message: "Title must be between 1 and 200 characters.",
              code: "BAD_USER_INPUT",
            },
          ],
        };
      }

      const draft = input.draft ?? false;
      const post = await repos.posts.create({
        authorId: userId,
        title,
        state: draft ? "DRAFT" : "PUBLISHED",
      });

      // Invalidate so the next read of this author's posts sees the write.
      ctx.loaders.postsByAuthor.clear(userId);

      return { post, userErrors: [] };
    },
  },
};

export function buildSchema() {
  return makeExecutableSchema({ typeDefs, resolvers });
}
```

## Error masking

Keep one place that turns internal failures into client-safe errors:

```ts
import { GraphQLError } from "graphql";

declare const logger: { error(fields: Record<string, unknown>, msg: string): void };

export function maskError(err: unknown, ctx: Context): never {
  if (err instanceof GraphQLError) throw err;
  logger.error({ err, traceId: ctx.traceId, userId: ctx.userId }, "unhandled resolver error");
  throw new GraphQLError(`Internal error. Reference: ${ctx.traceId}`, {
    extensions: { code: "INTERNAL_SERVER_ERROR", traceId: ctx.traceId },
  });
}
```

Wrap handler bodies in `try { ... } catch (err) { maskError(err, ctx) }` so nothing reaches the client by accident.

## Introspection gating

```ts
// Disable introspection outside development. Keep it on in dev so tooling works.
const introspectionEnabled = process.env.NODE_ENV !== "production";

if (!introspectionEnabled) {
  resolvers.Query.__schema = () => {
    throw new GraphQLError("Introspection is disabled", { extensions: { code: "FORBIDDEN" } });
  };
  resolvers.Query.__type = resolvers.Query.__schema;
}
```

The exact option name differs by server, so most servers expose an `introspection` boolean in their configuration instead. Prefer the server's own option; use this only if you are on a bare `graphql-js` setup.

## Cost analysis sketch

Complexity scoring that accounts for list multiplication:

```ts
const FIELD_COST = 1;

function fieldCost(selectionSetLength: number, first: number | null | undefined): number {
  return selectionSetLength * Math.min(first ?? 1, MAX_PAGE_SIZE);
}

function estimateCost(query: string, variables: Record<string, unknown>, budget: number): number {
  // Use the server library's cost analyzer in production; this shows the shape.
  // A nested list multiplies its parent: posts(first:100){author{posts(first:100){...}}}
  // costs far more than the sum of its parts.
  return costOfDocument(query, variables, budget);
}
```

In production, use the analyzer provided by your server (Yoga, Apollo Server, GraphQL Java, Strawberry, gqlgen all ship one). Names and defaults differ, so check that library's documentation rather than copying a config key from another server.

## Testing resolvers without a running server

```ts
import { graphql } from "graphql";

async function run(query: string, ctx: Context) {
  return graphql({
    schema: buildSchema(),
    source: query,
    contextValue: ctx,
  });
}

const ctx = createContext("u_1", "t_1", "trace_1");
const result = await run(`{ viewer { id posts(first: 2) { nodes { title } } } }`, ctx);
if (result.errors?.length) {
  throw new Error(result.errors.map((e) => e.message).join("; "));
}
```

Because loaders live in the context, unit tests get batching for free: construct a fresh context per call and the N+1 assertion becomes a count on the fake repository.