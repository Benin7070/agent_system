use agentctl_daemon::core::audit::{AuditError, AuditLog};
use serde_json::json;
use sqlx::sqlite::SqlitePoolOptions;

#[tokio::test]
async fn test_audit_log_chain_and_tamper_detection() {
    let pool = SqlitePoolOptions::new()
        .max_connections(1)
        .connect("sqlite::memory:")
        .await
        .expect("in-memory db");

    let audit = AuditLog::new(pool.clone());
    audit.init().await.expect("init audit log");

    // Event 1
    let payload1 = json!({
        "action": "file_read",
        "path": "/safe/data.txt"
    });
    let hash1 = audit.append("INTENT_READ", &payload1).await.expect("append 1");
    assert!(!hash1.is_empty());

    // Event 2 (contains sensitive secret)
    let payload2 = json!({
        "action": "connect",
        "api_key": "sk-secret-12345",
        "nested": {
            "password": "super-password"
        }
    });
    let hash2 = audit.append("CONNECT", &payload2).await.expect("append 2");
    assert!(!hash2.is_empty());

    // Event 3
    let payload3 = json!({
        "action": "log_message",
        "msg": "all done"
    });
    let _hash3 = audit.append("DONE", &payload3).await.expect("append 3");

    // Verify secret scrubbing before write
    let row2: (String,) = sqlx::query_as("SELECT payload FROM audit_log WHERE id = 2")
        .fetch_one(&pool)
        .await
        .expect("fetch row 2");
    assert!(
        row2.0.contains("[REDACTED]"),
        "Secrets must be scrubbed before saving payload"
    );
    assert!(
        !row2.0.contains("sk-secret-12345"),
        "Raw secret must never be written to audit log"
    );
    assert!(
        !row2.0.contains("super-password"),
        "Raw password must never be written to audit log"
    );

    // Initial chain verification MUST pass
    audit.verify_chain().await.expect("chain must be valid initially");

    // TAMPER TEST: Artificially alter the payload in row 2
    sqlx::query("UPDATE audit_log SET payload = '{\"action\":\"malicious_injection\"}' WHERE id = 2")
        .execute(&pool)
        .await
        .expect("tamper query");

    // Chain verification MUST now fail with TamperDetected
    let verify_res = audit.verify_chain().await;
    match verify_res {
        Err(AuditError::TamperDetected { id, reason }) => {
            assert_eq!(id, 2);
            assert!(reason.contains("Invalid row hash"));
        }
        other => panic!("Expected TamperDetected on tampered row, got {:?}", other),
    }
}

