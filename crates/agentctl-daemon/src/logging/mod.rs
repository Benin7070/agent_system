//! Logging and telemetry subsystem for AgentCTL.

pub mod crash;
pub mod events;
pub mod writer;

pub use events::{format_daemon_log_line, redact_str, redact_value, LogEvent, LogLevel};
pub use writer::{get_default_log_dir, prune_logs, LogConfig, LogWriter};

use serde_json::json;
use std::sync::Arc;

#[derive(Clone)]
pub struct Logger {
    writer: Arc<LogWriter>,
    config: LogConfig,
}

impl Logger {
    pub fn new(config: LogConfig, initial_level: LogLevel) -> Arc<Self> {
        let writer = Arc::new(LogWriter::new(config.clone(), initial_level));

        // Install panic hook for fatal crash reporting
        crash::install_panic_hook(config.log_dir.clone(), writer.get_ring_buffer());

        Arc::new(Self { writer, config })
    }

    pub fn log(&self, event: LogEvent) {
        self.writer.log(event);
    }

    pub fn debug(
        &self,
        event: impl Into<String>,
        component: impl Into<String>,
        message: impl Into<String>,
        payload: serde_json::Value,
    ) {
        self.log(LogEvent::new(LogLevel::Debug, event, component, message, payload));
    }

    pub fn info(
        &self,
        event: impl Into<String>,
        component: impl Into<String>,
        message: impl Into<String>,
        payload: serde_json::Value,
    ) {
        self.log(LogEvent::new(LogLevel::Info, event, component, message, payload));
    }

    pub fn warn(
        &self,
        event: impl Into<String>,
        component: impl Into<String>,
        message: impl Into<String>,
        payload: serde_json::Value,
    ) {
        self.log(LogEvent::new(LogLevel::Warn, event, component, message, payload));
    }

    pub fn error(
        &self,
        event: impl Into<String>,
        component: impl Into<String>,
        message: impl Into<String>,
        payload: serde_json::Value,
    ) {
        self.log(LogEvent::new(LogLevel::Error, event, component, message, payload));
    }

    pub fn get_level(&self) -> LogLevel {
        self.writer.get_level()
    }

    pub fn set_level(&self, level: LogLevel) {
        self.writer.set_level(level);
    }

    pub fn get_recent_events(&self, limit: usize) -> Vec<LogEvent> {
        self.writer.get_recent_events(limit)
    }

    pub fn config(&self) -> &LogConfig {
        &self.config
    }

    pub fn shutdown(&self, requester: &str, reason: &str, exit_code: i32, uptime_secs: u64) {
        self.info(
            "daemon.shutdown_requested",
            "daemon",
            format!("Shutdown requested by {}: {}", requester, reason),
            json!({ "requester": requester, "reason": reason }),
        );
        self.info(
            "daemon.clean_exit",
            "daemon",
            format!("Clean exit with code {}", exit_code),
            json!({ "exit_code": exit_code, "uptime_secs": uptime_secs }),
        );
        self.writer.shutdown();
        std::thread::sleep(std::time::Duration::from_millis(50));
    }
}
