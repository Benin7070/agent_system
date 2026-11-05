//! Management of the local `llama-server` process lifecycle.
//! Automatically ensures the model is running on daemon startup and stops it on daemon shutdown,
//! releasing discrete GPU VRAM and system memory.

use std::env;
use std::path::PathBuf;
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

static SPAWNED_BY_DAEMON: AtomicBool = AtomicBool::new(false);

pub struct LocalLlmManager;

impl LocalLlmManager {
    pub fn get_pid_file_path() -> PathBuf {
        if let Ok(sudo_user) = env::var("SUDO_USER") {
            return PathBuf::from(format!("/home/{}/.local/state/agentctl/llama-server.pid", sudo_user));
        }
        if let Ok(state_home) = env::var("XDG_STATE_HOME") {
            return PathBuf::from(state_home).join("agentctl").join("llama-server.pid");
        }
        if let Ok(home) = env::var("HOME") {
            return PathBuf::from(home).join(".local").join("state").join("agentctl").join("llama-server.pid");
        }
        PathBuf::from("/tmp/llama-server.pid")
    }

    pub fn get_log_file_path() -> PathBuf {
        if let Ok(sudo_user) = env::var("SUDO_USER") {
            return PathBuf::from(format!("/home/{}/.local/state/agentctl/logs/llama-server.log", sudo_user));
        }
        if let Ok(state_home) = env::var("XDG_STATE_HOME") {
            return PathBuf::from(state_home).join("agentctl").join("logs").join("llama-server.log");
        }
        if let Ok(home) = env::var("HOME") {
            return PathBuf::from(home).join(".local").join("state").join("agentctl").join("logs").join("llama-server.log");
        }
        PathBuf::from("/tmp/llama-server.log")
    }

    pub async fn is_healthy(port: u16) -> bool {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_millis(500))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());
        let url = format!("http://127.0.0.1:{}/health", port);
        match client.get(&url).send().await {
            Ok(resp) => resp.status().is_success(),
            Err(_) => false,
        }
    }

    pub async fn ensure_started(port: u16) -> Result<(), anyhow::Error> {
        if Self::is_healthy(port).await {
            tracing::info!("Local LLM (llama-server) is already healthy on port {}", port);
            return Ok(());
        }

        let home_dir = if let Ok(u) = env::var("SUDO_USER") {
            format!("/home/{}", u)
        } else {
            env::var("HOME").unwrap_or_else(|_| "/home/benin".to_string())
        };

        let server_bin = PathBuf::from(&home_dir).join(".local/bin/llama-server");
        let fallback_bin = PathBuf::from(&home_dir).join(".local/share/agentctl/llama.cpp/llama-server");
        let bin_to_use = if server_bin.exists() {
            server_bin
        } else if fallback_bin.exists() {
            fallback_bin
        } else {
            anyhow::bail!("llama-server binary not found at {:?}", server_bin);
        };

        let model_path = PathBuf::from(&home_dir).join(".local/share/agentctl/models/llm/Qwen3.5-9B-Q4_K_M.gguf");
        if !model_path.exists() {
            anyhow::bail!("GGUF model not found at {:?}", model_path);
        }

        let pid_file = Self::get_pid_file_path();
        let log_file = Self::get_log_file_path();
        if let Some(parent) = log_file.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        if let Some(parent) = pid_file.parent() {
            let _ = std::fs::create_dir_all(parent);
        }

        let log_out = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&log_file)?;
        let log_err = log_out.try_clone()?;

        tracing::info!(
            "Auto-spawning llama-server (model: {:?}, port: {})...",
            model_path.file_name().unwrap_or_default(),
            port
        );

        let mut cmd = Command::new(&bin_to_use);
        cmd.args([
            "-m",
            model_path.to_str().unwrap(),
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
            "-ngl",
            "99",
            "-c",
            "4096",
            "--flash-attn",
            "on",
            "--cache-type-k",
            "q8_0",
            "--reasoning",
            "off",
            "-t",
            "4",
        ]);
        cmd.stdout(log_out);
        cmd.stderr(log_err);

        // Drop privileges to SUDO_USER if running as root
        let is_root = unsafe { libc::getuid() } == 0;
        if is_root {
            if let Some(sudo_uid) = env::var("SUDO_UID").ok().and_then(|s| s.parse::<u32>().ok()) {
                let sudo_gid = env::var("SUDO_GID").ok().and_then(|s| s.parse::<u32>().ok()).unwrap_or(sudo_uid);
                use std::os::unix::process::CommandExt;
                cmd.uid(sudo_uid);
                cmd.gid(sudo_gid);
                cmd.env("HOME", &home_dir);
                if let Ok(sudo_user) = env::var("SUDO_USER") {
                    cmd.env("USER", &sudo_user);
                    cmd.env("LOGNAME", &sudo_user);
                }
            }
        }

        let child = cmd.spawn()?;
        let pid = child.id();
        let _ = std::fs::write(&pid_file, pid.to_string());
        SPAWNED_BY_DAEMON.store(true, Ordering::SeqCst);

        // Wait up to 10s for readiness
        for _ in 0..50 {
            tokio::time::sleep(Duration::from_millis(200)).await;
            if Self::is_healthy(port).await {
                tracing::info!("llama-server is READY on http://127.0.0.1:{} (PID {})", port, pid);
                return Ok(());
            }
        }

        tracing::warn!("llama-server spawned (PID {}), still initializing...", pid);
        Ok(())
    }

    pub fn stop() {
        let pid_file = Self::get_pid_file_path();
        if !pid_file.exists() {
            return;
        }

        if let Ok(content) = std::fs::read_to_string(&pid_file) {
            if let Ok(pid) = content.trim().parse::<i32>() {
                tracing::info!("Stopping llama-server (PID {})...", pid);
                unsafe {
                    libc::kill(pid, libc::SIGTERM);
                }
                std::thread::sleep(Duration::from_millis(250));
                let ret = unsafe { libc::kill(pid, 0) };
                if ret == 0 {
                    std::thread::sleep(Duration::from_millis(500));
                    let _ = unsafe { libc::kill(pid, libc::SIGKILL) };
                }
                tracing::info!("llama-server stopped. Discrete GPU VRAM and RAM freed.");
            }
        }
        let _ = std::fs::remove_file(&pid_file);
    }
}
