use agentctl_daemon::api::create_router;
use agentctl_daemon::api::handlers::AppState;
use agentctl_daemon::core::audit::AuditLog;
use agentctl_daemon::core::gate::ApprovalGate;
use agentctl_daemon::fsm::states::FsmState;
use agentctl_daemon::fsm::OrchestratorFsm;
use futures_util::StreamExt;
use sqlx::sqlite::SqlitePoolOptions;
use std::sync::Arc;
use std::time::Duration;
use tokio::net::TcpListener;
use tokio_tungstenite::connect_async;
use tokio_tungstenite::tungstenite::Message;

#[tokio::test]
async fn test_websocket_state_and_level_push() {
    let pool = SqlitePoolOptions::new()
        .connect("sqlite::memory:")
        .await
        .unwrap();

    let audit = AuditLog::new(pool);
    audit.init().await.unwrap();

    let fsm = OrchestratorFsm::new();
    let gate = ApprovalGate::new();
    let state = Arc::new(AppState::new(fsm.clone(), gate, audit));
    let app = create_router(state.clone());

    // Bind on ephemeral local TCP port for testing WebSocket client
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    tokio::spawn(async move {
        axum::serve(listener, app).await.unwrap();
    });

    let ws_url = format!("ws://{}/ws", addr);
    let (mut ws_stream, _) = connect_async(&ws_url)
        .await
        .expect("WebSocket client must connect");

    // 1. First message must be initial state "idle"
    let msg = ws_stream.next().await.unwrap().unwrap();
    if let Message::Text(text) = msg {
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert_eq!(v["type"], "state");
        assert_eq!(v["state"], "idle");
    } else {
        panic!("Expected text message");
    }

    // 2. State change in FSM -> must immediately push state event
    fsm.transition_to(FsmState::Listening).await.unwrap();

    let mut state_received = false;
    // Next message could be level tick or state; read until state event
    for _ in 0..5 {
        let msg = ws_stream.next().await.unwrap().unwrap();
        if let Message::Text(text) = msg {
            let v: serde_json::Value = serde_json::from_str(&text).unwrap();
            if v["type"] == "state" {
                assert_eq!(v["state"], "listening");
                state_received = true;
                break;
            }
        }
    }
    assert!(state_received, "Must receive state transition to listening");

    // 3. Level updates via watch channel
    state.level_tx.send(0.85).unwrap();

    let mut level_received = false;
    for _ in 0..10 {
        let msg = ws_stream.next().await.unwrap().unwrap();
        if let Message::Text(text) = msg {
            let v: serde_json::Value = serde_json::from_str(&text).unwrap();
            if v["type"] == "level" && (v["value"].as_f64().unwrap() - 0.85).abs() < 1e-4 {
                level_received = true;
                break;
            }
        }
    }
    assert!(level_received, "Must receive level value 0.85");

    // 4. Test rapid level updates to ensure stale levels are dropped
    for i in 0..10 {
        let _ = state.level_tx.send(0.1 * i as f32);
    }
    state.level_tx.send(0.99).unwrap();

    let mut latest_level = 0.0;
    for _ in 0..10 {
        let Ok(Some(Ok(Message::Text(text)))) =
            tokio::time::timeout(Duration::from_millis(100), ws_stream.next()).await
        else {
            break;
        };
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        if v["type"] == "level" {
            latest_level = v["value"].as_f64().unwrap();
            if (latest_level - 0.99).abs() < 1e-4 {
                break;
            }
        }
    }
    assert!(
        (latest_level - 0.99).abs() < 1e-4,
        "Watch channel must deliver latest conflated level"
    );

    let _ = ws_stream.close(None).await;
}
