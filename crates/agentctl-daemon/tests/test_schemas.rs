use agentctl_daemon::core::schemas::{
    ApiResponse, CancelResponse, IntentRequest, PermissionToken, ProposedAction, StatusResponse,
    ValidatorResult,
};
use chrono::Utc;
use std::path::PathBuf;
use url::Url;

#[test]
fn test_serde_proposed_action_roundtrip() {
    let actions = vec![
        ProposedAction::FileRead {
            path: PathBuf::from("/tmp/test.txt"),
        },
        ProposedAction::FileWrite {
            path: PathBuf::from("/tmp/out.txt"),
            content: "hello world".to_string(),
        },
        ProposedAction::FileMove {
            src: PathBuf::from("/tmp/a.txt"),
            dst: PathBuf::from("/tmp/b.txt"),
        },
        ProposedAction::FileDelete {
            path: PathBuf::from("/tmp/dead.txt"),
        },
        ProposedAction::ShellExec {
            command: "ls".to_string(),
            args: vec!["-la".to_string(), "/tmp".to_string()],
        },
        ProposedAction::BrowserNav {
            url: Url::parse("https://example.com/api").unwrap(),
        },
        ProposedAction::LogMessage {
            message: "step done".to_string(),
        },
    ];

    for action in actions {
        let json = serde_json::to_string(&action).expect("serialize failed");
        let decoded: ProposedAction = serde_json::from_str(&json).expect("deserialize failed");
        assert_eq!(action, decoded);
    }
}

#[test]
fn test_unknown_action_type_is_rejected() {
    let json = r#"{"type": "evil_exec", "command": "rm -rf /"}"#;
    let res: Result<ProposedAction, _> = serde_json::from_str(json);
    assert!(res.is_err(), "Unknown action type must be rejected");
}

#[test]
fn test_permission_token_roundtrip() {
    let token = PermissionToken {
        id: "token-123".to_string(),
        allowed_actions: vec!["file_read".to_string(), "file_write".to_string()],
        allowed_paths: vec![PathBuf::from("/safe/workspace")],
        allowed_domains: vec!["example.com".to_string()],
        expires_at: Utc::now(),
        revoked: false,
    };

    let json = serde_json::to_string(&token).unwrap();
    let decoded: PermissionToken = serde_json::from_str(&json).unwrap();
    assert_eq!(token.id, decoded.id);
    assert_eq!(token.allowed_actions, decoded.allowed_actions);
    assert_eq!(token.allowed_paths, decoded.allowed_paths);
    assert_eq!(token.revoked, decoded.revoked);
}

#[test]
fn test_api_responses_roundtrip() {
    let status = StatusResponse {
        state: "LISTENING".to_string(),
        task_id: Some("task-42".to_string()),
        error: None,
        details: None,
    };
    let resp = ApiResponse::success(status.clone());
    let json = serde_json::to_string(&resp).unwrap();
    let decoded: ApiResponse<StatusResponse> = serde_json::from_str(&json).unwrap();
    assert!(decoded.ok);
    assert_eq!(decoded.data.unwrap().state, "LISTENING");

    let cancel = CancelResponse {
        ok: true,
        cancelled_from: "EXECUTING".to_string(),
        task_id: Some("task-99".to_string()),
    };
    let c_json = serde_json::to_string(&cancel).unwrap();
    let c_dec: CancelResponse = serde_json::from_str(&c_json).unwrap();
    assert_eq!(c_dec.cancelled_from, "EXECUTING");

    let intent = IntentRequest {
        text: "open calendar".to_string(),
        session_id: Some("sess-1".to_string()),
    };
    let i_json = serde_json::to_string(&intent).unwrap();
    let i_dec: IntentRequest = serde_json::from_str(&i_json).unwrap();
    assert_eq!(i_dec.text, "open calendar");

    let val = ValidatorResult::deny("blocked path");
    assert!(!val.allowed);
    assert_eq!(val.reason, "blocked path");
}
