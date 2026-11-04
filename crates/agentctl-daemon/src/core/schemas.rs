use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use url::Url;

/// Discriminated union of all possible actions an agent can propose.
/// Unknown action types are rejected during deserialization.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ProposedAction {
    FileRead {
        path: PathBuf,
    },
    FileWrite {
        path: PathBuf,
        content: String,
    },
    FileMove {
        src: PathBuf,
        dst: PathBuf,
    },
    FileDelete {
        path: PathBuf,
    },
    ShellExec {
        command: String,
        args: Vec<String>,
    },
    BrowserNav {
        url: Url,
    },
    LogMessage {
        message: String,
    },
}

impl ProposedAction {
    /// Return the canonical type name for permission checks.
    pub fn action_type_name(&self) -> &'static str {
        match self {
            Self::FileRead { .. } => "file_read",
            Self::FileWrite { .. } => "file_write",
            Self::FileMove { .. } => "file_move",
            Self::FileDelete { .. } => "file_delete",
            Self::ShellExec { .. } => "shell_exec",
            Self::BrowserNav { .. } => "browser_nav",
            Self::LogMessage { .. } => "log_message",
        }
    }
}

/// Permission token granting rights to perform certain actions within limits.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct PermissionToken {
    pub id: String,
    pub allowed_actions: Vec<String>,
    pub allowed_paths: Vec<PathBuf>,
    pub allowed_domains: Vec<String>,
    pub expires_at: DateTime<Utc>,
    pub revoked: bool,
}

/// Result returned by the action validator.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ValidatorResult {
    pub allowed: bool,
    pub reason: String,
}

impl ValidatorResult {
    pub fn allow() -> Self {
        Self {
            allowed: true,
            reason: "Action permitted under token policy".to_string(),
        }
    }

    pub fn deny(reason: impl Into<String>) -> Self {
        Self {
            allowed: false,
            reason: reason.into(),
        }
    }
}

/// Request payload for submitting voice/text user intent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct IntentRequest {
    pub text: String,
    #[serde(default)]
    pub session_id: Option<String>,
}

/// Response payload for reporting daemon / task status.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StatusResponse {
    pub state: String,
    #[serde(default)]
    pub task_id: Option<String>,
    #[serde(default)]
    pub error: Option<String>,
}

/// Response payload when an operation is cancelled.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CancelResponse {
    pub ok: bool,
    pub cancelled_from: String,
    #[serde(default)]
    pub task_id: Option<String>,
}

/// Approval decision payload sent by client/UI.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApprovalDecision {
    pub task_id: String,
    pub hash: String,
}

/// Standard envelope for API responses.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ApiResponse<T> {
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<T>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub error: Option<String>,
}

impl<T> ApiResponse<T> {
    pub fn success(data: T) -> Self {
        Self {
            ok: true,
            data: Some(data),
            error: None,
        }
    }

    pub fn error(err: impl Into<String>) -> Self {
        Self {
            ok: false,
            data: None,
            error: Some(err.into()),
        }
    }
}
