use crate::core::schemas::ProposedAction;
use std::fs;
use thiserror::Error;
use tokio::process::Command;

#[derive(Error, Debug)]
pub enum ExecutorError {
    #[error("I/O error during execution: {0}")]
    Io(#[from] std::io::Error),
    #[error("Shell execution error: {0}")]
    Shell(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecutionOutput {
    Read(String),
    Written(usize),
    Moved,
    Deleted,
    Executed { status_code: i32, stdout: String },
    Logged,
    Navigated(String),
}

/// Executes a single validated, approved action.
pub async fn execute(action: &ProposedAction) -> Result<ExecutionOutput, ExecutorError> {
    match action {
        ProposedAction::FileRead { path } => {
            let content = fs::read_to_string(path)?;
            Ok(ExecutionOutput::Read(content))
        }
        ProposedAction::FileWrite { path, content } => {
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::write(path, content)?;
            Ok(ExecutionOutput::Written(content.len()))
        }
        ProposedAction::FileMove { src, dst } => {
            // Idempotent: if src doesn't exist but dst exists, it was already completed
            if !src.exists() && dst.exists() {
                return Ok(ExecutionOutput::Moved);
            }
            if let Some(parent) = dst.parent() {
                fs::create_dir_all(parent)?;
            }
            fs::rename(src, dst)?;
            Ok(ExecutionOutput::Moved)
        }
        ProposedAction::FileDelete { path } => {
            // Idempotent: if already deleted, return Ok
            if path.exists() {
                if path.is_dir() {
                    fs::remove_dir_all(path)?;
                } else {
                    fs::remove_file(path)?;
                }
            }
            Ok(ExecutionOutput::Deleted)
        }
        ProposedAction::ShellExec { command, args } => {
            let output = Command::new(command).args(args).output().await?;
            let stdout = String::from_utf8_lossy(&output.stdout).to_string();
            let code = output.status.code().unwrap_or(-1);
            Ok(ExecutionOutput::Executed {
                status_code: code,
                stdout,
            })
        }
        ProposedAction::BrowserNav { url } => {
            Ok(ExecutionOutput::Navigated(url.to_string()))
        }
        ProposedAction::LogMessage { .. } => Ok(ExecutionOutput::Logged),
    }
}
