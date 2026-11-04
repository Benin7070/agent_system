//! Hotkey listener module: binds hardware hotkeys (specifically KEY_PROG2, keycode 149)
//! via Linux evdev (`/dev/input/event*`) to the AgentCTL FSM and overlay UI.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use crate::api::handlers::AppState;
use crate::core::kill::kill_all_managed;
use crate::fsm::states::FsmState;

/// Linux input event type for key events (`EV_KEY`)
pub const EV_KEY: u16 = 1;

/// Linux input event code for `KEY_PROG2` (Programmable Key 2, HP WMI hotkey)
pub const KEY_PROG2: u16 = 149;

/// Default event device for HP WMI hotkeys
pub const DEFAULT_HOTKEY_DEVICE: &str = "/dev/input/event13";

/// Resulting action of a hotkey toggle
#[derive(Debug, PartialEq, Eq, Clone)]
pub enum HotkeyAction {
    Activated,
    Cancelled {
        from_state: String,
        task_id: Option<String>,
    },
}

/// Discovered Linux input device
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DiscoveredDevice {
    pub name: String,
    pub path: PathBuf,
}

/// Parses `/proc/bus/input/devices` content to find the device node providing `target_key`.
pub fn parse_proc_bus_input_devices(content: &str, target_key: u16) -> Option<DiscoveredDevice> {
    for block in content.split("\n\n") {
        let lines: Vec<&str> = block.lines().collect();
        let name = lines
            .iter()
            .find(|l| l.starts_with("N: Name="))
            .map(|l| {
                l.trim_start_matches("N: Name=")
                    .trim_matches('"')
                    .to_string()
            })
            .unwrap_or_else(|| "Unknown".to_string());

        let handlers = lines.iter().find(|l| l.starts_with("H: Handlers="));
        let key_line = lines.iter().find(|l| l.starts_with("B: KEY="));

        if let (Some(h_line), Some(k_line)) = (handlers, key_line) {
            let key_hex_str = k_line.trim_start_matches("B: KEY=").trim();
            let words: Vec<u64> = key_hex_str
                .split_whitespace()
                .rev()
                .filter_map(|s| u64::from_str_radix(s, 16).ok())
                .collect();

            let word_idx = (target_key / 64) as usize;
            let bit_idx = target_key % 64;

            if word_idx < words.len() && ((words[word_idx] >> bit_idx) & 1) == 1 {
                let handlers_str = h_line.trim_start_matches("H: Handlers=").trim();
                for part in handlers_str.split_whitespace() {
                    if part.starts_with("event") {
                        let path = PathBuf::from(format!("/dev/input/{}", part));
                        return Some(DiscoveredDevice { name, path });
                    }
                }
            }
        }
    }
    None
}

/// Finds the hotkey device providing `target_key` (via AGENTCTL_HOTKEY_DEVICE,
/// `/proc/bus/input/devices`, or fallback to `/dev/input/event13`).
pub fn find_hotkey_device(target_key: u16) -> Option<DiscoveredDevice> {
    // 1. Explicit environment variable override
    if let Ok(dev_str) = std::env::var("AGENTCTL_HOTKEY_DEVICE") {
        let trimmed = dev_str.trim();
        if !trimmed.is_empty() {
            let p = PathBuf::from(trimmed);
            return Some(DiscoveredDevice {
                name: "Custom Device (via AGENTCTL_HOTKEY_DEVICE)".to_string(),
                path: p,
            });
        }
    }

    // 2. Auto-discovery via /proc/bus/input/devices
    if let Ok(content) = std::fs::read_to_string("/proc/bus/input/devices") {
        if let Some(dev) = parse_proc_bus_input_devices(&content, target_key) {
            return Some(dev);
        }
    }

    // 3. Fallback to /dev/input/event13 if it exists
    let fallback = PathBuf::from(DEFAULT_HOTKEY_DEVICE);
    if fallback.exists() {
        return Some(DiscoveredDevice {
            name: "Fallback Default (event13)".to_string(),
            path: fallback,
        });
    }

    None
}

/// Resolves the python binary to use for launching the UI overlay.
pub fn resolve_python_path() -> PathBuf {
    if let Ok(py) = std::env::var("AGENTCTL_PYTHON") {
        return PathBuf::from(py);
    }
    if let Ok(mut dir) = std::env::current_dir() {
        loop {
            let candidate = dir.join(".venv/bin/python");
            if candidate.exists() {
                return candidate;
            }
            if !dir.pop() {
                break;
            }
        }
    }
    PathBuf::from("python3")
}

/// Spawns the overlay UI subprocess if not already running.
pub async fn spawn_overlay(
    overlay_child: &Arc<tokio::sync::Mutex<Option<tokio::process::Child>>>,
) {
    #[cfg(test)]
    {
        let _ = overlay_child;
    }

    #[cfg(not(test))]
    {
        let python = resolve_python_path();
        tracing::info!("Spawning overlay UI with Python: {}", python.display());

        let mut cmd = tokio::process::Command::new(&python);
        cmd.arg("-m").arg("agentctl.ui.overlay");

        if let Ok(mut dir) = std::env::current_dir() {
            loop {
                let src_candidate = dir.join("src");
                if src_candidate.join("agentctl").exists() {
                    let current_ppath = std::env::var("PYTHONPATH").unwrap_or_default();
                    let new_ppath = if current_ppath.is_empty() {
                        src_candidate.display().to_string()
                    } else {
                        format!("{}:{}", src_candidate.display(), current_ppath)
                    };
                    cmd.env("PYTHONPATH", new_ppath);
                    break;
                }
                if !dir.pop() {
                    break;
                }
            }
        }

        match cmd.spawn() {
            Ok(child) => {
                let mut lock = overlay_child.lock().await;
                *lock = Some(child);
            }
            Err(e) => {
                tracing::error!("Failed to spawn overlay UI: {}", e);
            }
        }
    }
}

/// Core toggle logic:
/// - If FSM is `Idle`: transition to `Listening`, log audit event, and spawn the overlay UI.
/// - If FSM is active: cancel active intent, release gate/containers, log audit, and reap overlay.
pub async fn handle_hotkey_toggle(
    state: &AppState,
    overlay_child: &Arc<tokio::sync::Mutex<Option<tokio::process::Child>>>,
) -> Result<HotkeyAction, anyhow::Error> {
    let current_state = state.fsm.get_state().await;
    tracing::info!(
        "Hotkey KEY_PROG2 (149) triggered. Current FSM state: {:?}",
        current_state
    );

    if current_state == FsmState::Idle {
        state.fsm.transition_to(FsmState::Listening).await?;
        let _ = state
            .audit
            .append(
                "HOTKEY_ACTIVATE",
                &serde_json::json!({
                    "key": "KEY_PROG2",
                    "code": KEY_PROG2,
                }),
            )
            .await;

        spawn_overlay(overlay_child).await;
        Ok(HotkeyAction::Activated)
    } else {
        let task_id = state.fsm.get_current_task().await;
        let (prev_state, cancelled) = state.fsm.cancel().await;

        if cancelled {
            if let Some(ref tid) = task_id {
                let _ = state.gate.cancel(tid).await;
                let _ = kill_all_managed(Some(tid)).await;
            }

            let _ = state
                .audit
                .append(
                    "HOTKEY_CANCEL",
                    &serde_json::json!({
                        "cancelled_from": prev_state.as_str(),
                        "task_id": task_id,
                        "key": "KEY_PROG2",
                        "code": KEY_PROG2,
                    }),
                )
                .await;
        }

        let child_slot = overlay_child.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_millis(400)).await;
            let mut lock = child_slot.lock().await;
            if let Some(mut child) = lock.take() {
                match child.try_wait() {
                    Ok(Some(_)) => {}
                    Ok(None) => {
                        let _ = child.kill().await;
                    }
                    Err(_) => {}
                }
            }
        });

        Ok(HotkeyAction::Cancelled {
            from_state: prev_state.as_str().to_string(),
            task_id,
        })
    }
}

/// Spawns a background task running the hotkey listener.
pub fn spawn_hotkey_listener(state: Arc<AppState>) -> tokio::task::JoinHandle<()> {
    let overlay_child: Arc<tokio::sync::Mutex<Option<tokio::process::Child>>> =
        Arc::new(tokio::sync::Mutex::new(None));

    tokio::spawn(async move {
        run_hotkey_listener_loop(state, overlay_child).await;
    })
}

/// Persistent listener loop with automatic device re-discovery and permission handling.
pub async fn run_hotkey_listener_loop(
    state: Arc<AppState>,
    overlay_child: Arc<tokio::sync::Mutex<Option<tokio::process::Child>>>,
) {
    loop {
        let dev = match find_hotkey_device(KEY_PROG2) {
            Some(d) => d,
            None => {
                tracing::warn!(
                    "No hotkey device found for KEY_PROG2 ({}) in /proc/bus/input/devices or {}. Retrying in 5s...",
                    KEY_PROG2,
                    DEFAULT_HOTKEY_DEVICE
                );
                tokio::time::sleep(Duration::from_secs(5)).await;
                continue;
            }
        };

        tracing::info!(
            "Attempting to bind hotkey KEY_PROG2 (149) on {} ({})",
            dev.path.display(),
            dev.name
        );

        let path = dev.path.clone();
        let state_clone = state.clone();
        let overlay_clone = overlay_child.clone();

        let res = tokio::task::spawn_blocking(move || {
            listen_device_blocking(&path, state_clone, overlay_clone)
        })
        .await;

        match res {
            Ok(Ok(())) => {
                tracing::info!("Hotkey listener completed.");
                break;
            }
            Ok(Err(e)) => {
                tracing::warn!(
                    "Hotkey listener error on {}: {}. Retrying in 5s...",
                    dev.path.display(),
                    e
                );
            }
            Err(join_err) => {
                tracing::error!("Hotkey listener blocking task panicked: {}", join_err);
            }
        }

        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}

/// Synchronously reads Linux evdev events from `path` until an error occurs.
fn listen_device_blocking(
    path: &Path,
    state: Arc<AppState>,
    overlay_child: Arc<tokio::sync::Mutex<Option<tokio::process::Child>>>,
) -> Result<(), anyhow::Error> {
    use std::io::Read;

    let mut file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(e) => {
            if e.kind() == std::io::ErrorKind::PermissionDenied {
                tracing::warn!(
                    "Permission denied reading {}. To allow non-root hotkey listening, run: sudo usermod -a -G input $USER (or run agentctl-daemon with sudo).",
                    path.display()
                );
            }
            return Err(e.into());
        }
    };

    println!(
        "[Hotkey] Bound KEY_PROG2 (149) on {}! Ready for hardware hotkey events.",
        path.display()
    );
    tracing::info!(
        "Bound KEY_PROG2 (149) on {}! Ready for hardware hotkey events.",
        path.display()
    );

    let event_size = std::mem::size_of::<libc::input_event>();
    let mut buf = vec![0u8; event_size];
    let mut last_press = Instant::now() - Duration::from_secs(10);

    let rt = tokio::runtime::Handle::try_current().ok();

    loop {
        file.read_exact(&mut buf)?;
        let ev: libc::input_event =
            unsafe { std::ptr::read_unaligned(buf.as_ptr() as *const _) };

        if ev.type_ == EV_KEY && ev.code == KEY_PROG2 {
            if ev.value == 1 {
                // Key pressed
                let now = Instant::now();
                if now.duration_since(last_press) < Duration::from_millis(250) {
                    // Debounce rapid double pulses
                    continue;
                }
                last_press = now;

                println!("[Hotkey] KEY_PROG2 (149) PRESSED -> Toggling state");
                tracing::info!("[Hotkey] KEY_PROG2 (149) PRESSED -> Toggling state");

                let st = state.clone();
                let oc = overlay_child.clone();

                if let Some(ref handle) = rt {
                    handle.spawn(async move {
                        if let Err(e) = handle_hotkey_toggle(&st, &oc).await {
                            tracing::error!("Failed to handle hotkey toggle: {}", e);
                        }
                    });
                }
            } else if ev.value == 0 {
                tracing::trace!("[Hotkey] KEY_PROG2 (149) released");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::audit::AuditLog;
    use crate::core::gate::ApprovalGate;
    use crate::fsm::OrchestratorFsm;
    use sqlx::sqlite::SqlitePoolOptions;

    #[test]
    fn test_parse_proc_bus_input_devices_hp_wmi() {
        let mock_devices = r#"
I: Bus=0019 Vendor=0000 Product=0000 Version=0000
N: Name="HP WMI hotkeys"
P: Phys=wmi/input0
S: Sysfs=/devices/virtual/input/input17
U: Uniq=
H: Handlers=kbd event13 
B: PROP=0
B: EV=33
B: KEY=180000 20000 0 4000000000 0 101000700000000 2302400 0 0
B: MSC=10
B: SW=20

I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=PNP0C0C/button/input0
S: Sysfs=/devices/platform/PNP0C0C:00/input/input1
U: Uniq=
H: Handlers=kbd event1 
B: PROP=0
B: EV=3
B: KEY=8000 10000000000000 0
"#;

        let dev = parse_proc_bus_input_devices(mock_devices, KEY_PROG2).expect("Must find HP WMI hotkeys");
        assert_eq!(dev.name, "HP WMI hotkeys");
        assert_eq!(dev.path, PathBuf::from("/dev/input/event13"));
    }

    #[test]
    fn test_parse_proc_bus_input_devices_not_found() {
        let mock_devices = r#"
I: Bus=0019 Vendor=0000 Product=0001 Version=0000
N: Name="Power Button"
P: Phys=PNP0C0C/button/input0
S: Sysfs=/devices/platform/PNP0C0C:00/input/input1
U: Uniq=
H: Handlers=kbd event1 
B: PROP=0
B: EV=3
B: KEY=8000 10000000000000 0
"#;

        let dev = parse_proc_bus_input_devices(mock_devices, KEY_PROG2);
        assert!(dev.is_none());
    }

    #[tokio::test]
    async fn test_hotkey_toggle_idle_to_listening() {
        let pool = SqlitePoolOptions::new()
            .connect("sqlite::memory:")
            .await
            .unwrap();
        let audit = AuditLog::new(pool);
        audit.init().await.unwrap();
        let gate = ApprovalGate::new();
        let fsm = OrchestratorFsm::new();
        let state = AppState::new(fsm, gate, audit);
        let overlay_slot = Arc::new(tokio::sync::Mutex::new(None));

        assert_eq!(state.fsm.get_state().await, FsmState::Idle);

        let action = handle_hotkey_toggle(&state, &overlay_slot)
            .await
            .expect("Toggle must succeed");

        assert_eq!(action, HotkeyAction::Activated);
        assert_eq!(state.fsm.get_state().await, FsmState::Listening);
    }

    #[tokio::test]
    async fn test_hotkey_toggle_listening_to_cancel() {
        let pool = SqlitePoolOptions::new()
            .connect("sqlite::memory:")
            .await
            .unwrap();
        let audit = AuditLog::new(pool);
        audit.init().await.unwrap();
        let gate = ApprovalGate::new();
        let fsm = OrchestratorFsm::new();
        let state = AppState::new(fsm, gate, audit);
        let overlay_slot = Arc::new(tokio::sync::Mutex::new(None));

        state.fsm.transition_to(FsmState::Listening).await.unwrap();
        assert_eq!(state.fsm.get_state().await, FsmState::Listening);

        let action = handle_hotkey_toggle(&state, &overlay_slot)
            .await
            .expect("Toggle must succeed");

        match action {
            HotkeyAction::Cancelled { from_state, .. } => {
                assert_eq!(from_state, "LISTENING");
            }
            _ => panic!("Expected Cancelled action"),
        }
        assert_eq!(state.fsm.get_state().await, FsmState::Idle);
    }

    #[test]
    fn test_input_event_struct_layout() {
        let ev = libc::input_event {
            time: libc::timeval {
                tv_sec: 100,
                tv_usec: 500,
            },
            type_: EV_KEY,
            code: KEY_PROG2,
            value: 1,
        };
        assert_eq!(ev.type_, 1);
        assert_eq!(ev.code, 149);
        assert_eq!(ev.value, 1);
    }
}
