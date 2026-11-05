//! Event models, formatting, and secret redaction for AgentCTL telemetry.

use chrono::Utc;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::fmt;
use std::sync::OnceLock;

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum LogLevel {
    Debug = 0,
    Info = 1,
    Warn = 2,
    Error = 3,
}

impl LogLevel {
    pub fn as_str(&self) -> &'static str {
        match self {
            LogLevel::Debug => "DEBUG",
            LogLevel::Info => "INFO",
            LogLevel::Warn => "WARN",
            LogLevel::Error => "ERROR",
        }
    }

    pub fn from_str_loose(s: &str) -> Option<Self> {
        match s.trim().to_lowercase().as_str() {
            "debug" => Some(LogLevel::Debug),
            "info" => Some(LogLevel::Info),
            "warn" | "warning" => Some(LogLevel::Warn),
            "error" => Some(LogLevel::Error),
            _ => None,
        }
    }
}

impl fmt::Display for LogLevel {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.as_str())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogEvent {
    pub timestamp: String,
    pub level: LogLevel,
    pub event: String,
    pub component: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub turn_id: Option<u64>,
    pub message: String,
    pub payload: serde_json::Value,
}

impl LogEvent {
    pub fn new(
        level: LogLevel,
        event: impl Into<String>,
        component: impl Into<String>,
        message: impl Into<String>,
        payload: serde_json::Value,
    ) -> Self {
        Self {
            timestamp: Utc::now().to_rfc3339_opts(chrono::SecondsFormat::Millis, true),
            level,
            event: event.into(),
            component: component.into(),
            session_id: None,
            turn_id: None,
            message: message.into(),
            payload,
        }
    }

    pub fn with_session(mut self, session_id: Option<String>, turn_id: Option<u64>) -> Self {
        self.session_id = session_id;
        self.turn_id = turn_id;
        self
    }
}

static REDACT_PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();

fn get_redact_patterns() -> &'static [Regex] {
    REDACT_PATTERNS.get_or_init(|| {
        vec![
            // OpenAI and similar API key pattern (sk-...)
            Regex::new(r"sk-[A-Za-z0-9_\-]{20,}").unwrap(),
            // Bearer tokens
            Regex::new(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+").unwrap(),
            // Generic api_key or secret in query/form
            Regex::new(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[^\s,;]+").unwrap(),
        ]
    })
}

/// Recursively redacts secrets from any JSON structure.
pub fn redact_value(value: &serde_json::Value) -> serde_json::Value {
    match value {
        serde_json::Value::Object(map) => {
            let mut redacted_map = serde_json::Map::new();
            for (k, v) in map {
                let lower_k = k.to_lowercase();
                if lower_k.contains("key")
                    || lower_k.contains("secret")
                    || lower_k.contains("token")
                    || lower_k.contains("auth")
                    || lower_k.contains("password")
                    || lower_k.contains("credential")
                {
                    redacted_map.insert(k.clone(), serde_json::Value::String("[REDACTED]".to_string()));
                } else {
                    redacted_map.insert(k.clone(), redact_value(v));
                }
            }
            serde_json::Value::Object(redacted_map)
        }
        serde_json::Value::Array(arr) => {
            serde_json::Value::Array(arr.iter().map(redact_value).collect())
        }
        serde_json::Value::String(s) => {
            serde_json::Value::String(redact_str(s))
        }
        other => other.clone(),
    }
}

/// Redacts any API key or credential pattern from a raw text string.
pub fn redact_str(text: &str) -> String {
    let mut out = text.to_string();
    for regex in get_redact_patterns() {
        out = regex.replace_all(&out, "[REDACTED]").to_string();
    }
    out
}

/// Formats a human-readable line for `daemon.log`:
/// `2026-11-05 16:26:03.412 INFO  session  turn 3 finished  total=2.31s`
pub fn format_daemon_log_line(event: &LogEvent) -> String {
    // Format timestamp nicely as YYYY-MM-DD HH:MM:SS.mmm
    let ts = match chrono::DateTime::parse_from_rfc3339(&event.timestamp) {
        Ok(dt) => dt.format("%Y-%m-%d %H:%M:%S%.3f").to_string(),
        Err(_) => event.timestamp.clone(),
    };

    let level_str = format!("{:<5}", event.level.as_str());
    let comp_str = format!("{:<10}", event.component);

    let mut details = String::new();
    if let Some(ref sid) = event.session_id {
        let sid_short = if sid.len() > 8 { &sid[..8] } else { sid.as_str() };
        details.push_str(&format!("sid={} ", sid_short));
    }
    if let Some(tid) = event.turn_id {
        details.push_str(&format!("turn={} ", tid));
    }

    if let serde_json::Value::Object(map) = &event.payload {
        let parts: Vec<String> = map
            .iter()
            .take(5)
            .map(|(k, v)| {
                let v_str = match v {
                    serde_json::Value::String(s) => s.clone(),
                    _ => v.to_string(),
                };
                format!("{}={}", k, v_str)
            })
            .collect();
        if !parts.is_empty() {
            if !details.is_empty() {
                details.push(' ');
            }
            details.push_str(&parts.join(" "));
        }
    }

    if details.is_empty() {
        format!("{} {} {} {}\n", ts, level_str, comp_str, event.message)
    } else {
        format!("{} {} {} {} | {}\n", ts, level_str, comp_str, event.message, details)
    }
}

