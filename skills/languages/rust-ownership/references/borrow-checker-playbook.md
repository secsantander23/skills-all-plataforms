# Borrow Checker Playbook

## The decision procedure

When you get a borrow error, ask in this order:

1. **Which access is the compiler complaining about?** Read the note: "as mutable
   because it is also borrowed as immutable" points at the *conflicting* borrow; the
   "previous borrow" line points at where the live one started.
2. **Where is the last use of the other borrow?** Not the end of the scope; the last use.
3. **Can the conflicting region be wrapped in a block** so the other borrow ends first?
4. **Can the two accesses be split** (`split_at_mut`, `get_mut`, disjoint keys)?
5. **Can a value be moved out temporarily** (`take`, `replace`, `std::mem::swap`) so the
   container is not borrowed during the operation?
6. **Can the data structure change?** Indexing by key, storing an id instead of a
   reference, or returning owned values often removes the conflict permanently.
7. **Only then**: clone, or wrap in `Rc`/`Arc` + `RefCell`/`Mutex`.

## Working example: the classic E0502

```rust
fn main() {
    let mut data = vec![1, 2, 3];
    let first = &data[0];
    data.push(4);                     // ERROR[E0502]
    println!("{first}");
}
```

Why: `push` may reallocate, which would invalidate `first`, so the compiler refuses.

Fix A - end the borrow:

```rust
let first = data[0];                 // Copy: an owned u32, no borrow at all
data.push(4);
println!("{first}");
```

Fix B - do the read in a scope:

```rust
let first = {
    let r = &data[0];
    *r
};
data.push(4);
println!("{first}");
```

Fix C - if you truly need a reference and a mutation, index with a range
(`&mut data[0] = 4;`) which borrows only one element:

```rust
let mut data = vec![1, 2, 3];
let first = data[0];     // Copy again: prefer this
data[0] = 10;            // single-element borrow
println!("{first}");
```

## Splitting container borrows

```rust
let mut v = vec![String::from("a"), String::from("b")];

// Error: &v[0] and &mut v[1] both cover the whole Vec in older reasoning;
// split_at_mut proves they are disjoint.
let (head, tail) = v.split_at_mut(1);
head[0].push('!');
tail[0].push('?');

// HashMap: one key mutably, another immutably.
let mut m = std::collections::HashMap::new();
m.insert("a".to_string(), 1);
m.insert("b".to_string(), 2);

if let Some(v) = m.get_mut("a") {
    *v += 10;
}
let b = m.get("b").copied();

// get_disjoint_mut for two mutable keys at once; check availability in your version.
if let [Some(a), Some(b)] = m.get_disjoint_mut(["a", "b"]) {
    *a += 1;
    *b += 2;
}
```

## Struct field borrows

The compiler understands disjoint field borrows on a single struct:

```rust
struct Report {
    title: String,
    rows: Vec<Row>,
}

fn retitle(r: &mut Report) {
    let title = &mut r.title;   // borrows only the `title` field
    let rows = &r.rows;         // borrows only the `rows` field: no conflict
    rows.push(Row::default());
    title.push_str(" (updated)");
}
```

But **not** through an index or a method that takes the whole struct:

```rust
fn retitle_via_method(r: &mut Report) {
    let title = &mut r.title;
    r.set_rows(Vec::new());   // ERROR: &mut r overlaps with &mut r.title
    title.push('!');
}
```

Fix: reorder so the whole-struct call comes first, or copy the title.

## Linked lists and trees

The classic owned linked list; the borrow checker enforces that no node aliases another.

```rust
#[derive(Debug)]
struct Node {
    value: i32,
    next: Option<Box<Node>>,
}

impl Node {
    fn len(&self) -> usize {
        1 + self.next.as_ref().map_or(0, |n| n.len())
    }

    fn push_front(&mut self, value: i32) {
        let new = Node { value, next: self.next.take() };
        self.next = Some(Box::new(new));
    }

    fn into_values(self) -> Vec<i32> {
        let mut out = vec![self.value];
        let mut cur = self.next;
        while let Some(node) = cur {
            out.push(node.value);
            cur = node.next;      // moves the Box out, then unwraps it
        }
        out
    }
}
```

`Box` exists so the list is a fixed-size chain of pointers rather than an infinitely sized
recursive value. This is not a workaround; it is the representation.

A binary search tree with an arena or with `Rc<RefCell<...>>` children trades the borrow
checker for runtime borrow checks. Prefer the owned form when the tree is not shared:

```rust
#[derive(Debug, Default)]
struct Bst {
    root: Option<Box<Node2>>,
}

#[derive(Debug, Default)]
struct Node2 {
    value: i32,
    left: Option<Box<Node2>>,
    right: Option<Box<Node2>>,
}

impl Bst {
    fn insert(&mut self, value: i32) {
        fn go(slot: &mut Option<Box<Node2>>, value: i32) {
            let node = slot.get_or_insert_with(|| Box::new(Node2 {
                value,
                ..Default::default()
            }));
            match value.cmp(&node.value) {
                std::cmp::Ordering::Less => go(&mut node.left, value),
                std::cmp::Ordering::Greater => go(&mut node.right, value),
                std::cmp::Ordering::Equal => {}
            }
        }
        go(&mut self.root, value);
    }

    fn contains(&self, value: i32) -> bool {
        let mut cur = self.root.as_ref();
        while let Some(node) = cur {
            match value.cmp(&node.value) {
                std::cmp::Ordering::Less => cur = node.left.as_ref(),
                std::cmp::Ordering::Greater => cur = node.right.as_ref(),
                std::cmp::Ordering::Equal => return true,
            }
        }
        false
    }
}
```

## Closures and capture

A `move` closure takes ownership of what it captures; a non-`move` closure borrows.

```rust
let data = vec![1, 2, 3];
let show = || println!("{:?}", data);     // borrows data
show();
println!("{:?}", data);                   // still fine: the borrow ended

let consume = move || println!("{:?}", data);   // takes ownership
consume();
// println!("{:?}", data);               // ERROR: moved into the closure
```

The subtle case: a `FnMut` closure that mutates while also being iterated.

```rust
let mut names = vec![String::from("a"), String::from("b")];
let mut upper = Vec::new();

// Error if you iterate names while calling the mutable closure.
for name in &names {
    upper.push(name.to_uppercase());
}
```

Solution: split the borrow, or use `drain`/`iter_mut`:

```rust
let mut names = vec![String::from("a"), String::from("b")];
for name in &mut names {
    name.make_ascii_uppercase();          // iter_mut borrows each element mutably
}
let joined = names.join(",");             // names is available again
```

## Iterators and adapters

Adapters that consume vs borrow:

| Adapter | Effect |
| --- | --- |
| `iter()` | `Iterator<Item = &T>`; borrows |
| `iter_mut()` | `Iterator<Item = &mut T>`; mutable borrow |
| `into_iter()` | `Iterator<Item = T>`; consumes the collection |
| `drain(..)` | yields owned items, empties the collection |
| `retain`, `sort`, `sort_by` | mutable borrow of the whole collection |
| `split_at_mut` | two disjoint mutable slices |
| `windows`, `chunks` | non-overlapping or overlapping views |

```rust
let mut v = vec![1, 2, 3, 4, 5];
let evens: Vec<i32> = v.drain(..).filter(|x| x % 2 == 0).collect();
assert_eq!(evens, vec![2, 4]);
assert!(v.is_empty());        // drain emptied it; v is usable again
```

`retain` combined with a filter that borrows something else is the common E0502 source:

```rust
let mut items: Vec<Item> = load();
let threshold = config.threshold;   // owned copy, so it is not a borrow of config
items.retain(|i| i.score >= threshold);   // no conflict
```

## Lifetimes in signatures

Patterns you will write:

```rust
// 1. Two inputs, one output: which input does it borrow from?
fn pick<'a>(a: &'a str, b: &'a str) -> &'a str;

// 2. Two inputs, independent outputs.
fn pair<'a, 'b>(a: &'a str, b: &'b str) -> (&'a str, &'b str);

// 3. Output borrowed from the first input.
fn head_and_tail(s: &str) -> (&str, &str);

// 4. Struct that holds a borrow.
struct Excerpt<'a> { part: &'a str, index: usize }

// 5. Method elision: output lifetime is self's.
impl<'a> Excerpt<'a> {
    fn part(&self) -> &'a str { self.part }
    fn as_str(&self) -> &str { self.part }   // elided, tied to self
}
```

Returning multiple borrows from one input is always fine because they share the input's
lifetime. Returning borrows of *two different* locals is impossible: a reference cannot
outlive its referent, so the compiler must reject it.

## Debugging technique

1. Compile and read the full error, including the "note:" lines that explain which
   borrow is still live and where it was created.
2. Read the *last* use of the conflicting borrow. Non-lexical lifetimes mean the borrow
   ends there; anything the compiler points to later is a genuine use.
3. If the error mentions a closure, remember the closure may outlive the statement.
4. If the error is in generic code, check whether the constraint is missing on the
   signature (`E0277`) rather than a borrow issue.
5. Reduce to a minimal reproduction in a scratch file; borrow errors often disappear once
   you strip the generics, which tells you it is a constraint problem, not a lifetime
   problem.

Run `cargo check` for fast feedback; `cargo clippy` adds suggestions such as suggesting
`first().copied()` when you could avoid a reference entirely.