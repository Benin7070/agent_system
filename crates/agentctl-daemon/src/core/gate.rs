use crate::core::schemas::ProposedAction;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::Arc;
use thiserror::Error;
use tokio::sync::RwLock;

#[derive(Error, Debug, PartialEq, Eq)]
pub enum GateError {
    #[error("Task '{0}' has no pending approval request")]
    NotFound(String),
    #[error("Security violation: Action hash mismatch (stored '{stored}', presented '{provided}')")]
    HashMismatch { stored: String, provided: String },
    #[error("Serialization error: {0}")]
    Serialization(String),
}

/// A human-facing approval prompt containing ONLY deterministic facts.
/// Model reasoning is explicitly forbidden from being used here.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApprovalPrompt {
    pub task_id: String,
    pub action_hash: String,
    pub deterministic_summary: String,
}

/// Canonicalizes action to JSON and computes SHA-256 digest.
pub fn compute_action_hash(action: &ProposedAction) -> Result<String, GateError> {
    let json_bytes = serde_json::to_vec(action)
        .map_err(|e| GateError::Serialization(e.to_string()))?;
    let mut hasher = Sha256::new();
    hasher.update(&json_bytes);
    Ok(hex::encode(hasher.finalize()))
}

/// Generates a strictly deterministic, fact-only human-readable summary of the action.
pub fn generate_deterministic_summary(action: &ProposedAction) -> String {
    match action {
        ProposedAction::FileRead { path } => {
            format!("FILE READ: Exact path '{}'", path.display())
        }
        ProposedAction::FileWrite { path, content } => {
            format!(
                "FILE WRITE: Exact path '{}' ({} bytes)",
                path.display(),
                content.len()
            )
        }
        ProposedAction::FileMove { src, dst } => {
            format!(
                "FILE MOVE: Move '{}' -> '{}'",
                src.display(),
                dst.display()
            )
        }
        ProposedAction::FileDelete { path } => {
            format!("FILE DELETE: Exact path '{}'", path.display())
        }
        ProposedAction::ShellExec { command, args } => {
            format!(
                "SHELL EXEC: Command '{}' with arguments {:?}",
                command, args
            )
        }
        ProposedAction::BrowserNav { url } => {
            format!("BROWSER NAV: Navigate to '{}'", url)
        }
        ProposedAction::LogMessage { message } => {
            format!("LOG: Message '{}'", message)
        }
    }
}

#[derive(Clone, Default)]
pub struct ApprovalGate {
    // task_id -> (Action, Bound SHA-256 Hash)
    pending: Arc<RwLock<HashMap<String, (ProposedAction, String)>>>,
}

impl ApprovalGate {
    pub fn new() -> Self {
        Self {
            pending: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Submits an action for approval, generating an immutable hash binding and prompt.
    pub async fn request_approval(
        &self,
        task_id: &str,
        action: &ProposedAction,
    ) -> Result<ApprovalPrompt, GateError> {
        let action_hash = compute_action_hash(action)?;
        let summary = generate_deterministic_summary(action);

        let mut lock = self.pending.write().await;
        lock.insert(task_id.to_string(), (action.clone(), action_hash.clone()));

        Ok(ApprovalPrompt {
            task_id: task_id.to_string(),
            action_hash,
            deterministic_summary: summary,
        })
    }

    /// Verifies approval against the bound hash. If the action was modified,
    /// or if the presented hash does not match, rejects immediately.
    pub async fn verify_and_consume(
        &self,
        task_id: &str,
        provided_hash: &str,
    ) -> Result<ProposedAction, GateError> {
        let mut lock = self.pending.write().await;
        let (stored_action, stored_hash) = lock
            .remove(task_id)
            .ok_or_else(|| GateError::NotFound(task_id.to_string()))?;

        // Recompute hash from the stored action to ensure zero in-memory tampering
        let current_hash = compute_action_hash(&stored_action)?;
        if current_hash != stored_hash {
            return Err(GateError::HashMismatch {
                stored: stored_hash,
                provided: current_hash,
            });
        }

        if stored_hash != provided_hash {
            return Err(GateError::HashMismatch {
                stored: stored_hash,
                provided: provided_hash.to_string(),
            });
        }

        Ok(stored_action)
    }

    /// Withdraws/cancels an approval request (e.g. on hotkey cancel or timeout).
    pub async fn cancel(&self, task_id: &str) -> bool {
        let mut lock = self.pending.write().await;
        lock.remove(task_id).is_some()
    }
}
