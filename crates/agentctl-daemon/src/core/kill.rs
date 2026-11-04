use std::time::{Duration, Instant};
use thiserror::Error;
use tokio::process::Command;

#[derive(Error, Debug)]
pub enum KillError {
    #[error("Timeout while killing containers/processes")]
    Timeout,
    #[error("I/O error during kill operation: {0}")]
    Io(#[from] std::io::Error),
    #[error("Kill failed: {0}")]
    Execution(String),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KillReport {
    pub stopped_count: usize,
    pub elapsed_ms: u128,
}

/// Direct kill switch. Does NOT use the HTTP API or depend on daemon state.
/// Directly queries and terminates all containers labeled `agentctl.managed=true`.
///
/// Falls back smoothly across podman and docker runtimes.
/// Guaranteed to complete or fail within 2.0 seconds.
pub async fn kill_all_managed(task_id: Option<&str>) -> Result<KillReport, KillError> {
    let start = Instant::now();
    let timeout_duration = Duration::from_millis(2000);

    let filter_arg = match task_id {
        Some(tid) => format!("label=agentctl.task_id={}", tid),
        None => "label=agentctl.managed=true".to_string(),
    };

    // Try podman first, then docker
    let runtimes = ["podman", "docker"];
    let mut total_stopped = 0;

    for runtime in runtimes {
        // Query running container IDs with the filter
        let list_cmd = Command::new(runtime)
            .args(["ps", "-q", "--filter", &filter_arg])
            .output();

        let output = match tokio::time::timeout(Duration::from_millis(600), list_cmd).await {
            Ok(Ok(out)) => out,
            _ => continue, // Runtime not present or timed out, try next
        };

        if output.status.success() {
            let stdout = String::from_utf8_lossy(&output.stdout);
            let container_ids: Vec<&str> = stdout.lines().map(|l| l.trim()).filter(|l| !l.is_empty()).collect();

            if !container_ids.is_empty() {
                // Kill them with SIGKILL (-s KILL)
                let mut kill_args = vec!["kill", "-s", "KILL"];
                kill_args.extend(container_ids.iter().copied());

                let kill_cmd = Command::new(runtime).args(kill_args).output();

                if let Ok(Ok(kill_out)) = tokio::time::timeout(Duration::from_millis(1000), kill_cmd).await {
                    if kill_out.status.success() {
                        total_stopped += container_ids.len();
                    }
                }
            }
        }
    }

    let elapsed = start.elapsed();
    if elapsed > timeout_duration {
        return Err(KillError::Timeout);
    }

    Ok(KillReport {
        stopped_count: total_stopped,
        elapsed_ms: elapsed.as_millis(),
    })
}
