use crate::core::audit::AuditLog;
use crate::core::gate::ApprovalGate;
use crate::core::kill::kill_all_managed;
use crate::core::schemas::{
    ApiResponse, ApprovalDecision, CancelResponse, IntentRequest, StatusResponse,
};
use crate::fsm::states::FsmState;
use crate::fsm::OrchestratorFsm;
use crate::harness::session::{HarnessConfig, HarnessSession, SessionEvent};
use axum::{
    extract::{
        ws::{Message, WebSocket, WebSocketUpgrade},
        State,
    },
    Json,
};
use futures_util::{SinkExt, StreamExt};
use serde_json::json;
use std::sync::Arc;
use tokio::sync::{broadcast, watch};

#[derive(Clone)]
pub struct AppState {
    pub fsm: OrchestratorFsm,
    pub gate: ApprovalGate,
    pub audit: AuditLog,
    pub level_tx: watch::Sender<f32>,
    pub harness: Arc<HarnessSession>,
    pub session_event_tx: broadcast::Sender<SessionEvent>,
}

impl AppState {
    pub fn new(fsm: OrchestratorFsm, gate: ApprovalGate, audit: AuditLog) -> Self {
        let (level_tx, _) = watch::channel(0.0f32);
        let (session_event_tx, _) = broadcast::channel(64);

        let agent_name = std::env::var("AGENTCTL_AGENT_NAME").unwrap_or_else(|_| "Bella".to_string());
        let user_name = std::env::var("AGENTCTL_USER_NAME")
            .or_else(|_| std::env::var("SUDO_USER"))
            .or_else(|_| std::env::var("USER"))
            .unwrap_or_else(|_| "Benin".to_string());

        let harness_config = HarnessConfig {
            agent_name,
            user_name,
            ..Default::default()
        };
        let harness = Arc::new(HarnessSession::new(harness_config, fsm.clone(), None));

        Self {
            fsm,
            gate,
            audit,
            level_tx,
            harness,
            session_event_tx,
        }
    }
}

pub async fn activate_handler(
    State(state): State<Arc<AppState>>,
) -> Json<ApiResponse<StatusResponse>> {
    match state.fsm.transition_to(FsmState::Listening).await {
        Ok(s) => {
            let _ = state
                .audit
                .append("STATE_CHANGE", &json!({ "new_state": s.as_str() }))
                .await;
            Json(ApiResponse::success(StatusResponse {
                state: s.as_str().to_string(),
                task_id: None,
                error: None,
            }))
        }
        Err(e) => Json(ApiResponse::error(e.to_string())),
    }
}

pub async fn cancel_handler(
    State(state): State<Arc<AppState>>,
) -> Json<ApiResponse<CancelResponse>> {
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
                "CANCELLED",
                &json!({
                    "cancelled_from": prev_state.as_str(),
                    "task_id": task_id
                }),
            )
            .await;
    }

    Json(ApiResponse::success(CancelResponse {
        ok: true,
        cancelled_from: prev_state.as_str().to_string(),
        task_id,
    }))
}

pub async fn status_handler(
    State(state): State<Arc<AppState>>,
) -> Json<ApiResponse<StatusResponse>> {
    let s = state.fsm.get_state().await;
    let task = state.fsm.get_current_task().await;

    Json(ApiResponse::success(StatusResponse {
        state: s.as_str().to_string(),
        task_id: task,
        error: None,
    }))
}

pub async fn intent_handler(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<IntentRequest>,
) -> Json<ApiResponse<StatusResponse>> {
    let current = state.fsm.get_state().await;
    if current != FsmState::Listening && current != FsmState::Idle {
        return Json(ApiResponse::error(format!(
            "Cannot submit intent while in state {:?}",
            current
        )));
    }

    let task_id = format!("task-{}", &chrono::Utc::now().timestamp_millis());
    let _ = state.fsm.set_task(Some(task_id.clone())).await;

    let _ = state.fsm.transition_to(FsmState::Processing).await;
    let _ = state
        .audit
        .append(
            "INTENT_RECEIVED",
            &json!({
                "task_id": task_id,
                "text": payload.text,
                "session_id": payload.session_id
            }),
        )
        .await;

    // Dispatch utterance to the harness conversation session
    let harness = state.harness.clone();
    let session_tx = state.session_event_tx.clone();
    let text = payload.text.clone();
    tokio::spawn(async move {
        let (mpsc_tx, mut mpsc_rx) = tokio::sync::mpsc::channel(32);
        let forwarder = tokio::spawn(async move {
            while let Some(evt) = mpsc_rx.recv().await {
                let _ = session_tx.send(evt);
            }
        });
        if let Err(e) = harness.handle_user_utterance(&text, mpsc_tx).await {
            tracing::error!("Harness utterance error: {}", e);
        }
        let _ = forwarder.await;
    });

    Json(ApiResponse::success(StatusResponse {
        state: "PROCESSING".to_string(),
        task_id: Some(task_id),
        error: None,
    }))
}

pub async fn approve_handler(
    State(state): State<Arc<AppState>>,
    Json(decision): Json<ApprovalDecision>,
) -> Json<ApiResponse<String>> {
    match state.gate.verify_and_consume(&decision.task_id, &decision.hash).await {
        Ok(action) => {
            let _ = state.fsm.transition_to(FsmState::Executing).await;
            let _ = state
                .audit
                .append(
                    "ACTION_APPROVED",
                    &json!({
                        "task_id": decision.task_id,
                        "hash": decision.hash,
                        "action_type": action.action_type_name()
                    }),
                )
                .await;

            Json(ApiResponse::success("Action approved for execution".to_string()))
        }
        Err(e) => Json(ApiResponse::error(e.to_string())),
    }
}

pub async fn deny_handler(
    State(state): State<Arc<AppState>>,
    Json(decision): Json<ApprovalDecision>,
) -> Json<ApiResponse<String>> {
    let _ = state.gate.cancel(&decision.task_id).await;
    let _ = state.fsm.transition_to(FsmState::Idle).await;
    let _ = state
        .audit
        .append(
            "ACTION_DENIED",
            &json!({ "task_id": decision.task_id, "hash": decision.hash }),
        )
        .await;

    Json(ApiResponse::success("Action denied".to_string()))
}

pub async fn kill_handler(
    State(state): State<Arc<AppState>>,
) -> Json<ApiResponse<String>> {
    match kill_all_managed(None).await {
        Ok(report) => {
            let _ = state.fsm.cancel().await;
            let _ = state
                .audit
                .append(
                    "KILL_EXECUTED",
                    &json!({
                        "stopped_count": report.stopped_count,
                        "elapsed_ms": report.elapsed_ms
                    }),
                )
                .await;

            Json(ApiResponse::success(format!(
                "Killed {} managed containers in {}ms",
                report.stopped_count, report.elapsed_ms
            )))
        }
        Err(e) => Json(ApiResponse::error(e.to_string())),
    }
}

#[derive(Debug, serde::Deserialize)]
pub struct LevelRequest {
    pub value: f32,
}

pub async fn level_handler(
    State(state): State<Arc<AppState>>,
    Json(payload): Json<LevelRequest>,
) -> Json<ApiResponse<()>> {
    let _ = state.level_tx.send(payload.value.clamp(0.0, 1.0));
    Json(ApiResponse::success(()))
}

pub async fn ws_handler(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> axum::response::Response {
    ws.on_upgrade(|socket| handle_socket(socket, state))
}

async fn handle_socket(socket: WebSocket, state: Arc<AppState>) {
    let (mut sender, mut receiver) = socket.split();

    // 1. Send initial state immediately upon connection
    let initial_state = state.fsm.get_state().await.to_ui_state();
    let init_msg = json!({
        "type": "state",
        "state": initial_state
    });
    if sender
        .send(Message::Text(init_msg.to_string()))
        .await
        .is_err()
    {
        return;
    }

    let mut state_rx = state.fsm.subscribe_state();
    let mut level_rx = state.level_tx.subscribe();
    let mut session_event_rx = state.session_event_tx.subscribe();

    // Spawn reader task to handle incoming client messages (e.g., client level pushes or pings)
    let state_clone = state.clone();
    let mut read_task = tokio::spawn(async move {
        while let Some(Ok(msg)) = receiver.next().await {
            if let Message::Text(text) = msg {
                if let Ok(val) = serde_json::from_str::<serde_json::Value>(&text) {
                    let msg_type = val.get("type").and_then(|t| t.as_str());
                    if msg_type == Some("level") {
                        if let Some(lvl) = val.get("value").and_then(|v| v.as_f64()) {
                            let _ = state_clone.level_tx.send((lvl as f32).clamp(0.0, 1.0));
                        }
                    } else if msg_type == Some("tts_playback_finished") {
                        let _ = state_clone.harness.on_tts_playback_finished().await;
                    }
                }
            }
        }
    });

    // Write task: sends state changes immediately, sends level at ~50 Hz, and streams tts_chunks
    let mut write_task = tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_millis(20));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

        loop {
            tokio::select! {
                Ok(new_state) = state_rx.recv() => {
                    let msg = json!({
                        "type": "state",
                        "state": new_state.to_ui_state()
                    });
                    if sender.send(Message::Text(msg.to_string())).await.is_err() {
                        break;
                    }
                }
                Ok(session_evt) = session_event_rx.recv() => {
                    match session_evt {
                        SessionEvent::SentenceReady { sentence, is_final } => {
                            let msg = json!({
                                "type": "tts_chunk",
                                "sentence": sentence,
                                "is_final": is_final
                            });
                            if sender.send(Message::Text(msg.to_string())).await.is_err() {
                                break;
                            }
                        }
                        SessionEvent::ExitTriggered { goodbye_msg } => {
                            let msg = json!({
                                "type": "exit",
                                "goodbye_msg": goodbye_msg
                            });
                            if sender.send(Message::Text(msg.to_string())).await.is_err() {
                                break;
                            }
                        }
                        _ => {}
                    }
                }
                _ = interval.tick() => {
                    let val = *level_rx.borrow_and_update();
                    let msg = json!({
                        "type": "level",
                        "value": val
                    });
                    if sender.send(Message::Text(msg.to_string())).await.is_err() {
                        break;
                    }
                }
            }
        }
    });

    tokio::select! {
        _ = (&mut read_task) => write_task.abort(),
        _ = (&mut write_task) => read_task.abort(),
    }
}
