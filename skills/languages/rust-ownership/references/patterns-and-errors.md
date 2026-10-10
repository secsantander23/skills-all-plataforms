# Patterns and Error Design

## Newtype

A newtype gives you a distinct type with the same representation, so the compiler rejects
mixing up units of the same underlying primitive.

```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
struct Meters(f64);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
struct Feet(f64);

impl Meters {
    fn to_feet(self) -> Feet {
        Feet(self.0 * 3.280839895)
    }

    fn value(self) -> f64 {
        self.0
    }
}

// Meters and Feet cannot be mixed by accident, despite both being a newtype over f64.
```

Validate at construction and keep the invariant absolute:

```rust
use std::num::ParseFloatError;
use std::str::FromStr;

struct Celsius(f64);

impl FromStr for Celsius {
    type Err = ParseFloatError;

    fn from_str(s: &str) -> Result<Self, Self::Err> {
        let raw: f64 = s.trim().parse()?;
        Ok(Celsius(raw))
    }
}

impl Celsius {
    fn to_fahrenheit(self) -> f64 {
        self.0 * 9.0 / 5.0 + 32.0
    }
}
```

## Typestate

Typestate encodes a state machine in the type system so invalid transitions do not
compile. This is worth the extra types only where an invalid state is expensive or
security-relevant.

```rust
use std::marker::PhantomData;

struct Unopened;
struct Opened;

struct Connection<State> {
    socket: Option<std::net::TcpStream>,
    _state: PhantomData<State>,
}

impl Connection<Unopened> {
    fn new(addr: &str) -> Result<Connection<Unopened>, std::io::Error> {
        Ok(Connection { socket: None, _state: PhantomData })
    }

    fn open(mut self) -> Result<Connection<Opened>, std::io::Error> {
        self.socket = Some(std::net::TcpStream::connect(addr)?);
        Ok(Connection { socket: self.socket.take(), _state: PhantomData })
    }
}

impl Connection<Opened> {
    // Only callable on an opened connection: this is the payoff.
    fn send(&mut self, data: &[u8]) -> std::io::Result<usize> {
        use std::io::Write;
        self.socket.as_mut().expect("typestate guarantees a socket").write(data)
    }
}

// conn.send(b"x");   // ERROR: send is not a method on Connection<Unopened>
```

## Iterator adaptation

```rust
use std::sync::mpsc;

// Turning a function returning Option into an iterator.
fn digits(n: u32) -> Option<u32> {
    (0..10).find(|d| n / 10u32.pow(d + 1) == 0).map(|d| n / 10u32.pow(d) % 10)
}

let ds: Vec<u32> = (0..5).filter_map(digits).collect();   // [5, 4, 3, 2, 1]

// Turning a blocking channel into a sync iterator.
struct ReceiverIter<T> {
    rx: mpsc::Receiver<T>,
}

impl<T> Iterator for ReceiverIter<T> {
    type Item = T;

    fn next(&mut self) -> Option<T> {
        self.rx.recv().ok()
    }
}
```

Returning `impl Iterator<Item = T>` from a function keeps the concrete type private and
avoids naming it; callers use `collect`, `for`, etc.

```rust
fn evens(up_to: u32) -> impl Iterator<Item = u32> {
    (0..up_to).filter(|n| n % 2 == 0)
}

let squares: Vec<u32> = evens(10).map(|n| n * n).collect();
```

Custom iterator by implementing `Iterator` with the supplied `next`, or better, by
implementing `next` plus `size_hint` (used by `collect` to pre-allocate). `sum`, `fold`,
`collect`, and `zip` for iterators are the performance tools; a hand-written `for` loop is
usually the clearest and is optimised away in release builds.

## Error design for public APIs

Two-layer approach:

- **Leaf errors** (one per low-level operation) are concrete enums.
- **Library functions** return a boxed error that composes them, or a concrete enum when
  the caller genuinely needs to branch.

```rust
use std::error::Error;
use std::fmt;

#[derive(Debug)]
pub struct Layer {
    context: String,
    source: Box<dyn Error + Send + Sync + 'static>,
}

impl fmt::Display for Layer {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {}", self.context, self.source)
    }
}

impl Error for Layer {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        Some(self.source.as_ref())
    }
}

// A tiny `context` helper. (anyhow does this; shown to make the mechanism explicit.)
pub trait Context<T> {
    fn context(self, msg: impl Into<String>) -> Result<T, Layer>;
}

impl<T, E> Context<T> for Result<T, E>
where
    E: Error + Send + Sync + 'static,
{
    fn context(self, msg: impl Into<String>) -> Result<T, Layer> {
        self.map_err(|e| Layer { context: msg.into(), source: Box::new(e) })
    }
}

fn run() -> Result<(), Layer> {
    let cfg = std::fs::read_to_string("app.toml").context("read app.toml")?;
    parse(&cfg).context("parse app.toml")?;
    Ok(())
}
```

`Send + Sync + 'static` on the boxed source is what lets the error cross thread boundaries,
which is usually required for errors in spawned tasks.

## Conversions and `?`

```rust
use std::num::ParseIntError;

// Manual From conversion: `?` then converts automatically.
#[derive(Debug)]
enum StoreError {
    Parse(ParseIntError),
    Missing,
}

impl From<ParseIntError> for StoreError {
    fn from(e: ParseIntError) -> Self {
        StoreError::Parse(e)
    }
}

fn load(id: &str) -> Result<u32, StoreError> {
    let n = id.parse()?;        // ParseIntError -> StoreError
    if n == 0 {
        return Err(StoreError::Missing);
    }
    Ok(n)
}
```

`map_err` when the conversion is not a simple `From`, or when you want to add data:

```rust
let n = id.parse::<u32>().map_err(|e| StoreError::BadId {
    id: id.to_owned(),
    source: e,
})?;
```

Do not write `map_err(|e| StoreError::Parse(e.to_string()))`; that flattens the error into a
string and loses the ability to inspect the cause.

## Panic boundaries

- `panic!` and its shorthands (`unwrap`, `expect`, indexing, `assert!`) unwind by default.
- `catch_unwind` only catches panics on the calling thread:

```rust
use std::panic::{catch_unwind, AssertUnwindSafe};

fn guarded(f: impl FnOnce()) -> Result<(), String> {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(()) => Ok(()),
        Err(payload) => {
            let msg = payload.downcast_ref::<&str>().map(|s| s.to_string())
                .or_else(|| payload.downcast_ref::<String>().cloned())
                .unwrap_or_else(|| "panic".to_owned());
            Err(msg)
        }
    }
}
```

Use it at a plugin boundary, an FFI shim, or a worker whose death you want to survive.
Do not use it inside a function to "try something".

## Threading notes

```rust
use std::sync::mpsc;
use std::thread;

fn producer(tx: mpsc::Sender<u32>) {
    for i in 0..10 {
        // send can fail if the receiver is gone; handle it rather than panic
        if tx.send(i).is_err() {
            return;
        }
    }
}

fn main() {
    let (tx, rx) = mpsc::channel();
    let handle = thread::spawn(move || producer(tx));

    for v in rx.iter() {
        println!("{v}");
    }
    handle.join().expect("producer panicked");
}
```

`join().unwrap()` propagates a panic in the spawned thread as a panic in the caller, which
is usually what you want. `crossbeam` and other crates add work-stealing and
select-with-multiple-channels; check their current APIs on docs.rs before adopting.

## Verify before relying on this

`get_disjoint_mut`, `slice::get_mut`, task-group equivalents, and some `OnceCell`
conveniences depend on the toolchain version. Check:

- https://doc.rust-lang.org/std/primitive.slice.html
- https://doc.rust-lang.org/std/cell/index.html
- https://doc.rust-lang.org/std/sync/index.html

before depending on them.