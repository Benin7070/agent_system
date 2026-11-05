//! Integration test suite for Hardware Hotkey listener and FSM state toggle.

use agentctl_daemon::api::handlers::AppState;
use agentctl_daemon::core::audit::AuditLog;
use agentctl_daemon::core::gate::ApprovalGate;
use agentctl_daemon::fsm::states::FsmState;
use agentctl_daemon::fsm::OrchestratorFsm;
use agentctl_daemon::hotkey::{
    handle_hotkey_toggle, parse_proc_bus_input_devices, HotkeyAction, EV_KEY, KEY_PROG2,
};
use sqlx::sqlite::SqlitePoolOptions;
use std::path::PathBuf;
use std::sync::Arc;

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
    std::env::set_var("AGENTCTL_TEST_MODE", "1");
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
    std::env::set_var("AGENTCTL_TEST_MODE", "1");
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
