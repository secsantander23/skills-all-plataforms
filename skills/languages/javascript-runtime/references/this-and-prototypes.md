# `this` and the Prototype Chain

## How `this` is chosen

At call time, the engine derives `this` from the call expression, in this order:

1. `new C()` -> a freshly allocated object whose prototype is `C.prototype`.
2. `C()` (call on a class without `new`) -> `TypeError: Class constructor C cannot be
   invoked without 'new'`. Class bodies are always strict, so bare calls of *any* function
   declared in a module or class body set `this` to `undefined`.
3. `obj.m(...)` -> `obj`, except when `m` is a bound function, in which case the bound
   value wins.
4. `m(...)` bare -> `undefined` in strict mode, the global object in sloppy mode.
5. `m.call(x)` / `m.apply(x)` / `m.bind(x)` -> `x` (or the bound value).

Arrow functions have no `this` of their own. They close over the `this` of the enclosing
function, permanently. That is the whole feature; it is also why you cannot rebind an
arrow's `this`.

## Losing `this` in callbacks

```js
class Logger {
  constructor() { this.prefix = "app"; }
  log(msg) { console.log(this.prefix, msg); }
}

const logger = new Logger();
["handler"].forEach(logger.log);              // this === undefined -> TypeError
["handler"].forEach(m => logger.log(m));      // explicit receiver, works
["handler"].forEach(logger.log.bind(logger));  // bound, works
```

The arrow class field version, which binds once at construction:

```js
class Logger {
  prefix = "app";
  log = (msg) => console.log(this.prefix, msg);
}
```

Tradeoff: the arrow field is a per-instance function, so it costs one closure per object
and defeats prototype sharing. For a class with thousands of instances, keep the prototype
method and bind where you pass the reference.

## Prototype lookup

Property access walks the chain: own properties, then `[[Prototype]]`, then repeating,
then `null`. Writes shadow, they do not delegate to the prototype.

```js
function Base() {}
Base.prototype.tag = "base";

const b = new Base();
b.tag;                     // "base"
Object.setPrototypeOf(b, { tag: "shadowed" });
b.tag;                     // "shadowed"
b.hasOwnProperty("tag");   // false
```

`Object.create(null)` produces an object with no prototype: no `toString`, no
`hasOwnProperty`, and no inherited keys. It is the right choice for dictionary-style data
where keys are attacker-controlled, because it removes `__proto__` and `constructor` as
attack surface. You still must guard against the literal key `"__proto__"` when copying
keys, because `obj["__proto__"] = x` walks the accessor rather than creating a property.

```js
function safeAssign(target, source) {
  for (const key of Object.keys(source)) {
    if (key === "__proto__") continue;
    target[key] = source[key];
  }
  return target;
}
```

Prefer `Object.create(null)` plus `Map` where you need a real dictionary. A `Map` cannot
be polluted at all and is not affected by property-name rules.

## Class features that are not "just syntax"

- Class declarations are not hoisted in the TDZ-friendly way; they throw if evaluated
  before declaration (unlike function declarations).
- Class bodies are strict mode, always.
- Methods are non-enumerable, unlike object-literal methods. `for...in` and
  `Object.keys` over a class instance skip methods; use `Object.getOwnPropertyNames`.
- Getters/setters and `static` members work as expected.
- `#private` fields and methods are enforced by the engine. `#x in obj` is the supported
  feature-detection form (the older stringified `hasOwnProperty` hack is unnecessary).
- Static blocks run once at class definition time, useful for building memoised helpers.

```js
class Config {
  static #cache = null;
  static load() {
    if (Config.#cache) return Config.#cache;
    Config.#cache = JSON.parse(globalThis.CONFIG_JSON);
    return Config.#cache;
  }
}
```

## Extending built-ins

```js
class MyError extends Error {
  constructor(message, options = {}) {
    super(message, options);   // sets .message and .stack
    this.name = "MyError";
    this.code = options.code;
  }
}

try {
  throw new MyError("nope", { cause: original, code: "E_NOPE" });
} catch (err) {
  err instanceof MyError; // true
  err instanceof Error;   // true
  err.cause === original; // true
}
```

The common bug: forgetting `super(...)`. Symptom is `ReferenceError: Must call super
constructor in derived class before accessing 'this'`. A second common bug is setting
`this.name` in the subclass constructor *before* `super`, which throws for the same reason.

Transpiling classes to ES5 breaks subclassing of built-ins. Target a runtime that
supports native classes if you extend `Error`, `Array`, or `Map`.

## `Reflect` and property descriptors

```js
const obj = {};
Object.defineProperty(obj, "id", { value: 1, enumerable: false, writable: false });
Reflect.set(obj, "id", 2);  // returns false, strict mode caller gets TypeError
Reflect.has(obj, "id");     // true
Reflect.ownKeys(obj);       // ["id"]
```

`Reflect.get(obj, key, receiver)` is the correct way to write a generic getter, because it
honours the receiver and inherited accessors. Plain `obj[key]` does not.

```js
function defineHidden(target, key, value) {
  Object.defineProperty(target, key, { value, enumerable: false });
  return target;
}
```

## Freezing

`Object.freeze` is shallow and irreversible for that object. In strict mode, writing to a
frozen property throws; in sloppy mode the write silently fails. Modules and class bodies
are strict, so it throws there.

```js
Object.freeze({ retries: 3 });        // deep would need each nested object frozen too
Object.freeze(Object.freeze([1, 2, 3]));
```

Use it at trust boundaries: configuration objects parsed from files, and objects handed to
plugins. Deep-freeze recursively if you need the guarantee, but measure: freezing large
object graphs at startup costs time proportional to the graph.