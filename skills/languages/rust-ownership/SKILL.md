---
name: Rust Ownership and Safety
description: Covers ownership and borrowing rules, lifetime elision and explicit annotations, traits and generics, error handling with Result and the question-mark operator, Option versus Result, interior mutability, Box Rc and Arc smart pointers, Send and Sync, borrow-checker-friendly patterns, and how to read common compiler errors. Use when writing or debugging Rust, when hitting a borrow checker error, choosing between Rc Arc RefCell Mutex or Box, designing an API, or when structuring code to avoid fighting the checker.
metadata:
  opencode/autoinvoke: true
---

# Rust Ownership and Safety

Rust's ownership system is a static proof that memory is either uniquely owned, shared
immutably any number of times, or shared mutably exactly once. The mental model that makes
it click: **every value has exactly one owner; when the owner goes out of scope the value
is dropped; references may not outlive what they refer to.** Most "borrow checker
errors" are the compiler failing to prove that second rule holds.

## When to use this

- The user is writing Rust and hitting `E0382` (use of moved value), `E0502` (cannot
  borrow as mutable because also borrowed as immutable), `E0597` (does not live long
  enough), or `E0499` (cannot borrow as mutable more than once).
- The user asks about lifetimes: elision rules, `'static`, struct lifetimes, why a function
  needs an annotation.
- The user is choosing between `Box`, `Rc`, `Arc`, `RefCell`, `Mutex`, `Cell`.
- The user is designing a trait, generic function, or public API and asks about
  `dyn` versus generics.
- The user is handling errors and asks about `Result`, `Option`, `?`, `From`, or panics.
- The user needs to move data across threads and asks what `Send`/`Sync` mean.
- The user is restructuring code to make the borrow checker happy rather than reaching for
  `Rc<RefCell<...>>`.

## Quick start

Ownership transfer, borrowing, and error propagation in one pass:

```rust
use std::collections::HashMap;
use std::fmt;
use std::num::ParseIntError;

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct UserId(String);

impl fmt::Display for UserId {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.0)
    }
}

impl std::str::FromStr for UserId {
    type Err = ParseIntError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        // Validate, then build. Returning Self is what makes ? work downstream.
        s.parse::<u64>()?;
        Ok(UserId(s.to_owned()))
    }
}

#[derive(Debug)]
enum AppError {
    UnknownUser { id: UserId },
    Corrupt(String),
}

impl fmt::Display for AppError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AppError::UnknownUser { id } => write!(f, "unknown user {id}"),
            AppError::Corrupt(msg) => write!(f, "corrupt input: {msg}"),
        }
    }
}

impl std::error::Error for AppError {}

struct Registry {
    users: HashMap<UserId, String>,
}

impl Registry {
    fn new() -> Self {
        Registry { users: HashMap::new() }
    }

    fn insert(&mut self, id: UserId, name: String) {
        self.users.insert(id, name);
    }

    // Two immutable borrows of `self.users` coexist here; that is fine.
    fn name_of(&self, id: &UserId) -> Option<&str> {
        self.users.get(id).map(String::as_str)
    }

    // `&mut self` and `&UserId` do not overlap, so this compiles.
    fn rename(&mut self, id: &UserId, name: String) {
        if let Some(slot) = self.users.get_mut(id) {
            *slot = name;
        }
    }

    fn require_name(&self, id: &UserId) -> Result<&str, AppError> {
        self.name_of(id)
            .ok_or_else(|| AppError::UnknownUser { id: id.clone() })
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut registry = Registry::new();
    registry.insert(UserId("u1".into()), "Ada".into());

    let id = UserId("u1".into());
    let name = registry.require_name(&id)?;   // `?` converts and returns on error
    println!("{id} is {name}");

    // `registry` can still be borrowed mutably: `name` borrows it immutably,
    // so `name` must not be used after this point.
    registry.rename(&id, "Ada Lovelace".into());
    println!("{}", registry.require_name(&id)?);

    Ok(())
}
```

## Ownership rules

| Type | Rule |
| --- | --- |
| Owned value (`String`, `Vec<T>`, `Box<T>`) | one owner; assigning or passing **moves** it unless the type is `Copy` |
| `&T` | any number of shared borrows, may not mutate |
| `&mut T` | exactly one mutable borrow, no other borrows alive |
| Reference | may not outlive the referent |

```rust
let s1 = String::from("hello");
let s2 = s1;                 // MOVE: s1 is now invalid
// println!("{s1}");        // error[E0382]: borrow of moved value: `s1`

let s3 = s2.clone();         // explicit deep copy
let s4 = s2;                 // fine: s3 and s4 are independent

let n1 = 10;
let n2 = n1;                 // COPY: integers implement Copy, n1 still valid
```

`Copy` is derived for small, trivially copyable types (integers, floats, `bool`, `char`,
`&T`, `&[T]` via shared references, arrays of `Copy`, and your own types when you
`#[derive(Copy, Clone)]`). Types owning heap memory (`String`, `Vec`, `Box`, `HashMap`) are
never `Copy`.

### Partial moves

```rust
struct User {
    id: String,
    name: String,
}

let u = User { id: "1".into(), name: "Ada".into() };
let id = u.id;               // moves just the `id` field out
// println!("{:?}", u);      // ERROR: partial move of `u`
println!("{id}");            // fine: `id` is a String now
```

Destructuring moves the named fields and leaves the rest alone. Destructuring a value that
has already been partially moved fails, so do one or the other:

```rust
let u = User { id: "1".into(), name: "Ada".into() };
let User { id, name } = u;  // moves both fields out; `u` is now unusable
// `..` ignores the rest without moving it: let User { id, .. } = other;
```

When you need the whole value after moving a field, destructure up front or borrow
instead of moving.

## Borrowing and how to stop fighting the checker

The compiler computes a set of live borrows. It only complains when a live borrow's
requirements conflict. Common fixes, in order of preference:

**1. Narrow the borrow scope with a block.**

```rust
// BAD: the immutable borrow of `input` lives through the statement that
// calls the &mut method.
let first = &input[0];
normalize(&mut input);
println!("{first}");          // ERROR[E0502]

// GOOD: the borrow ends at the end of the block.
let first = {
    let r = &input[0];
    r.to_uppercase()
};                             // borrow of `input` ends here
normalize(&mut input);
println!("{first}");
```

The general fix: derive an owned value (or copy) from the borrow, then end the borrow.

**2. Split the borrow of a container.**

```rust
let mut users = vec![String::from("ada"), String::from("alan")];

// BAD: &mut users borrows the whole Vec, conflicting with &users[0].
let first = &users[0];
users[0].make_ascii_uppercase();   // ERROR[E0502]

// GOOD: split_at_mut gives two disjoint &mut slices.
let (head, tail) = users.split_at_mut(1);
head[0].make_ascii_uppercase();
// `tail` is unaffected
```

Related tools: `slice::split_at_mut`, `split_first_mut`, `HashMap::get_mut`, `Vec::iter_mut`
(zipped against an index), and `slice::get_disjoint_mut` (newer; verify availability).

**3. Move the value out temporarily with `take`, `replace`, or `mem::take`.**

```rust
use std::mem;

struct State {
    pending: Option<String>,
}

fn drain(state: &mut State) -> Option<String> {
    // mem::take requires Default, which Option<String> has.
    state.pending.take()
}

fn swap_in(state: &mut State, next: Option<String>) -> Option<String> {
    mem::replace(&mut state.pending, next)
}
```

For `String` and `Vec`, `std::mem::take` requires `Default`; for other types use
`mem::replace(&mut field, Default::default())`.

**4. Iterate by value when you are consuming, by reference when you are observing.**

```rust
for line in input.lines() { /* borrows input */ }
for line in owned.lines() { /* consumes owned, moves each line */ }

let mut it = data.iter();          // immutable, cheap
let mut it = data.iter_mut();      // mutable
let into_it = data.into_iter();    // consumes the collection
```

**5. Restructure the algorithm to a pattern match instead of index mutation.**

```rust
match collection.first_mut() {
    Some(head) => head.push('!'),    // borrow only lasts for the arm
    None => collection.push('!'),
}
```

**6. Return owned values instead of references.**

A function that returns `String` is easier to compose than one returning `&str` borrowed
from an argument, because the caller is not constrained. Return references only when the
caller genuinely wants a zero-copy view, and then write the lifetime explicitly.

## Lifetimes

A lifetime annotation tells the compiler how a reference parameter relates to the returned
reference. It does not change how long anything lives.

### Elision

Three rules, in order:

1. Every elided lifetime in **input** position becomes a distinct lifetime parameter.
2. If there is exactly one input lifetime position (elided or not), that lifetime is
   assigned to every elided **output** lifetime.
3. If the method takes `&self` or `&mut self`, the output lifetime is `self`'s lifetime.

```rust
fn first_word(s: &str) -> &str { &s[..s.find(' ').unwrap_or(s.len())] }
// rule 2: the one input lifetime becomes the output lifetime. OK.

// Rule 1 would make this ambiguous, so an annotation is required:
fn longer<'a>(a: &'a str, b: &a str) -> &'a str { if a.len() > b.len() { a } else { b } }

// Rule 3 saves the common method case:
struct Doc { body: String }

impl Doc {
    fn body(&self) -> &str { &self.body }   // return lifetime is self's
}
```

### Explicit lifetimes you actually write

```rust
// A struct that holds a borrowed value must say so.
struct Excerpt<'a> {
    part: &'a str,
}

// Two references into the SAME buffer, so you can split a string at an index.
fn split_at(s: &str, idx: usize) -> Result<(&str, &str), AppError> {
    s.split_at(idx)
        .map(|(a, b)| (a, b))
        .map_err(|_| AppError::Corrupt("index not on a char boundary".into()))
}

// 'static on a function return means: valid for the whole program.
// Only literals and compile-time constants satisfy this without other constraints.
fn config() -> &'static str { "production" }
```

`'static` is *not* "lives forever by magic"; it means "this reference has no shorter
lifetime than the program", and the compiler will only accept it when that is provable. A
`&'static str` obtained from a `String` leak (`Box::leak`) does satisfy it, at the cost of
the allocation never being freed.

### When to write `'static`

- Function parameters that must outlive the call (spawning a thread, storing in a
  `'static` task).
- `Box<dyn Trait + 'static>` trait objects handed to storage.
- `thread::spawn` closures: `'static` by default; scoped threads exist if you need to
  borrow, check availability in your version.

```rust
use std::thread;

fn spawn_worker(mut rx: std::sync::mpsc::Receiver<u32>) {
    thread::spawn(move || {
        for value in rx {
            println!("got {value}");
        }
    });
}
```

The `move` closure is required because the thread outlives the function; the closure then
owns `rx`.

## Traits and generics

```rust
use std::fmt::Debug;

pub trait Repository {
    type Id;                        // associated type: one per implementor

    fn get(&self, id: &Self::Id) -> Option<String>;

    // A default method can call the required ones.
    fn get_or_default(&self, id: &Self::Id) -> String {
        self.get(id).unwrap_or_else(|| "unknown".to_owned())
    }
}

impl Repository for HashMap<u32, String> {
    type Id = u32;

    fn get(&self, id: &Self::Id) -> Option<String> {
        HashMap::get(self, id).cloned()
    }
}

// Generic function: monomorphised, zero dispatch cost.
fn describe<R: Repository>(repo: &R, id: &R::Id) -> String {
    format!("{id:?} -> {}", repo.get_or_default(id))
}

// Dynamic dispatch: one pointer indirection, runtime flexibility.
fn describe_dyn(repo: &dyn Repository<u32>, id: &u32) -> String {
    repo.get_or_default(id)
}
```

Tradeoffs:

| | Generic `fn f<T: Trait>` | `&dyn Trait` |
| --- | --- | --- |
| Dispatch | static, inlined | dynamic, vtable |
| Code size | one copy per concrete type | one copy of `f` |
| Requires | the concrete type be known | only the trait |
| Accepts heterogeneous collections | no (one `T` per call, though `impl Trait` in argument position accepts one) | yes |
| Object safety | not required | the trait must be object safe (no generics, no `Self` in some positions) |

Both are used together: generics for hot inner code, `dyn` for plugin boundaries and
heterogeneous collections.

```rust
use std::fmt::Debug;

fn log_all(items: &[Box<dyn Debug>]) {
    for item in items {
        println!("{item:?}");
    }
}

fn sum_all<I>(items: I) -> i64
where
    I: IntoIterator<Item = i64>,
{
    items.into_iter().sum()
}

// impl Trait in argument position is sugar for a generic parameter.
fn print_it(item: &impl Debug) { println!("{item:?}"); }

// impl Trait in return position hides the concrete type from callers.
fn make_parser() -> impl Fn(&str) -> usize {
    |s| s.len()
}
```

Other things worth knowing:

- **Blanket impls**: `impl<T: Display> ToString for T` in the standard library. You can
  write `impl<T: MyTrait> OtherTrait for T` only if you do not violate the orphan rule.
- **Orphan rule**: you may implement a trait for a type only if the trait or the type is
  local to your crate. A newtype wrapper (`struct MyVec(Vec<T>)`) is the standard escape.
- **Supertraits** express "must also implement":
  `trait Save: fmt::Debug + Clone {}`.
- **Marker traits** (`Copy`, `Send`, `Sync`, `Sized`) carry a constraint and no methods.
- **Sealed traits** prevent external implementation by having a private supertrait.

```rust
use std::fmt;

// Sealed: only types in this module can implement Summary.
mod private {
    pub trait Sealed {}
}

pub trait Summary: private::Sealed {
    fn summary(&self) -> String;
}

pub struct Article;

impl private::Sealed for Article {}
impl Summary for Article {
    fn summary(&self) -> String { format!("(Read more from {}...)", self) }
}
```

## Errors: Result and `?`

```rust
use std::fs;
use std::num::ParseIntError;

// 1. Concrete error type. Best for libraries: callers can match exhaustively.
#[derive(Debug)]
pub enum ConfigError {
    Io(std::io::Error),
    Parse { key: String, source: ParseIntError },
    Missing(String),
}

impl std::fmt::Display for ConfigError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ConfigError::Io(e) => write!(f, "io error: {e}"),
            ConfigError::Parse { key, source } => write!(f, "bad value for {key}: {source}"),
            ConfigError::Missing(k) => write!(f, "missing key: {k}"),
        }
    }
}

impl std::error::Error for ConfigError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            ConfigError::Io(e) => Some(e),
            ConfigError::Parse { source, .. } => Some(source),
            ConfigError::Missing(_) => None,
        }
    }
}

// 2. `?` converts via From and returns early on Err.
impl From<std::io::Error> for ConfigError {
    fn from(e: std::io::Error) -> Self { ConfigError::Io(e) }
}

pub fn read_port(path: &str) -> Result<u16, ConfigError> {
    let text = fs::read_to_string(path)?;               // io::Error -> ConfigError
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return Err(ConfigError::Missing("port".into()));
    }
    Ok(trimmed.parse::<u16>().map_err(|source| ConfigError::Parse {
        key: "port".into(),
        source,
    })?)
}
```

`?` semantics: on `Err(e)`, apply `From::from` to convert `e` to the function's error
type, then return `Err(converted)`. On `Ok(v)`, unwrap to `v` and continue.

Application code typically uses a boxed dynamic error to avoid declaring a type per
function. The common crates are `thiserror` (derive error enums) and `anyhow` (opaque
`anyhow::Error` with `.context()`); both are widely used and stable, and their derive
macros are documented on their own sites. Verify current API details on
docs.rs before pinning a dependency.

```rust
use std::num::ParseIntError;
use std::str::FromStr;

fn parse_pair(a: &str, b: &str) -> Result<(u32, u32), ParseIntError> {
    Ok((FromStr::from_str(a)?, u32::from_str(b)?))
}
```

### Option versus Result

| | `Option<T>` | `Result<T, E>` |
| --- | --- | --- |
| Meaning | absence; `.None()` | a value or an explanation |
| No-`Err` form | `.unwrap()`, `.expect("msg")`, `unwrap_or`, `unwrap_or_else`, `unwrap_or_default` | `.unwrap_err()`, `map_err`, `ok_or`, `ok_or_else` |
| Convert to Option | `.ok()` | yes |
| Convert to Result | `.ok_or(Err(...))` | — |

```rust
let port: Option<u16> = args.next().and_then(|s| s.parse().ok());
let port = port.unwrap_or(8080);

// ok_or_else defers the error construction until it is actually needed.
let user = lookup(id).ok_or_else(|| AppError::UnknownUser { id: id.clone() })?;

// Combinators that keep you out of matches:
let total: u32 = rows.iter().filter_map(|r| r.amount).sum();
let names: Vec<String> = users.iter().map(|u| u.name.clone()).collect();
```

`unwrap()` in library code is a policy decision; `expect("invariant: message")` at least
records why the panic is acceptable. Both panic. Returning `Result` from anything a caller
might not expect to fail is the better default for public APIs.

### Panic, unwind, and catch_unwind

- Panic for programmer errors: indexing out of bounds, `unreachable!()` for a
  logically-impossible branch, `assert!` for internal invariants.
- Return `Result` for anything caused by input, IO, or environment.
- Panics unwind by default; a panic in a thread that is not caught terminates that thread,
  and a panic on the main thread terminates the process.
- `std::panic::catch_unwind` catches panics on the current thread. It does not catch a
  panic in another thread, and it does not catch aborts. Use it at a hard boundary
  (a plugin host, an FFI shim), never as ordinary control flow.

```rust
use std::panic::{catch_unwind, AssertUnwindSafe};

fn risky() -> Result<u32, String> {
    catch_unwind(AssertUnwindSafe(|| {
        let v: Vec<u32> = Vec::new();
        v[3]                    // panics
    }))
    .map_err(|_| "worker panicked".to_string())
}
```

## Interior mutability

Mutability through a shared reference, checked at runtime instead of compile time.

| Type | Cell | Use for |
| --- | --- | --- |
| `Cell<T>` | no | small `Copy` values; `get`, `set`, `replace`, `take` |
| `RefCell<T>` | borrow-checked at runtime | a value needing `&mut` through `&self`; `borrow`, `borrow_mut` |
| `Mutex<T>` | thread-safe, blocking | shared mutable state across threads |
| `RwLock<T>` | thread-safe, many readers | read-heavy shared state |
| `OnceCell<T>` / `OnceLock<T>` | write-once | lazy statics without `lazy_static` |
| `Cow<'a, T>` | copy-on-write | "borrowed if possible, owned if needed" |

```rust
use std::cell::{Cell, RefCell};
use std::sync::atomic::{AtomicU64, Ordering};

struct Counter {
    hits: Cell<u64>,                       // cheap: no borrow checks, Copy value
    names: RefCell<Vec<String>>,           // runtime borrow checks
}

impl Counter {
    fn new() -> Self {
        Counter { hits: Cell::new(0), names: RefCell::new(Vec::new()) }
    }

    fn record(&self, name: &str) {
        self.hits.set(self.hits.get() + 1);
        self.names.borrow_mut().push(name.to_owned());
    }
}
```

`RefCell` moves the compiler's error to a panic at runtime:
`already borrowed: BorrowMutError`. That is a correctness mechanism, not an optimisation
tradeoff; use it when you need mutation through a shared reference and the shape is small
and local.

For shared mutable state across threads:

```rust
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

let requests = AtomicU64::new(0);
requests.fetch_add(1, Ordering::Relaxed);

let cache = Mutex::new(std::collections::HashMap::new());
{
    let mut guard = cache.lock().expect("cache mutex poisoned");
    guard.insert("k".to_owned(), 1);
}
```

Always handle lock poisoning: `.lock()` returns a `Result`. A poisoned mutex means a
previous holder panicked; recovering from it (with `.into_inner()` on `PoisonError`, or
`get_mut` when you hold it exclusively) is usually correct for data caches, and never
correct for invariants. Prefer `Ordering::Relaxed` for counters and stronger orderings only
where you actually publish data.

## Smart pointers

| Pointer | Ownership | Count | Thread safe |
| --- | --- | --- | --- |
| `Box<T>` | unique, heap | 1 | depends on `T` |
| `Rc<T>` | shared | many | no (`!Send`, `!Sync`) |
| `Arc<T>` | shared | many | yes, if `T: Send + Sync` |
| `Weak<T>` | observer, does not keep alive | - | pairs with `Arc` |

```rust
use std::rc::Rc;
use std::sync::Arc;
use std::sync::atomic::{AtomicU32, Ordering};

// Box: a large value, a recursive type, or a trait object.
enum Tree {
    Leaf(i32),
    Node(Box<Tree>, Box<Tree>),
}

// Rc: shared within one thread. RefCell for mutation through the shared handle.
struct Node {
    value: i32,
    parent: RefCell<Weak<Node>>,
    children: RefCell<Vec<Rc<Node>>>,
}

// Arc: shared across threads.
let shared = Arc::new(AtomicU32::new(0));
let clone = Arc::clone(&shared);
std::thread::spawn(move || {
    clone.fetch_add(1, Ordering::SeqCst);
}).join().unwrap();

// Weak breaks the cycle: a parent must not keep its children alive forever.
let parent = Rc::downgrade(&node);
match parent.upgrade() {
    Some(p) => { /* parent is still alive */ }
    None => { /* parent has been dropped */ }
}
```

Memory cycles through `Rc` (or `Arc`) leak: if A holds an `Rc` to B and B holds an `Rc`
to A, neither is ever freed. Break them with `Weak` on the back-reference. This is the same
problem as a reference cycle in a garbage-collected language.

`Rc::make_mut` gives copy-on-write semantics: clone-on-write only when there is more than
one strong reference.

## Send and Sync

- `Send`: safe to move the value to another thread.
- `Sync`: safe to share `&T` across threads (equivalently, `T: Sync` means `&T: Send`).

Most types are both. The notable exceptions: `Rc` is neither (single-threaded refcount);
`RefCell` is `Send` only if its contents are, and never `Sync` (its borrow flag is not
atomic); `Mutex<T>` is `Send`/`Sync` when `T: Send`; raw pointers are neither unless you
`unsafe impl` them. `Cell<T>` is `Send` when `T: Send` and `Sync` when `T: Send` (because
you can write through `&Cell<T>`).

These bounds are what make `std::thread::spawn` and channel APIs safe: the compiler rejects
a closure that captures an `Rc` before the program runs. If you hit "the trait `Send` is not
implemented", the correct fix is almost always to send owned data or `Arc`-wrapped data,
never an `unsafe impl Send`.

## Common compiler errors and how to read them

| Code | Message | Root cause | Fix |
| --- | --- | --- | --- |
| E0382 | "borrow of moved value: `x`" | ownership moved, original unusable | clone, borrow, or restructure |
| E0499 | "cannot borrow `x` as mutable more than once at a time" | two overlapping `&mut` | scope one, or `split_at_mut` |
| E0502 | "cannot borrow `x` as mutable because it is also borrowed as immutable" | a read borrow is still live | end the read borrow in a block, or derive an owned value |
| E0597 | "`x` does not live long enough" | a reference outlives its owner | shorten the reference, or tie lifetimes with an annotation |
| E0515 | "cannot return value referencing temporary" | returned a reference to a local | return an owned value |
| E0716 | "temporary value dropped while borrowed" | `&String` into a `&str` returned from a block | bind to a variable first |
| E0607 | "invalid reference to packed field" | taking a reference to a `#[repr(packed)]` field | copy the field, or use unaligned reads |
| E0277 | "the trait bound `T: Display` is not satisfied" | a generic constraint is missing | add the bound to the signature |
| E0038 | "object safety" errors | using `dyn` on a trait with generics or `Self` returns | change the trait, or use generics |
| E0507 | "cannot move out of borrowed content" | moving out of a reference | clone or `to_owned()` |

Non-lexical lifetimes (stable for many releases) mean a borrow ends at its *last use*, not
at the end of its scope. If you get an error involving a borrow that you believe is
finished, the compiler found a later use — usually inside a closure, a `return`, or a
value that escapes the block. Re-read and find the last use.

## Checklist

- [ ] Prefer `&T` over `&mut T`, and borrowing over cloning.
- [ ] Function signatures state ownership intent: `&str`/`&[T]` for readers, `String`/
      `Vec<T>` for takers, `&mut T` for in-place mutation.
- [ ] Public API functions return `Result` for anything that can fail for non-programmer
      reasons.
- [ ] Errors are typed and implement `Display` + `Error` (with `source()` where a cause
      exists).
- [ ] Borrow-heavy code uses blocks to end borrows, `split_at_mut` for containers, and
      `mem::take`/`replace` to move values out temporarily.
- [ ] Lifetimes are annotated only where elision is insufficient, with a comment
      explaining the relationship.
- [ ] Shared state uses `Arc` + `Mutex` (or `RwLock`), never `Rc` across threads.
- [ ] `Rc`/`Arc` cycles are broken with `Weak`.
- [ ] `RefCell` usage keeps the borrow short and never holds two `borrow_mut` at once.
- [ ] `cargo clippy` and `cargo fmt --check` run in CI.
- [ ] No `unwrap`/`expect` in library paths unless the panic is genuinely a programmer
      error and documented.

## Common mistakes

**`E0502` on a line after the borrow looks finished.** Something still uses it: the
returned value, a `println!` later, or a closure. Find the last use; that is where the
borrow really ends.

**`E0597: does not live long enough` when returning `&str` from a struct field while
building the struct.** Return an owned `String`, or restructure so the source outlives the
caller.

**Cloning to silence E0382 without asking why.** Sometimes the data genuinely must be
shared. Often the design wants an index, an id, or a narrower function signature.

**`Rc<RefCell<T>>` used as a universal escape hatch.** It defeats every compile-time check
inside `T` and moves the panic to runtime. If a function signature can avoid it, do that
first.

**Holding a `MutexGuard` across `.await` or across a call that might re-enter.** The lock
is held for the whole suspension, and a re-entrant `lock()` deadlocks (std `Mutex` is not
reentrant). Scope the guard tightly.

**`unwrap()` on user input.** Parse errors are expected; return `Result` or `Option`.

**Passing `&Vec<T>` instead of `&[T]`.** The slice accepts any contiguous sequence; the
`Vec` reference only accepts a `Vec`.

**`impl Trait` in return position when you need to name the type later.** It is opaque to
callers; use a named struct.

## Anti-patterns

- **Fighting the borrow checker with clones or `Arc<Mutex<...>>` instead of restructuring.**
  Each restructure makes the ownership model clearer; the escape hatch hides a design
  question.
- **Global mutable state** (`static mut`, `lazy_static` for anything but truly immutable
  config). It makes reasoning about the borrow order impossible and is unsafe.
- **`unsafe` to shorten a lifetime** rather than fixing the signature. Every `unsafe` block
  is a promise the compiler stops checking.
- **Panicking on recoverable input errors.**
- **Deeply nested generic bounds** like `T: IntoIterator<Item = U>, U: AsRef<str>`,
  where a small custom trait would read better.
- **A `Vec<T>` where a slice, array, or iterator would do**, allocating to pass data that
  is not owned by the receiver.
- **Public traits that cannot evolve.** Every added method is a breaking change for
  implementors; prefer `#[non_exhaustive]` on enums, or a sealed trait.
- **`unwrap_or_else(|| expensive())` written as `unwrap_or(expensive())`**, evaluating the
  default eagerly every call.
- **Silent `.ok()` on a `Result`**, discarding a real failure without logging.
- **Trusting `clone()` to be cheap** on `Arc` or `Rc` (cheap) versus on the value inside
  (not cheap). Be explicit about which one you mean.

## References

- The Rust Programming Language (the Book)
  - https://doc.rust-lang.org/book/ch04-01-what-is-ownership.html
  - https://doc.rust-lang.org/book/ch04-02-references-and-borrowing.html
  - https://doc.rust-lang.org/book/ch10-03-lifetime-syntax.html
  - https://doc.rust-lang.org/book/ch10-02-traits.html
  - https://doc.rust-lang.org/book/ch11-02-writing-tests.html
  - https://doc.rust-lang.org/book/ch09-02-recoverable-errors-with-result.html
  - https://doc.rust-lang.org/book/ch15-05-interior-mutability.html
  - https://doc.rust-lang.org/book/ch15-06-rustfmt-and-clippy.html
  - https://doc.rust-lang.org/book/ch17-02-concurrent-programming.html
- Standard library
  - https://doc.rust-lang.org/std/primitive.slice.html
  - https://doc.rust-lang.org/std/primitive.str.html
  - https://doc.rust-lang.org/std/result/enum.Result.html
  - https://doc.rust-lang.org/std/option/enum.Option.html
  - https://doc.rust-lang.org/std/cell/index.html
  - https://doc.rust-lang.org/std/sync/struct.Mutex.html
  - https://doc.rust-lang.org/std/sync/struct.RwLock.html
  - https://doc.rust-lang.org/std/rc/struct.Rc.html
  - https://doc.rust-lang.org/std/sync/struct.Arc.html
  - https://doc.rust-lang.org/std/sync/struct.Weak.html
  - https://doc.rust-lang.org/std/marker/trait.Send.html
  - https://doc.rust-lang.org/std/marker/trait.Sync.html
  - https://doc.rust-lang.org/std/mem/index.html
  - https://doc.rust-lang.org/std/error/trait.Error.html
  - https://doc.rust-lang.org/std/panic/index.html
- Tools
  - https://doc.rust-lang.org/clippy/
  - https://doc.rust-lang.org/cargo/
- Community
  - https://rust-lang.github.io/api-guidelines/
  - https://docs.rs/thiserror/ and https://docs.rs/anyhow/ (for the two error crates)

For deeper reading:

- `references/borrow-checker-playbook.md` - step-by-step strategies for restructuring
  code, linked-list and tree patterns, closure capture rules, and testing invariants.
- `references/patterns-and-errors.md` - newtype and typestate patterns, iterator
  adaptation, error design for public APIs, `?` conversions, panic boundaries.