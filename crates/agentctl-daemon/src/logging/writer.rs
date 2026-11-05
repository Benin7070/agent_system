//! Asynchronous non-blocking file writer with rotation, retention, and drop protection.

use super::events::{format_daemon_log_line, redact_value, LogEvent, LogLevel};
use chrono::{Datelike, Local, Utc};
use serde_json::json;
use std::collections::VecDeque;
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::mpsc::{sync_channel, Receiver, SyncSender, TrySendError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, SystemTime};

const DEFAULT_QUEUE_CAPACITY: usize = 10_000;
const DEFAULT_MAX_FILE_SIZE_BYTES: u64 = 20 * 1024 * 1024; // 20 MB
const DEFAULT_MAX_TOTAL_SIZE_BYTES: u64 = 200 * 1024 * 1024; // 200 MB
const DEFAULT_MAX_RETENTION_DAYS: u64 = 7;
const RING_BUFFER_CAPACITY: usize = 50;

#[derive(Debug, Clone)]
pub struct LogConfig {
    pub log_dir: PathBuf,
    pub max_file_size_bytes: u64,
    pub max_total_size_bytes: u64,
    pub max_retention_days: u64,
    pub log_text: bool,
    pub journald: bool,
}

impl Default for LogConfig {
    fn default() -> Self {
        Self {
            log_dir: get_default_log_dir(),
            max_file_size_bytes: DEFAULT_MAX_FILE_SIZE_BYTES,
            max_total_size_bytes: DEFAULT_MAX_TOTAL_SIZE_BYTES,
            max_retention_days: DEFAULT_MAX_RETENTION_DAYS,
            log_text: false,
            journald: false,
        }
    }
}

pub fn get_default_log_dir() -> PathBuf {
    if let Ok(custom) = std::env::var("AGENTCTL_LOG_DIR") {
        if !custom.trim().is_empty() {
            return PathBuf::from(shellexpand_tilde(&custom));
        }
    }

    // If running under sudo, write to the real user's state dir so permissions match
    if let Ok(sudo_user) = std::env::var("SUDO_USER") {
        let p = PathBuf::from(format!("/home/{}/.local/state/agentctl/logs", sudo_user));
        return p;
    }

    if let Ok(state_home) = std::env::var("XDG_STATE_HOME") {
        PathBuf::from(state_home).join("agentctl").join("logs")
    } else if let Ok(home) = std::env::var("HOME") {
        PathBuf::from(home)
            .join(".local")
            .join("state")
            .join("agentctl")
            .join("logs")
    } else {
        PathBuf::from("/tmp/agentctl-logs")
    }
}

fn shellexpand_tilde(path: &str) -> String {
    if path.starts_with("~/") {
        if let Ok(home) = std::env::var("HOME") {
            return format!("{}{}", home, &path[1..]);
        }
    }
    path.to_string()
}

pub struct LogWriter {
    tx: SyncSender<LogEvent>,
    config: LogConfig,
    active_level: Arc<AtomicUsize>,
    dropped_count: Arc<AtomicU64>,
    ring_buffer: Arc<Mutex<VecDeque<LogEvent>>>,
    is_shutting_down: Arc<AtomicBool>,
}

impl LogWriter {
    pub fn new(config: LogConfig, initial_level: LogLevel) -> Self {
        // Ensure log directory exists with 0700 permissions
        if let Err(e) = fs::create_dir_all(&config.log_dir) {
            eprintln!("[Logger] Failed to create log directory {:?}: {}", config.log_dir, e);
        } else {
            let _ = fs::set_permissions(&config.log_dir, fs::Permissions::from_mode(0o700));
        }

        let (tx, rx) = sync_channel::<LogEvent>(DEFAULT_QUEUE_CAPACITY);
        let active_level = Arc::new(AtomicUsize::new(initial_level as usize));
        let dropped_count = Arc::new(AtomicU64::new(0));
        let ring_buffer = Arc::new(Mutex::new(VecDeque::with_capacity(RING_BUFFER_CAPACITY)));
        let is_shutting_down = Arc::new(AtomicBool::new(false));

        let worker_config = config.clone();
        let worker_ring = ring_buffer.clone();
        let worker_drops = dropped_count.clone();
        let worker_shutdown = is_shutting_down.clone();

        thread::Builder::new()
            .name("agentctl-logger".to_string())
            .spawn(move || {
                run_writer_loop(rx, worker_config, worker_ring, worker_drops, worker_shutdown);
            })
            .expect("Failed to spawn logger thread");

        Self {
            tx,
            config,
            active_level,
            dropped_count,
            ring_buffer,
            is_shutting_down,
        }
    }

    pub fn get_level(&self) -> LogLevel {
        match self.active_level.load(Ordering::Relaxed) {
            0 => LogLevel::Debug,
            1 => LogLevel::Info,
            2 => LogLevel::Warn,
            _ => LogLevel::Error,
        }
    }

    pub fn set_level(&self, level: LogLevel) {
        self.active_level.store(level as usize, Ordering::SeqCst);
    }

    pub fn is_level_enabled(&self, level: LogLevel) -> bool {
        level as usize >= self.active_level.load(Ordering::Relaxed)
    }

    /// Non-blocking event submission.
    pub fn log(&self, mut event: LogEvent) {
        if !self.is_level_enabled(event.level) {
            return;
        }

        // Privacy: Redact secrets unconditionally
        event.payload = redact_value(&event.payload);

        // Privacy: If log_text is false, ensure text fields in STT and LLM are filtered
        if !self.config.log_text {
            if let serde_json::Value::Object(ref mut map) = event.payload {
                if map.contains_key("text") {
                    map.insert("text".to_string(), json!("[REDACTED: log_text=false]"));
                }
                if map.contains_key("transcript") {
                    map.insert("transcript".to_string(), json!("[REDACTED: log_text=false]"));
                }
                if map.contains_key("reply_text") {
                    map.insert("reply_text".to_string(), json!("[REDACTED: log_text=false]"));
                }
            }
        }

        // Non-blocking try_send
        match self.tx.try_send(event.clone()) {
            Ok(()) => {}
            Err(TrySendError::Full(_)) => {
                // Drop event and increment drop counter
                self.dropped_count.fetch_add(1, Ordering::Relaxed);
            }
            Err(TrySendError::Disconnected(_)) => {
                // Writer thread died
            }
        }
    }

    pub fn get_ring_buffer(&self) -> Arc<Mutex<VecDeque<LogEvent>>> {
        self.ring_buffer.clone()
    }

    pub fn get_recent_events(&self, limit: usize) -> Vec<LogEvent> {
        if let Ok(guard) = self.ring_buffer.lock() {
            guard.iter().rev().take(limit).cloned().collect()
        } else {
            Vec::new()
        }
    }

    pub fn shutdown(&self) {
        self.is_shutting_down.store(true, Ordering::SeqCst);
    }
}

struct OpenLogFiles {
    events_file: Option<File>,
    daemon_file: Option<File>,
    events_size: u64,
    daemon_size: u64,
    current_day: u32,
}

fn open_log_file(path: &Path) -> std::io::Result<(File, u64)> {
    let mut opts = OpenOptions::new();
    opts.create(true).append(true).mode(0o600);
    let file = opts.open(path)?;
    let meta = file.metadata()?;
    let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
    Ok((file, meta.len()))
}

fn run_writer_loop(
    rx: Receiver<LogEvent>,
    config: LogConfig,
    ring_buffer: Arc<Mutex<VecDeque<LogEvent>>>,
    dropped_count: Arc<AtomicU64>,
    _is_shutting_down: Arc<AtomicBool>,
) {
    let events_path = config.log_dir.join("events.jsonl");
    let daemon_path = config.log_dir.join("daemon.log");

    let today = Local::now().day();
    let mut files = OpenLogFiles {
        events_file: None,
        daemon_file: None,
        events_size: 0,
        daemon_size: 0,
        current_day: today,
    };

    let mut last_retention_check = SystemTime::now();
    let mut had_drops = false;

    // Prune on startup
    prune_logs(&config);

    while let Ok(event) = rx.recv() {
        // Check if day changed for daily rotation
        let now_day = Local::now().day();
        if now_day != files.current_day {
            rotate_files(&config, &mut files, "daily");
            files.current_day = now_day;
        }

        // Check if size exceeded for size-based rotation
        if files.events_size >= config.max_file_size_bytes || files.daemon_size >= config.max_file_size_bytes {
            rotate_files(&config, &mut files, "size");
        }

        // Ensure files are open
        if files.events_file.is_none() {
            if let Ok((f, sz)) = open_log_file(&events_path) {
                files.events_file = Some(f);
                files.events_size = sz;
            }
        }
        if files.daemon_file.is_none() {
            if let Ok((f, sz)) = open_log_file(&daemon_path) {
                files.daemon_file = Some(f);
                files.daemon_size = sz;
            }
        }

        // Format lines
        let json_line = match serde_json::to_string(&event) {
            Ok(s) => format!("{}\n", s),
            Err(_) => String::new(),
        };
        let daemon_line = format_daemon_log_line(&event);

        // Write to events.jsonl
        if let Some(ref mut f) = files.events_file {
            if let Ok(_) = f.write_all(json_line.as_bytes()) {
                files.events_size += json_line.len() as u64;
            }
        }

        // Write to daemon.log
        if let Some(ref mut f) = files.daemon_file {
            if let Ok(_) = f.write_all(daemon_line.as_bytes()) {
                files.daemon_size += daemon_line.len() as u64;
            }
        }

        // Update in-memory ring buffer (last 50 events)
        if let Ok(mut guard) = ring_buffer.lock() {
            if guard.len() >= RING_BUFFER_CAPACITY {
                guard.pop_front();
            }
            guard.push_back(event);
        }

        // Periodic retention check every 60s
        if let Ok(elapsed) = last_retention_check.elapsed() {
            if elapsed >= Duration::from_secs(60) {
                prune_logs(&config);
                last_retention_check = SystemTime::now();

                // Check drops
                let drops = dropped_count.swap(0, Ordering::Relaxed);
                if drops > 0 {
                    had_drops = true;
                    eprintln!("[Logger Warning] Logger recovered: dropped {} events during queue saturation", drops);
                } else if had_drops {
                    had_drops = false;
                }
            }
        }
    }
}

fn rotate_files(config: &LogConfig, files: &mut OpenLogFiles, _reason: &str) {
    // Flush and close current files
    if let Some(mut f) = files.events_file.take() {
        let _ = f.flush();
    }
    if let Some(mut f) = files.daemon_file.take() {
        let _ = f.flush();
    }

    let timestamp = Utc::now().format("%Y%m%d-%H%M%S").to_string();
    let events_path = config.log_dir.join("events.jsonl");
    let daemon_path = config.log_dir.join("daemon.log");

    let rotated_events = config.log_dir.join(format!("events-{}.jsonl", timestamp));
    let rotated_daemon = config.log_dir.join(format!("daemon-{}.log", timestamp));

    if events_path.exists() {
        let _ = fs::rename(&events_path, &rotated_events);
    }
    if daemon_path.exists() {
        let _ = fs::rename(&daemon_path, &rotated_daemon);
    }

    files.events_size = 0;
    files.daemon_size = 0;

    // Prune after rotation
    prune_logs(config);
}

/// Prunes rotated logs based on age (7 days) and total directory size (200MB cap).
pub fn prune_logs(config: &LogConfig) {
    let entries = match fs::read_dir(&config.log_dir) {
        Ok(e) => e,
        Err(_) => return,
    };

    let mut log_files = Vec::new();
    let now = SystemTime::now();
    let max_age = Duration::from_secs(config.max_retention_days * 86400);

    for entry in entries.flatten() {
        let path = entry.path();
        if !path.is_file() {
            continue;
        }

        let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
        // Only prune rotated archives e.g. events-2026... or daemon-2026...
        if !(name.starts_with("events-") || name.starts_with("daemon-") || name.starts_with("crash-")) {
            continue;
        }

        if let Ok(meta) = entry.metadata() {
            let size = meta.len();
            let mtime = meta.modified().unwrap_or(now);

            // Check age
            if let Ok(age) = now.duration_since(mtime) {
                if age > max_age {
                    let _ = fs::remove_file(&path);
                    continue;
                }
            }

            log_files.push((path, size, mtime));
        }
    }

    // Check total size
    let mut total_size: u64 = log_files.iter().map(|(_, sz, _)| *sz).sum();
    if total_size > config.max_total_size_bytes {
        // Sort oldest first
        log_files.sort_by_key(|(_, _, mtime)| *mtime);
        for (path, size, _) in log_files {
            if total_size <= config.max_total_size_bytes {
                break;
            }
            if fs::remove_file(&path).is_ok() {
                total_size = total_size.saturating_sub(size);
            }
        }
    }
}
