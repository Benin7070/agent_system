//! Panic hook and crash reporting capturing backtraces and the in-memory ring buffer.

use super::events::LogEvent;
use chrono::Utc;
use std::collections::VecDeque;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

pub fn install_panic_hook(log_dir: PathBuf, ring_buffer: Arc<Mutex<VecDeque<LogEvent>>>) {
    let default_hook = std::panic::take_hook();

    std::panic::set_hook(Box::new(move |panic_info| {
        let ts = Utc::now().format("%Y%m%d-%H%M%S").to_string();
        let crash_file = log_dir.join(format!("crash-{}.log", ts));

        let mut msg = String::new();
        msg.push_str("================================================================================\n");
        msg.push_str("                        AGENTCTL DAEMON CRASH REPORT                            \n");
        msg.push_str("================================================================================\n");
        msg.push_str(&format!("Timestamp: {}\n", Utc::now().to_rfc3339()));

        if let Some(location) = panic_info.location() {
            msg.push_str(&format!("Panic Location: {}:{}:{}\n", location.file(), location.line(), location.column()));
        }

        let payload = if let Some(s) = panic_info.payload().downcast_ref::<&str>() {
            *s
        } else if let Some(s) = panic_info.payload().downcast_ref::<String>() {
            s.as_str()
        } else {
            "Unknown panic payload"
        };
        msg.push_str(&format!("Panic Reason: {}\n\n", payload));

        msg.push_str("--- Stack Backtrace ---\n");
        let backtrace = std::backtrace::Backtrace::capture();
        msg.push_str(&format!("{:?}\n\n", backtrace));

        msg.push_str("--- Last 50 In-Memory Events ---\n");
        if let Ok(guard) = ring_buffer.lock() {
            for (i, ev) in guard.iter().enumerate() {
                if let Ok(json_str) = serde_json::to_string(ev) {
                    msg.push_str(&format!("[#{:02}] {}\n", i + 1, json_str));
                }
            }
        }
        msg.push_str("================================================================================\n");

        // Write crash log with 0600 permissions
        let _ = fs::create_dir_all(&log_dir);
        let mut opts = OpenOptions::new();
        opts.create(true).write(true).truncate(true).mode(0o600);
        if let Ok(mut f) = opts.open(&crash_file) {
            let _ = f.write_all(msg.as_bytes());
            let _ = f.flush();
            let _ = fs::set_permissions(&crash_file, fs::Permissions::from_mode(0o600));
            eprintln!("[CRASH HANDLER] Wrote fatal crash log to {}", crash_file.display());
        }

        // Call standard hook for stderr output
        default_hook(panic_info);
    }));
}

