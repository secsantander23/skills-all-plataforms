# Writing a custom ESLint rule

Write one when a bug pattern recurs in review and no built-in rule covers it. Do not
write one for style, and do not write one you cannot test.

- Custom rules tutorial: https://eslint.org/docs/latest/extend/custom-rule-tutorial
- Custom rules reference: https://eslint.org/docs/latest/extend/custom-rules

## When it is justified

| Situation | Write a rule |
| --- | --- |
| A specific bug class keeps reaching review | Yes |
| A codebase convention that a built-in rule cannot express | Yes, if it is mechanical |
| A style preference the formatter cannot handle | Usually not |
| A one-off concern in one file | No, a comment in review is enough |

The bar: the rule must be mechanically decidable. If it needs judgement about intent, it
belongs in review, not in a linter.

## A worked example

Flag an `await` inside a loop over a literal array large enough that a sequential loop
would be a performance bug.

```typescript
// eslint-rules/no-floating-await.ts
import type { TSESLint, TSESTree } from "typescript-eslint";

type MessageIds = "unboundedAwait";
type Options = [{ maxConcurrency?: number }];

const rule: TSESLint.RuleModule<MessageIds, Options> = {
  meta: {
    type: "problem",
    docs: {
      description: "Disallow awaiting inside a loop over a literal array with no concurrency bound.",
      recommended: false,
    },
    schema: [
      {
        type: "object",
        properties: { maxConcurrency: { type: "integer", minimum: 1 } },
        additionalProperties: false,
      },
    ],
    messages: {
      unboundedAwait:
        "This await is inside a loop over {{count}} items with no concurrency bound. " +
        "Use a concurrency-limited helper instead.",
    },
  },
  defaultOptions: [{ maxConcurrency: 10 }],
  create(context) {
    const maxConcurrency = context.options[0]?.maxConcurrency ?? 10;

    return {
      AwaitExpression(node: TSESTree.AwaitExpression) {
        const loop = enclosingLoop(node);
        if (!loop) return;

        const size = literalArraySize(loop);
        // Unknown size: the rule cannot decide, so it stays quiet.
        if (size === null || size <= maxConcurrency) return;

        context.report({
          node,
          messageId: "unboundedAwait",
          data: { count: String(size) },
        });
      },
    };
  },
};

function enclosingLoop(node: TSESTree.Node): TSESTree.Node | undefined {
  let current: TSESTree.Node | undefined = node.parent;
  while (current) {
    if (
      current.type === "ForStatement" ||
      current.type === "ForOfStatement" ||
      current.type === "WhileStatement" ||
      current.type === "DoWhileStatement"
    ) {
      return current;
    }
    current = current.parent;
  }
  return undefined;
}

/** Returns the number of elements when the loop iterates a literal array, else null. */
function literalArraySize(loop: TSESTree.Node): number | null {
  if (loop.type !== "ForOfStatement") return null;
  const right = loop.right;
  if (right.type !== "ArrayExpression") return null;
  return right.elements.length;
}

export default rule;
```

Design notes:

- Stays silent when it cannot decide. A rule that guesses produces false positives, and
  false positives are what teaches a team to disable a rule.
- Uses a `messageId` with `{{placeholder}}` interpolation so callers can translate and
  tests can assert on a stable identifier.
- Declares a schema so ESLint can validate options and power editor completion.
- No autofix, because there is no single correct rewrite.

## Packaging and wiring

```javascript
// eslint-rules/index.js
import noFloatingAwait from "./no-floating-await.js";

export default {
  rules: {
    "no-floating-await": noFloatingAwait,
  },
};
```

```javascript
// eslint.config.js
import acmeRules from "./eslint-rules/index.js";
import { defineConfig } from "eslint/config";

export default defineConfig([
  {
    files: ["src/**/*.ts"],
    plugins: { acme: { rules: acmeRules.rules } },
    rules: {
      "acme/no-floating-await": ["error", { maxConcurrency: 10 }],
    },
  },
]);
```

For a plugin published for other repositories, package it as `eslint-plugin-<name>` with
a `flat/recommended` export so consumers get parser and rules together. Check the current
plugin authoring documentation for the export shape.

## Testing

Every rule needs a test suite. The four cases are mandatory:

1. **Valid**, several shapes the rule must accept, including the cases where it cannot
   decide.
2. **Invalid**, one per reported construct.
3. **Options**, at least one case proving an option changes behaviour.
4. **Regression**, a case for every bug you have fixed in the rule itself.

```javascript
import { RuleTester } from "eslint";
import rule from "./no-floating-await.js";

const ruleTester = new RuleTester({
  languageOptions: { ecmaVersion: 2022, sourceType: "module" },
});

ruleTester.run("no-floating-await", rule, {
  valid: [
    "for (const a of items) { await handle(a); }",         // unknown size
    "for (const a of [1, 2]) { await handle(a); }",        // under the bound
    "await run();",                                        // not in a loop
  ],
  invalid: [
    {
      code: "for (const a of [1,2,3,4,5,6,7,8,9,10,11]) { await handle(a); }",
      errors: [{ messageId: "unboundedAwait" }],
    },
    {
      code: "for (const a of items.slice(0, 100)) { await handle(a); }",
      // unknown size, so this must be valid; listed here only to document the limit
    },
  ],
});
```

The second invalid case above is intentionally mislabelled in the comment: it belongs in
`valid`. Writing a test file where you have to argue with yourself about placement is the
test suite doing its job.

## Autofix

Only offer a fix when there is one correct rewrite and applying it does not change
behaviour. Test the fix by running the linter twice: the second run must be clean.

A fix that "usually works" is worse than no fix, because it changes code without a human
reading the change.

## Rules of thumb

- Report at the narrowest node that explains the problem.
- Never traverse into another file.
- Never make a network or filesystem call.
- Be conservative: silence beats a false positive.
- Name the rule for the problem, not the solution. `no-floating-await` describes a bug;
  `use-p-map` describes a rewrite.
- Document the rule's false-negative cases. A maintainer needs to know what it does not
  catch.