use crate::core::audit::AuditLog;
use crate::core::gate::ApprovalGate;
use crate::core::kill::kill_all_managed;
use crate::core::schemas::{
    ApiResponse, ApprovalDecision, CancelResponse, IntentRequest, StatusResponse,
};
use crate::fsm::states::FsmState;
use crate::fsm::OrchestratorFsm;
use axum::{extract::State, Json};
use serde_json::json;
use std::sync::Arc;

#[derive(Clone)]
pub struct AppState {
    pub fsm: OrchestratorFsm,
    pub gate: ApprovalGate,
    pub audit: AuditLog,
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
