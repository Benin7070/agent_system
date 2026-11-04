use agentctl_daemon::api::create_router;
use agentctl_daemon::api::handlers::AppState;
use agentctl_daemon::core::audit::AuditLog;
use agentctl_daemon::core::gate::ApprovalGate;
use agentctl_daemon::core::schemas::{ApiResponse, CancelResponse, StatusResponse};
use agentctl_daemon::fsm::states::FsmState;
use agentctl_daemon::fsm::{FsmError, OrchestratorFsm};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use sqlx::sqlite::SqlitePoolOptions;
use std::sync::Arc;
use tower::ServiceExt;

#[tokio::test]
async fn test_fsm_valid_and_invalid_transitions() {
    let fsm = OrchestratorFsm::new();
    assert_eq!(fsm.get_state().await, FsmState::Idle);

    // Valid transition: Idle -> Listening
    fsm.transition_to(FsmState::Listening)
        .await
        .expect("Idle -> Listening must succeed");
    assert_eq!(fsm.get_state().await, FsmState::Listening);

    // Invalid transition: Listening -> Executing directly
    let invalid = fsm.transition_to(FsmState::Executing).await;
    assert_eq!(
        invalid,
        Err(FsmError::InvalidTransition {
            from: FsmState::Listening,
            to: FsmState::Executing
        })
    );

    // Valid: Listening -> Processing
    fsm.transition_to(FsmState::Processing)
        .await
        .expect("Listening -> Processing must succeed");

    // Valid: Processing -> AwaitingApproval
    fsm.transition_to(FsmState::AwaitingApproval)
        .await
        .expect("Processing -> AwaitingApproval must succeed");

    // Valid: AwaitingApproval -> Executing
    fsm.transition_to(FsmState::Executing)
        .await
        .expect("AwaitingApproval -> Executing must succeed");

    // Valid: Executing -> Idle
    fsm.transition_to(FsmState::Idle)
        .await
        .expect("Executing -> Idle must succeed");
    assert_eq!(fsm.get_state().await, FsmState::Idle);
}

#[tokio::test]
async fn test_fsm_cancel_from_any_active_state() {
    let fsm = OrchestratorFsm::new();

    // Cancel from Listening
    fsm.transition_to(FsmState::Listening).await.unwrap();
    let (prev, cancelled) = fsm.cancel().await;
    assert_eq!(prev, FsmState::Listening);
    assert!(cancelled);
    assert_eq!(fsm.get_state().await, FsmState::Idle);

    // Cancel from Executing
    fsm.transition_to(FsmState::Listening).await.unwrap();
    fsm.transition_to(FsmState::Processing).await.unwrap();
    fsm.transition_to(FsmState::Executing).await.unwrap();

    let (prev2, cancelled2) = fsm.cancel().await;
    assert_eq!(prev2, FsmState::Executing);
    assert!(cancelled2);
    assert_eq!(fsm.get_state().await, FsmState::Idle);
}

#[tokio::test]
async fn test_api_activate_status_and_cancel_endpoints() {
    let pool = SqlitePoolOptions::new()
        .connect("sqlite::memory:")
        .await
        .unwrap();

    let audit = AuditLog::new(pool);
    audit.init().await.unwrap();

    let state = Arc::new(AppState {
        fsm: OrchestratorFsm::new(),
        gate: ApprovalGate::new(),
        audit,
    });

    let app = create_router(state);

    // 1. Initial status -> IDLE
    let req = Request::builder()
        .method("GET")
        .uri("/status")
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);

    // 2. POST /activate
    let req = Request::builder()
        .method("POST")
        .uri("/activate")
        .header("content-type", "application/json")
        .body(Body::from("{}"))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), 1024 * 16).await.unwrap();
    let api_res: ApiResponse<StatusResponse> = serde_json::from_slice(&body).unwrap();
    assert!(api_res.ok);
    assert_eq!(api_res.data.unwrap().state, "LISTENING");

    // 3. POST /cancel (simulating second hotkey press)
    let req = Request::builder()
        .method("POST")
        .uri("/cancel")
        .header("content-type", "application/json")
        .body(Body::from("{}"))
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::OK);
    let body = axum::body::to_bytes(resp.into_body(), 1024 * 16).await.unwrap();
    let api_res: ApiResponse<CancelResponse> = serde_json::from_slice(&body).unwrap();
    assert!(api_res.ok);
    let cancel_data = api_res.data.unwrap();
    assert_eq!(cancel_data.cancelled_from, "LISTENING");

    // 4. Verify returned to IDLE
    let req = Request::builder()
        .method("GET")
        .uri("/status")
        .body(Body::empty())
        .unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    let body = axum::body::to_bytes(resp.into_body(), 1024 * 16).await.unwrap();
    let api_res: ApiResponse<StatusResponse> = serde_json::from_slice(&body).unwrap();
    assert_eq!(api_res.data.unwrap().state, "IDLE");
}

