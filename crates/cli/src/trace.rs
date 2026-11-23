//! Opt-in diagnostic timeline (`OXIMUT_TRACE_TIMELINE=<absolute dir>`), shared
//! with the Node adapter's `timeline.cjs`. The scheduler appends JSON lines to
//! `<dir>/scheduler-<pid>.jsonl`. Unset, nothing is opened and the closures that
//! build events never run.
//!
//! Clock: `t` is milliseconds since the Unix epoch (`SystemTime`) as an `f64`,
//! precise to far below a microsecond. The adapter writes
//! `performance.timeOrigin + performance.now()`, the same scale, so lines from
//! every process compare directly.
use serde_json::{Value, json};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::Write,
    path::PathBuf,
    sync::{Mutex, OnceLock},
    time::{SystemTime, UNIX_EPOCH},
};

static SINK: OnceLock<Option<Mutex<File>>> = OnceLock::new();

fn sink() -> Option<&'static Mutex<File>> {
    SINK.get_or_init(|| {
        let directory =
            PathBuf::from(env::var_os("OXIMUT_TRACE_TIMELINE").filter(|v| !v.is_empty())?);
        fs::create_dir_all(&directory).ok()?;
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(directory.join(format!("scheduler-{}.jsonl", std::process::id())))
            .ok()?;
        Some(Mutex::new(file))
    })
    .as_ref()
}

/// Milliseconds since the Unix epoch, sub-millisecond precision.
pub fn now_ms() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64() * 1000.0)
}

/// Appends one event. `fields` must produce a JSON object and runs only when tracing.
/// A write error drops the event: tracing never fails a run.
pub fn event(name: &str, fields: impl FnOnce() -> Value) {
    let Some(sink) = sink() else { return };
    let t = now_ms();
    let mut line = json!({ "t": t, "event": name, "role": "scheduler", "pid": std::process::id() });
    if let (Some(line), Value::Object(fields)) = (line.as_object_mut(), fields()) {
        line.extend(fields);
    }
    let mut text = line.to_string();
    text.push('\n');
    if let Ok(mut file) = sink.lock() {
        let _ = file.write_all(text.as_bytes());
    }
}
