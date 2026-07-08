use chrono::{DateTime, Datelike, Local, Timelike};
use crossbeam_channel::{Receiver, RecvTimeoutError, Sender};
use log::{LevelFilter, Metadata, Record};
use std::{
    cell::RefCell,
    io::{BufWriter, Write},
    sync::Arc,
    thread::JoinHandle,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

pub use log::{debug, error, info, trace, warn};
pub type Level = LevelFilter;
const CHANNEL_CAPACITY: usize = 65_536;
const DEFAULT_BATCH_SIZE: usize = 32;

// ANSI SGR sequences used when color output is enabled.
const DIM: &str = "\x1b[2m";
const RESET: &str = "\x1b[0m";

/// Set to `true` the first time a coloring color mode (`auto`/`always`) is
/// configured. Gates the (cold) building of colored prefixes in
/// `TimestampCache::refresh` so the default, no-color path does no extra work.
static COLOR_LIVE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
// Bytes reserved per producer batch buffer: DEFAULT_BATCH_SIZE lines * ~128 bytes.
const BATCH_BUF_CAP: usize = 4096;

thread_local! {
    static PRODUCER: RefCell<Producer> = RefCell::new(Producer::new());
}

/// Per-thread producer state: monotonic timestamp source, formatting cache, and
/// the byte buffer that log lines are rendered into before being shipped to the
/// writer thread. Keeping it all in one thread-local means a single TLS lookup
/// and a single borrow on the hot path.
struct Producer {
    ts: ThreadTimestampCache,
    fmt: TimestampCache,
    buf: Vec<u8>,
    count: usize,
    // Scratch for the Python bindings' %-formatting; lives here so the hot
    // path touches a single, already-cached thread-local slot.
    #[cfg(feature = "python")]
    scratch: Vec<u8>,
}

impl Producer {
    fn new() -> Self {
        Self {
            ts: ThreadTimestampCache::new(),
            fmt: TimestampCache::new(),
            buf: Vec::with_capacity(BATCH_BUF_CAP),
            count: 0,
            #[cfg(feature = "python")]
            scratch: Vec::new(),
        }
    }
}

#[derive(Debug, Clone, Copy)]
struct Timestamp {
    secs: u64,
    nanos: u32,
}

/// Monotonic clock read as raw nanoseconds since an arbitrary epoch.
///
/// This is the single per-message time source on the hot path, so it is worth
/// reading the OS clock as directly as possible. `std::time::Instant::now()`
/// wraps the same underlying source but adds ~10ns of timebase conversion and
/// `Duration` construction per call; going straight to the platform primitive
/// roughly halves the cost while keeping full nanosecond precision.
#[cfg(target_os = "macos")]
#[inline(always)]
fn mono_ns() -> u64 {
    // `mach_absolute_time` returns ticks; `mach_timebase_info` gives the
    // ticks->ns ratio (1:1 on Apple Silicon and modern x86_64 Macs). Both live
    // in libSystem, which is always linked.
    #[repr(C)]
    struct MachTimebaseInfo {
        numer: u32,
        denom: u32,
    }
    extern "C" {
        fn mach_absolute_time() -> u64;
        fn mach_timebase_info(info: *mut MachTimebaseInfo) -> std::os::raw::c_int;
    }

    // Cache the timebase ratio once, packed as (numer << 32 | denom) in a
    // single atomic so numer/denom are always read consistently. `0` means
    // "not yet initialized" (mach_timebase_info never returns denom == 0).
    static RATIO: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
    use std::sync::atomic::Ordering::Relaxed;

    let ticks = unsafe { mach_absolute_time() };
    let mut packed = RATIO.load(Relaxed);
    if packed == 0 {
        let mut info = MachTimebaseInfo { numer: 0, denom: 0 };
        unsafe { mach_timebase_info(&mut info) };
        packed = ((info.numer as u64) << 32) | info.denom as u64;
        RATIO.store(packed, Relaxed);
    }
    let numer = (packed >> 32) as u32;
    let denom = packed as u32;
    if numer == denom {
        ticks
    } else {
        ((ticks as u128 * numer as u128) / denom as u128) as u64
    }
}

#[cfg(not(target_os = "macos"))]
#[inline(always)]
fn mono_ns() -> u64 {
    // Portable fallback: same source `Instant` uses, so behavior matches the
    // previous implementation on non-macOS targets.
    use std::sync::OnceLock;
    static START: OnceLock<Instant> = OnceLock::new();
    START.get_or_init(Instant::now).elapsed().as_nanos() as u64
}

struct ThreadTimestampCache {
    base_ticks: u64,
    base_secs: u64,
    base_nanos: u32,
}

impl ThreadTimestampCache {
    fn new() -> Self {
        let ts = now_timestamp();
        Self {
            base_ticks: mono_ns(),
            base_secs: ts.secs,
            base_nanos: ts.nanos,
        }
    }

    #[cold]
    fn refresh(&mut self) -> Timestamp {
        let ts = now_timestamp();
        self.base_ticks = mono_ns();
        self.base_secs = ts.secs;
        self.base_nanos = ts.nanos;
        ts
    }

    #[inline(always)]
    fn now(&mut self) -> Timestamp {
        // Monotonic ns elapsed since the last wall-clock sync.
        let elapsed = mono_ns().wrapping_sub(self.base_ticks);
        if elapsed >= 1_000_000_000 {
            return self.refresh();
        }

        // `base_nanos` < 1e9 and `elapsed` < 1e9, so the sum needs at most a
        // single carry into seconds — no division required.
        let total_nanos = self.base_nanos as u64 + elapsed;
        let (carry, nanos) = if total_nanos >= 1_000_000_000 {
            (1, (total_nanos - 1_000_000_000) as u32)
        } else {
            (0, total_nanos as u32)
        };
        Timestamp {
            secs: self.base_secs + carry,
            nanos,
        }
    }
}

enum Action {
    WriteBytes(Vec<u8>),
    Flush,
    // Flush, then signal the sender so it can rely on the data being on disk.
    FlushSync(Sender<()>),
    Exit,
}

#[derive(Debug)]
struct Context<P: ToString + Send> {
    rx: Receiver<Action>,
    path: Option<P>,
    date: chrono::NaiveDate,
}

pub struct Handle {
    tx: Sender<Action>,
    thread: Option<JoinHandle<()>>,
}

impl Handle {
    pub fn stop(&mut self) {
        if let Some(thread) = self.thread.take() {
            let _ = self.tx.send(Action::Exit);
            let _ = thread.join();
        }
    }
}
impl Drop for Handle {
    fn drop(&mut self) {
        self.stop();
    }
}

struct Logger {
    tx: Sender<Action>,
    name: Option<Arc<str>>,
    unix_ts: bool,
}

impl log::Log for Logger {
    fn enabled(&self, metadata: &Metadata) -> bool {
        metadata.level() <= log::max_level()
    }

    fn log(&self, record: &Record) {
        if !self.enabled(record.metadata()) {
            return;
        }

        emit(
            &self.tx,
            DEFAULT_BATCH_SIZE,
            self.unix_ts,
            false,
            self.name.as_deref(),
            record.level(),
            |buf| {
                let _ = std::io::Write::write_fmt(buf, *record.args());
            },
        );
    }

    fn flush(&self) {
        flush_producer(&self.tx);
        let _ = self.tx.send(Action::Flush);
    }
}

fn open_file(path: &str) -> Result<std::fs::File, std::io::Error> {
    let dir = std::path::Path::new(path);
    if let Some(parent) = dir.parent() {
        std::fs::create_dir_all(parent)?;
    }

    std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)
}

fn rotate<P: ToString + Send>(
    ctx: &Context<P>,
) -> Result<BufWriter<Box<dyn Write>>, std::io::Error> {
    let capacity = 1024 * 1024;
    match &ctx.path {
        Some(path) => {
            let path = {
                let postfix = ctx.date.format("_%Y%m%d").to_string();
                let path_str = path.to_string();
                let input = std::path::Path::new(&path_str);
                let stem = input.file_stem().and_then(|s| s.to_str());
                let ext = input.extension().and_then(|s| s.to_str());
                if let (Some(stem), Some(ext)) = (stem, ext) {
                    let filename = format!("{stem}{postfix}.{ext}");
                    match input.parent() {
                        Some(parent) if !parent.as_os_str().is_empty() => {
                            parent.join(filename).to_string_lossy().to_string()
                        }
                        _ => filename,
                    }
                } else {
                    format!("{}{}.log", path_str, postfix)
                }
            };
            let file = open_file(&path)?;
            Ok(BufWriter::with_capacity(capacity, Box::new(file)))
        }
        None => {
            let target = Box::new(std::io::stdout());
            Ok(BufWriter::with_capacity(capacity, target))
        }
    }
}

fn now_timestamp() -> Timestamp {
    let now = SystemTime::now();
    let since_epoch = now
        .duration_since(UNIX_EPOCH)
        .unwrap_or_else(|err| err.duration());
    Timestamp {
        secs: since_epoch.as_secs(),
        nanos: since_epoch.subsec_nanos(),
    }
}

/// Formatting cache refreshed at most once per wall-clock second. Holds the
/// pre-rendered, second-granularity prefixes so the per-message hot path only
/// appends the sub-second digits.
struct TimestampCache {
    last_secs: u64,
    date: chrono::NaiveDate,
    time_prefix: String,
    offset_prefix: String,
    unix_prefix: String,
    // Colored counterparts, built only when color output is live (COLOR_LIVE).
    // Empty otherwise, so the default path pays nothing.
    time_prefix_colored: String,
    offset_colored: String,
    unix_prefix_colored: String,
}

impl TimestampCache {
    fn new() -> Self {
        Self {
            last_secs: u64::MAX,
            date: chrono::NaiveDate::from_ymd_opt(1970, 1, 1).unwrap(),
            time_prefix: String::new(),
            offset_prefix: String::new(),
            unix_prefix: String::new(),
            time_prefix_colored: String::new(),
            offset_colored: String::new(),
            unix_prefix_colored: String::new(),
        }
    }

    #[inline]
    fn update(&mut self, secs: u64) {
        if self.last_secs == secs {
            return;
        }
        self.refresh(secs);
    }

    #[cold]
    fn refresh(&mut self, secs: u64) {
        let dt: DateTime<Local> = DateTime::from(UNIX_EPOCH + Duration::from_secs(secs));
        self.last_secs = secs;
        self.date = dt.date_naive();

        let offset = dt.offset().local_minus_utc();
        let offset_sign = if offset >= 0 { '+' } else { '-' };
        let offset_abs = offset.abs();
        let offset_h = offset_abs / 3600;
        let offset_m = (offset_abs % 3600) / 60;

        self.time_prefix = format!(
            "time={:04}-{:02}-{:02}T{:02}:{:02}:{:02}.",
            dt.year(),
            dt.month(),
            dt.day(),
            dt.hour(),
            dt.minute(),
            dt.second()
        );
        self.offset_prefix = format!("{offset_sign}{offset_h:02}:{offset_m:02} level=");
        self.unix_prefix = format!("time={secs}.");

        // Colored prefixes reuse the plain ones (no date re-formatting) and are
        // only built when a coloring writer exists. `write_prefix` re-triggers
        // refresh if it finds these empty, so a mid-second flag flip is safe.
        if COLOR_LIVE.load(std::sync::atomic::Ordering::Relaxed) {
            self.time_prefix_colored = format!("{DIM}time={RESET}{}", &self.time_prefix["time=".len()..]);
            let offset_only = &self.offset_prefix[..self.offset_prefix.len() - " level=".len()];
            self.offset_colored = format!("{offset_only} {DIM}level={RESET}");
            self.unix_prefix_colored = format!("{DIM}time={RESET}{}", &self.unix_prefix["time=".len()..]);
        }
    }
}

#[inline]
fn level_str(level: log::Level) -> &'static str {
    match level {
        log::Level::Trace => "trace",
        log::Level::Debug => "debug",
        log::Level::Info => "info",
        log::Level::Warn => "warn",
        log::Level::Error => "error",
    }
}

/// The level token wrapped in a severity color plus a trailing reset, so it
/// can be appended with a single `extend_from_slice`.
#[inline]
fn level_str_colored(level: log::Level) -> &'static str {
    match level {
        log::Level::Trace => "\x1b[2mtrace\x1b[0m",
        log::Level::Debug => "\x1b[36mdebug\x1b[0m",
        log::Level::Info => "\x1b[32minfo\x1b[0m",
        log::Level::Warn => "\x1b[33mwarn\x1b[0m",
        log::Level::Error => "\x1b[31merror\x1b[0m",
    }
}

/// Append `val` to `buf` as exactly `width` ASCII digits, zero-padded. Avoids
/// the `core::fmt` width machinery on the hot path.
#[inline(always)]
fn push_pad(buf: &mut Vec<u8>, mut val: u32, width: usize) {
    let mut tmp = [0u8; 10];
    let mut i = tmp.len();
    loop {
        i -= 1;
        tmp[i] = b'0' + (val % 10) as u8;
        val /= 10;
        if val == 0 {
            break;
        }
    }
    let ndigits = tmp.len() - i;
    for _ in ndigits..width {
        buf.push(b'0');
    }
    buf.extend_from_slice(&tmp[i..]);
}

#[inline(always)]
fn write_prefix(buf: &mut Vec<u8>, fmt: &mut TimestampCache, ts: Timestamp, level: log::Level, unix_ts: bool, color: bool) {
    if color {
        // A producer thread may have refreshed this second before a coloring
        // writer was registered; rebuild the colored prefixes if so.
        if fmt.time_prefix_colored.is_empty() {
            fmt.refresh(ts.secs);
        }
        let level = level_str_colored(level);
        if unix_ts {
            buf.extend_from_slice(fmt.unix_prefix_colored.as_bytes());
            push_pad(buf, ts.nanos, 9);
            buf.extend_from_slice(b" ");
            buf.extend_from_slice(DIM.as_bytes());
            buf.extend_from_slice(b"level=");
            buf.extend_from_slice(RESET.as_bytes());
            buf.extend_from_slice(level.as_bytes());
        } else {
            buf.extend_from_slice(fmt.time_prefix_colored.as_bytes());
            push_pad(buf, ts.nanos / 1_000, 6);
            buf.extend_from_slice(fmt.offset_colored.as_bytes());
            buf.extend_from_slice(level.as_bytes());
        }
        return;
    }
    let level = level_str(level);
    if unix_ts {
        buf.extend_from_slice(fmt.unix_prefix.as_bytes());
        push_pad(buf, ts.nanos, 9);
        buf.extend_from_slice(b" level=");
        buf.extend_from_slice(level.as_bytes());
    } else {
        buf.extend_from_slice(fmt.time_prefix.as_bytes());
        push_pad(buf, ts.nanos / 1_000, 6);
        buf.extend_from_slice(fmt.offset_prefix.as_bytes());
        buf.extend_from_slice(level.as_bytes());
    }
}

/// Render one log line into the calling thread's buffer and, once a full batch
/// has accumulated, ship the raw bytes to the writer thread. `write_msg` writes
/// the message body directly into the buffer, avoiding any intermediate copy.
#[inline]
fn emit<F: FnOnce(&mut Vec<u8>)>(
    tx: &Sender<Action>,
    batch_size: usize,
    unix_ts: bool,
    color: bool,
    name: Option<&str>,
    level: log::Level,
    write_msg: F,
) {
    PRODUCER.with(|producer| {
        let mut producer = producer.borrow_mut();
        let ts = producer.ts.now();
        let Producer { fmt, buf, count, .. } = &mut *producer;
        fmt.update(ts.secs);

        write_prefix(buf, fmt, ts, level, unix_ts, color);
        if let Some(name) = name {
            if color {
                buf.extend_from_slice(b" ");
                buf.extend_from_slice(DIM.as_bytes());
                buf.extend_from_slice(b"name=");
                buf.extend_from_slice(RESET.as_bytes());
            } else {
                buf.extend_from_slice(b" name=");
            }
            buf.extend_from_slice(name.as_bytes());
        }
        if color {
            buf.extend_from_slice(b" ");
            buf.extend_from_slice(DIM.as_bytes());
            buf.extend_from_slice(b"msg=");
            buf.extend_from_slice(RESET.as_bytes());
            buf.extend_from_slice(b"\"");
        } else {
            buf.extend_from_slice(b" msg=\"");
        }
        write_msg(buf);
        buf.extend_from_slice(b"\"\n");

        *count += 1;
        if *count >= batch_size {
            let batch = std::mem::replace(buf, Vec::with_capacity(BATCH_BUF_CAP));
            *count = 0;
            let _ = tx.send(Action::WriteBytes(batch));
        }
    });
}

fn flush_producer(tx: &Sender<Action>) {
    PRODUCER.with(|producer| {
        let mut producer = producer.borrow_mut();
        if !producer.buf.is_empty() {
            let batch = std::mem::replace(&mut producer.buf, Vec::with_capacity(BATCH_BUF_CAP));
            producer.count = 0;
            let _ = tx.send(Action::WriteBytes(batch));
        }
    });
}

fn worker<P: ToString + Send>(mut ctx: Context<P>) -> Result<(), std::io::Error> {
    let timeout = Duration::from_secs(1);

    let mut target = rotate(&ctx)?;
    let mut last_flush = Instant::now();
    loop {
        match ctx.rx.recv_timeout(timeout) {
            Ok(Action::WriteBytes(bytes)) => {
                target.write_all(&bytes)?;
            }
            Ok(Action::Flush) => {
                target.flush()?;
            }
            Ok(Action::FlushSync(ack)) => {
                target.flush()?;
                let _ = ack.send(());
            }
            Ok(Action::Exit) => {
                target.flush()?;
                break;
            }
            Err(RecvTimeoutError::Timeout) => {}
            Err(RecvTimeoutError::Disconnected) => break,
        }

        if last_flush.elapsed() >= Duration::from_secs(1) {
            last_flush = Instant::now();
            // Roll over to a new dated file when the local day changes.
            if ctx.path.is_some() {
                let today = Local::now().date_naive();
                if today != ctx.date {
                    ctx.date = today;
                    target = rotate(&ctx)?;
                }
            }
            target.flush()?;
        }
    }

    Ok(())
}

pub fn init<P: ToString + Send + 'static>(name: &str, path: Option<P>, level: Level) -> Handle {
    let (tx, rx) = crossbeam_channel::bounded(CHANNEL_CAPACITY);

    let ctx = Context {
        rx,
        path,
        date: Local::now().date_naive(),
    };

    let logger = Logger {
        tx: tx.clone(),
        name: Some(Arc::from(name)),
        unix_ts: false,
    };

    log::set_boxed_logger(Box::new(logger)).expect("error to init logger");
    log::set_max_level(level);

    let thread = std::thread::spawn(move || {
        if let Err(msg) = worker(ctx) {
            eprintln!("error {}", msg);
        }
    });

    Handle {
        tx,
        thread: Some(thread),
    }
}

// Python bindings - instance-based logger
#[cfg(feature = "python")]
mod python {
    use super::{
        emit, flush_producer, worker, Action, Context, LevelFilter, CHANNEL_CAPACITY,
        COLOR_LIVE, DEFAULT_BATCH_SIZE, PRODUCER,
    };
    use chrono::Local;
    use crossbeam_channel::Sender;
    use pyo3::prelude::*;
    use pyo3::types::{PyDict, PyFloat, PyInt, PyString, PyTuple};
    use std::collections::HashMap;
    use std::hash::{Hash, Hasher};
    use std::io::{IsTerminal, Write};
    use std::sync::atomic::{AtomicU8, AtomicUsize, Ordering};
    use std::sync::{Arc, Mutex, OnceLock, Weak};
    use std::thread::JoinHandle;

    static BATCH_SIZE: AtomicUsize = AtomicUsize::new(DEFAULT_BATCH_SIZE);

    #[derive(Clone, Copy, PartialEq)]
    enum ColorMode {
        Off,
        Auto,
        Always,
    }

    fn parse_color(mode: &str) -> ColorMode {
        match mode {
            "off" => ColorMode::Off,
            "always" => ColorMode::Always,
            _ => ColorMode::Auto,
        }
    }

    /// Resolve a color mode against a concrete destination into a single bool
    /// carried on the writer. Runs once per writer creation, never per message.
    fn resolve_color(mode: ColorMode, path: &Option<String>) -> bool {
        match mode {
            ColorMode::Off => false,
            ColorMode::Always => true,
            ColorMode::Auto => {
                // `auto` only ever colors the stdout stream; files stay plain.
                if path.is_some() {
                    return false;
                }
                let non_empty = |k| std::env::var_os(k).is_some_and(|v| !v.is_empty());
                if non_empty("NO_COLOR") {
                    return false;
                }
                if std::env::var_os("FORCE_COLOR").is_some_and(|v| !v.is_empty() && v != "0") {
                    return true;
                }
                std::io::stdout().is_terminal()
            }
        }
    }

    #[derive(Clone, Eq)]
    enum PathKey {
        Stdout,
        File(String),
    }

    impl PartialEq for PathKey {
        fn eq(&self, other: &Self) -> bool {
            match (self, other) {
                (PathKey::Stdout, PathKey::Stdout) => true,
                (PathKey::File(a), PathKey::File(b)) => a == b,
                _ => false,
            }
        }
    }

    impl Hash for PathKey {
        fn hash<H: Hasher>(&self, state: &mut H) {
            match self {
                PathKey::Stdout => 0u8.hash(state),
                PathKey::File(path) => {
                    1u8.hash(state);
                    path.hash(state);
                }
            }
        }
    }

    struct SharedWriter {
        tx: Sender<Action>,
        unix_ts: bool,
        color: bool,
        thread: Mutex<Option<JoinHandle<()>>>,
    }

    impl SharedWriter {
        fn new(path: Option<String>, unix_ts: bool, color: bool) -> Self {
            let (tx, rx) = crossbeam_channel::bounded(CHANNEL_CAPACITY);
            let ctx = Context {
                rx,
                path,
                date: Local::now().date_naive(),
            };
            let thread = std::thread::spawn(move || {
                if let Err(msg) = worker(ctx) {
                    eprintln!("error {}", msg);
                }
            });

            SharedWriter {
                tx,
                unix_ts,
                color,
                thread: Mutex::new(Some(thread)),
            }
        }

        fn stop(&self) {
            let mut thread = self.thread.lock().unwrap();
            if let Some(thread) = thread.take() {
                let _ = self.tx.send(Action::Exit);
                let _ = thread.join();
            }
        }
    }

    impl Drop for SharedWriter {
        fn drop(&mut self) {
            self.stop();
        }
    }

    fn registry() -> &'static Mutex<HashMap<PathKey, Weak<SharedWriter>>> {
        static REGISTRY: OnceLock<Mutex<HashMap<PathKey, Weak<SharedWriter>>>> = OnceLock::new();
        REGISTRY.get_or_init(|| Mutex::new(HashMap::new()))
    }

    fn default_path_cell() -> &'static OnceLock<Mutex<Option<String>>> {
        static DEFAULT_PATH: OnceLock<Mutex<Option<String>>> = OnceLock::new();
        &DEFAULT_PATH
    }

    fn default_unix_ts_cell() -> &'static OnceLock<Mutex<bool>> {
        static DEFAULT_UNIX_TS: OnceLock<Mutex<bool>> = OnceLock::new();
        &DEFAULT_UNIX_TS
    }

    fn default_color_cell() -> &'static OnceLock<Mutex<ColorMode>> {
        static DEFAULT_COLOR: OnceLock<Mutex<ColorMode>> = OnceLock::new();
        &DEFAULT_COLOR
    }

    fn default_path() -> Option<String> {
        default_path_cell()
            .get_or_init(|| Mutex::new(None))
            .lock()
            .unwrap()
            .clone()
    }

    fn default_unix_ts() -> bool {
        *default_unix_ts_cell()
            .get_or_init(|| Mutex::new(false))
            .lock()
            .unwrap()
    }

    fn default_color() -> ColorMode {
        *default_color_cell()
            .get_or_init(|| Mutex::new(ColorMode::Auto))
            .lock()
            .unwrap()
    }

    fn set_default_path(path: Option<String>) {
        let cell = default_path_cell().get_or_init(|| Mutex::new(None));
        *cell.lock().unwrap() = path;
    }

    fn set_default_unix_ts(unix_ts: bool) {
        let cell = default_unix_ts_cell().get_or_init(|| Mutex::new(false));
        *cell.lock().unwrap() = unix_ts;
    }

    fn set_default_color(mode: ColorMode) {
        // Flip the global gate so producer threads start building colored
        // prefixes. Only ever set to true; a later `off` still leaves the (now
        // harmless) colored prefixes built, which no writer will read.
        if mode != ColorMode::Off {
            COLOR_LIVE.store(true, Ordering::Relaxed);
        }
        let cell = default_color_cell().get_or_init(|| Mutex::new(ColorMode::Auto));
        *cell.lock().unwrap() = mode;
    }

    fn shared_writer(path: Option<String>) -> Arc<SharedWriter> {
        let key = match path.clone() {
            Some(p) => PathKey::File(p),
            None => PathKey::Stdout,
        };

        let mut map = registry().lock().unwrap();
        if let Some(weak) = map.get(&key) {
            if let Some(writer) = weak.upgrade() {
                return writer;
            }
        }

        let color = resolve_color(default_color(), &path);
        let writer = Arc::new(SharedWriter::new(path, default_unix_ts(), color));
        map.insert(key, Arc::downgrade(&writer));
        writer
    }

    fn level_to_u8(level: log::Level) -> u8 {
        match level {
            log::Level::Error => 1,
            log::Level::Warn => 2,
            log::Level::Info => 3,
            log::Level::Debug => 4,
            log::Level::Trace => 5,
        }
    }

    fn push_u64(buf: &mut Vec<u8>, mut v: u64, base: u64) {
        let mut digits = [0u8; 22]; // u64 needs at most 22 octal digits
        let mut i = digits.len();
        loop {
            let d = (v % base) as u8;
            i -= 1;
            digits[i] = if d < 10 { b'0' + d } else { b'a' + d - 10 };
            v /= base;
            if v == 0 {
                break;
            }
        }
        buf.extend_from_slice(&digits[i..]);
    }

    fn push_i64(buf: &mut Vec<u8>, v: i64) {
        if v < 0 {
            buf.push(b'-');
        }
        push_u64(buf, v.unsigned_abs(), 10);
    }

    /// Write Python's repr(float) for the decimal-notation range; returns
    /// false when Python would use scientific notation (or for nan/inf) so
    /// the caller can fall back to PyObject_Str. Inside the range, Python's
    /// repr and Rust's Display both print the shortest round-trip digits, so
    /// output is identical except for the ".0" Python appends to whole values.
    fn push_f64_repr(out: &mut Vec<u8>, v: f64) -> bool {
        if v == 0.0 {
            out.extend_from_slice(if v.is_sign_negative() { b"-0.0" } else { b"0.0" });
            return true;
        }
        let a = v.abs();
        // Python repr switches to scientific outside [1e-4, 1e16). NaN and
        // inf fail this test too.
        if !(1e-4..1e16).contains(&a) {
            return false;
        }
        let start = out.len();
        let _ = write!(out, "{}", v);
        if !out[start..].contains(&b'.') {
            out.extend_from_slice(b".0");
        }
        true
    }

    /// Delegate the whole message to CPython's `str.__mod__`. Used both for
    /// format specs outside the fast path and for anomalies (bad counts,
    /// unknown conversions), so edge semantics and error messages are
    /// byte-identical to `message % args`.
    #[cold]
    fn fallback_format(
        out: &mut Vec<u8>,
        message: &Bound<'_, PyString>,
        target: &Bound<'_, PyAny>,
    ) -> PyResult<()> {
        out.clear();
        let formatted = message.rem(target)?;
        let formatted = formatted.cast_into::<PyString>()?;
        out.extend_from_slice(formatted.to_str()?.as_bytes());
        Ok(())
    }

    /// Write one converted argument. Ok(false) means the value needs CPython
    /// `%` semantics the fast path doesn't replicate (float for %d, huge ints,
    /// non-finite floats, ...) and the caller must fall back.
    fn convert_one(out: &mut Vec<u8>, conv: u8, obj: &Bound<'_, PyAny>) -> PyResult<bool> {
        match conv {
            b's' => {
                // Exact str skips PyObject_Str (subclasses may override
                // __str__); exact int within i64 renders natively, since
                // str(int) is plain decimal digits; exact float renders
                // natively when its repr uses decimal notation.
                if let Ok(s) = obj.cast_exact::<PyString>() {
                    out.extend_from_slice(s.to_str()?.as_bytes());
                } else if obj.is_exact_instance_of::<PyInt>() {
                    match obj.extract::<i64>() {
                        Ok(v) => push_i64(out, v),
                        Err(_) => out.extend_from_slice(obj.str()?.to_str()?.as_bytes()),
                    }
                } else if let Ok(f) = obj.cast_exact::<PyFloat>() {
                    if !push_f64_repr(out, f.value()) {
                        out.extend_from_slice(obj.str()?.to_str()?.as_bytes());
                    }
                } else {
                    out.extend_from_slice(obj.str()?.to_str()?.as_bytes());
                }
            }
            b'r' => {
                // For exact ints and floats, repr == str, so both share the
                // native paths above.
                if obj.is_exact_instance_of::<PyInt>() {
                    if let Ok(v) = obj.extract::<i64>() {
                        push_i64(out, v);
                        return Ok(true);
                    }
                } else if let Ok(f) = obj.cast_exact::<PyFloat>() {
                    if push_f64_repr(out, f.value()) {
                        return Ok(true);
                    }
                }
                out.extend_from_slice(obj.repr()?.to_str()?.as_bytes())
            }
            b'd' | b'i' => {
                // The PyInt gate excludes floats (Python's %d truncates them)
                // and arbitrary __index__ objects; ints beyond i64 also fall
                // back. bool is an int subclass and renders as 1/0.
                if !obj.is_instance_of::<PyInt>() {
                    return Ok(false);
                }
                match obj.extract::<i64>() {
                    Ok(v) => push_i64(out, v),
                    Err(_) => return Ok(false),
                }
            }
            b'x' | b'o' => {
                if !obj.is_instance_of::<PyInt>() {
                    return Ok(false);
                }
                match obj.extract::<i64>() {
                    Ok(v) => {
                        // Python renders negatives with a sign, not two's
                        // complement: "%x" % -255 == "-ff".
                        if v < 0 {
                            out.push(b'-');
                        }
                        push_u64(out, v.unsigned_abs(), if conv == b'x' { 16 } else { 8 });
                    }
                    Err(_) => return Ok(false),
                }
            }
            b'f' => {
                let Ok(f) = obj.cast::<PyFloat>() else {
                    return Ok(false); // "%f" % 3 is legal Python
                };
                let v = f.value();
                if !v.is_finite() {
                    return Ok(false); // Rust prints "NaN", Python "nan"
                }
                let _ = write!(out, "{:.6}", v);
            }
            _ => unreachable!(),
        }
        Ok(true)
    }

    /// Render `message % args` with stdlib-logging semantics into `out`.
    /// Bare %s/%r/%d/%i/%x/%o/%f/%% are written directly with no intermediate
    /// Python string; everything else delegates to CPython's `%` operator.
    fn render_message(
        out: &mut Vec<u8>,
        message: &Bound<'_, PyString>,
        args: &Bound<'_, PyTuple>,
    ) -> PyResult<()> {
        let items = args.as_slice();

        // stdlib quirk (LogRecord.__init__): a single non-empty dict argument
        // is used as a mapping, enabling %(key)s style.
        if let [only] = items {
            if only.is_instance_of::<PyDict>() && only.is_truthy()? {
                return fallback_format(out, message, only);
            }
        }

        let bytes = message.to_str()?.as_bytes();
        let mut argi = 0usize;
        let mut i = 0usize;
        while let Some(off) = bytes[i..].iter().position(|&b| b == b'%') {
            let pos = i + off;
            out.extend_from_slice(&bytes[i..pos]);
            match bytes.get(pos + 1).copied() {
                Some(b'%') => out.push(b'%'),
                Some(conv @ (b's' | b'r' | b'd' | b'i' | b'x' | b'o' | b'f')) => {
                    let Some(obj) = items.get(argi) else {
                        // too few args -> CPython's exact TypeError
                        return fallback_format(out, message, args.as_any());
                    };
                    if !convert_one(out, conv, obj)? {
                        return fallback_format(out, message, args.as_any());
                    }
                    argi += 1;
                }
                // Width/precision/flags, %(name)s, %e/%g, unknown conversions
                // and a trailing lone '%' all go to CPython.
                _ => return fallback_format(out, message, args.as_any()),
            }
            i = pos + 2;
        }
        out.extend_from_slice(&bytes[i..]);
        if argi != items.len() {
            // too many args -> CPython's exact TypeError
            return fallback_format(out, message, args.as_any());
        }
        Ok(())
    }

    #[pyclass]
    #[derive(Clone, Copy)]
    pub enum PyLevel {
        Trace,
        Debug,
        Info,
        Warn,
        Error,
    }

    impl From<PyLevel> for LevelFilter {
        fn from(level: PyLevel) -> Self {
            match level {
                PyLevel::Trace => LevelFilter::Trace,
                PyLevel::Debug => LevelFilter::Debug,
                PyLevel::Info => LevelFilter::Info,
                PyLevel::Warn => LevelFilter::Warn,
                PyLevel::Error => LevelFilter::Error,
            }
        }
    }

    impl From<PyLevel> for log::Level {
        fn from(level: PyLevel) -> Self {
            match level {
                PyLevel::Trace => log::Level::Trace,
                PyLevel::Debug => log::Level::Debug,
                PyLevel::Info => log::Level::Info,
                PyLevel::Warn => log::Level::Warn,
                PyLevel::Error => log::Level::Error,
            }
        }
    }

    #[pyclass]
    pub struct PyLogger {
        writer: Arc<SharedWriter>,
        name: Option<Arc<str>>,
        level: AtomicU8,
    }

    #[pymethods]
    impl PyLogger {
        #[new]
        #[pyo3(signature = (name, path=None, level=PyLevel::Info))]
        fn new(name: Option<String>, path: Option<String>, level: PyLevel) -> PyResult<Self> {
            Ok(PyLogger {
                writer: shared_writer(path),
                name: name.map(Arc::from),
                level: AtomicU8::new(level_to_u8(level.into())),
            })
        }

        fn shutdown(&self) {
            flush_producer(&self.writer.tx);
            // Wait (bounded) for the writer thread to flush, so data is on
            // disk when shutdown() returns even while other loggers still
            // share this writer and the thread keeps running.
            let (ack_tx, ack_rx) = crossbeam_channel::bounded(1);
            if self.writer.tx.send(Action::FlushSync(ack_tx)).is_ok() {
                let _ = ack_rx.recv_timeout(std::time::Duration::from_secs(5));
            }
            if Arc::strong_count(&self.writer) == 1 {
                self.writer.stop();
            }
        }

        #[pyo3(signature = (message, *args))]
        fn trace(&self, message: &Bound<'_, PyString>, args: &Bound<'_, PyTuple>) -> PyResult<()> {
            self.log_args(log::Level::Trace, message, args)
        }

        #[pyo3(signature = (message, *args))]
        fn debug(&self, message: &Bound<'_, PyString>, args: &Bound<'_, PyTuple>) -> PyResult<()> {
            self.log_args(log::Level::Debug, message, args)
        }

        #[pyo3(signature = (message, *args))]
        fn info(&self, message: &Bound<'_, PyString>, args: &Bound<'_, PyTuple>) -> PyResult<()> {
            self.log_args(log::Level::Info, message, args)
        }

        #[pyo3(signature = (message, *args))]
        fn warn(&self, message: &Bound<'_, PyString>, args: &Bound<'_, PyTuple>) -> PyResult<()> {
            self.log_args(log::Level::Warn, message, args)
        }

        // `warning` mirrors the stdlib `logging` name; `warn` is kept as an alias.
        #[pyo3(signature = (message, *args))]
        fn warning(&self, message: &Bound<'_, PyString>, args: &Bound<'_, PyTuple>) -> PyResult<()> {
            self.log_args(log::Level::Warn, message, args)
        }

        #[pyo3(signature = (message, *args))]
        fn error(&self, message: &Bound<'_, PyString>, args: &Bound<'_, PyTuple>) -> PyResult<()> {
            self.log_args(log::Level::Error, message, args)
        }
    }

    impl PyLogger {
        #[inline]
        fn log_args(
            &self,
            level: log::Level,
            message: &Bound<'_, PyString>,
            args: &Bound<'_, PyTuple>,
        ) -> PyResult<()> {
            // Lazy gate: args are never touched when the level is disabled.
            let max_level = self.level.load(Ordering::Relaxed);
            if level_to_u8(level) > max_level {
                return Ok(());
            }
            let batch_size = BATCH_SIZE.load(Ordering::Relaxed);

            if args.is_empty() {
                // Verbatim, no % processing — matches stdlib logging.
                let msg = message.to_str()?;
                emit(
                    &self.writer.tx,
                    batch_size,
                    self.writer.unix_ts,
                    self.writer.color,
                    self.name.as_deref(),
                    level,
                    |buf| buf.extend_from_slice(msg.as_bytes()),
                );
                return Ok(());
            }

            // Render into per-thread scratch before committing to the batch
            // buffer: a Python __str__/__repr__ may raise mid-render, and a
            // half-written line must never reach `emit`. Taken with
            // `mem::take` (not a held borrow) so a reentrant log call from
            // inside __str__ cannot hit a RefCell double-borrow.
            let mut scratch = PRODUCER.with(|p| std::mem::take(&mut p.borrow_mut().scratch));
            let result = render_message(&mut scratch, message, args);
            if result.is_ok() {
                emit(
                    &self.writer.tx,
                    batch_size,
                    self.writer.unix_ts,
                    self.writer.color,
                    self.name.as_deref(),
                    level,
                    |buf| buf.extend_from_slice(&scratch),
                );
            }
            scratch.clear();
            PRODUCER.with(|p| p.borrow_mut().scratch = scratch);
            result
        }
    }

    #[pymodule]
    #[pyo3(name = "_logger")]
    pub fn logger_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
        #[pyfunction]
        #[pyo3(signature = (path=None, unix_ts=false, batch_size=None, color="auto".to_string()))]
        fn basic_config(
            path: Option<String>,
            unix_ts: bool,
            batch_size: Option<usize>,
            color: String,
        ) -> PyResult<()> {
            set_default_path(path);
            set_default_unix_ts(unix_ts);
            set_default_color(parse_color(&color));
            if let Some(size) = batch_size {
                BATCH_SIZE.store(size.max(1), Ordering::Relaxed);
            }
            Ok(())
        }

        #[pyfunction]
        #[pyo3(signature = (name, level=PyLevel::Info))]
        fn get_logger(name: Option<String>, level: PyLevel) -> PyResult<PyLogger> {
            Ok(PyLogger {
                writer: shared_writer(default_path()),
                name: name.map(Arc::from),
                level: AtomicU8::new(level_to_u8(level.into())),
            })
        }

        m.add_class::<PyLevel>()?;
        m.add_class::<PyLogger>()?;
        m.add_function(wrap_pyfunction!(basic_config, m)?)?;
        m.add_function(wrap_pyfunction!(get_logger, m)?)?;
        Ok(())
    }
}
