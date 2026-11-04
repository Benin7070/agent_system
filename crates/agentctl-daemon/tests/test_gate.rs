use agentctl_daemon::core::gate::{compute_action_hash, ApprovalGate, GateError};
use agentctl_daemon::core::schemas::ProposedAction;
use std::path::PathBuf;

#[tokio::test]
async fn test_approval_gate_normal_and_mutated_hash_mismatch() {
    let gate = ApprovalGate::new();

    let safe_action = ProposedAction::FileWrite {
        path: PathBuf::from("/workspace/code.py"),
        content: "print('hello')".to_string(),
    };

    let prompt = gate
        .request_approval("task-100", &safe_action)
        .await
        .expect("request approval");

    // Check deterministic summary
    assert!(prompt.deterministic_summary.contains("FILE WRITE"));
    assert!(prompt.deterministic_summary.contains("/workspace/code.py"));
    assert!(!prompt.action_hash.is_empty());

    // TAMPER TEST: Presented hash is different / forged
    let fake_hash = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    let fail_res = gate.verify_and_consume("task-100", fake_hash).await;
    match fail_res {
        Err(GateError::HashMismatch { stored, provided }) => {
            assert_eq!(stored, prompt.action_hash);
            assert_eq!(provided, fake_hash);
        }
        other => panic!("Expected HashMismatch, got {:?}", other),
    }

    // Now re-register and verify valid hash succeeds
    let prompt2 = gate
        .request_approval("task-100", &safe_action)
        .await
        .expect("request approval 2");

    let approved_action = gate
        .verify_and_consume("task-100", &prompt2.action_hash)
        .await
        .expect("valid hash must be approved");

    assert_eq!(approved_action, safe_action);
}

#[tokio::test]
async fn test_approval_hash_changes_on_action_mutation() {
    let original = ProposedAction::FileDelete {
        path: PathBuf::from("/tmp/safe.log"),
    };
    let mutated = ProposedAction::FileDelete {
        path: PathBuf::from("/tmp/critical.log"),
    };

    let hash1 = compute_action_hash(&original).unwrap();
    let hash2 = compute_action_hash(&mutated).unwrap();

    assert_ne!(
        hash1, hash2,
        "Mutating any field of an action MUST alter its canonical hash"
    );
}

#[tokio::test]
async fn test_approval_cancellation() {
    let gate = ApprovalGate::new();
    let action = ProposedAction::LogMessage {
        message: "hello".to_string(),
    };

    let prompt = gate.request_approval("task-cancel", &action).await.unwrap();
    assert!(gate.cancel("task-cancel").await);

    // After cancellation, verifying must return NotFound
    let res = gate.verify_and_consume("task-cancel", &prompt.action_hash).await;
    assert_eq!(res, Err(GateError::NotFound("task-cancel".to_string())));
}

