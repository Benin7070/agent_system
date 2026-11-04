use agentctl_daemon::core::audit::AuditLog;
use agentctl_daemon::core::scheduler::Scheduler;
use agentctl_daemon::core::schemas::ProposedAction;
use sqlx::sqlite::SqlitePoolOptions;
use std::fs;
use tempfile::tempdir;

#[tokio::test]
async fn test_scheduler_crash_resume_and_idempotence() {
    let pool = SqlitePoolOptions::new()
        .connect("sqlite::memory:")
        .await
        .unwrap();

    let audit = AuditLog::new(pool.clone());
    audit.init().await.unwrap();

    let scheduler = Scheduler::new(pool.clone(), audit);
    scheduler.init().await.unwrap();

    let dir = tempdir().unwrap();
    let file_a = dir.path().join("a.txt");
    let file_b = dir.path().join("b.txt");
    let file_c = dir.path().join("c.txt");

    let steps = vec![
        ProposedAction::FileWrite {
            path: file_a.clone(),
            content: "initial-a".to_string(),
        },
        ProposedAction::FileMove {
            src: file_a.clone(),
            dst: file_b.clone(),
        },
        ProposedAction::FileWrite {
            path: file_c.clone(),
            content: "final-c".to_string(),
        },
    ];

    let task_id = "task-crash-test";
    scheduler.register_plan(task_id, &steps).await.unwrap();

    // Simulate partial execution before crash: run step 0 and 1 manually
    fs::write(&file_a, "initial-a").unwrap();
    fs::rename(&file_a, &file_b).unwrap();

    // Mark step 0 and step 1 as COMPLETED in checkpoints table
    sqlx::query("UPDATE task_checkpoints SET status = 'COMPLETED' WHERE task_id = ? AND step_index IN (0, 1)")
        .bind(task_id)
        .execute(&pool)
        .await
        .unwrap();

    // RESUME AFTER CRASH: Run scheduler
    let count = scheduler.run_or_resume(task_id).await.unwrap();
    assert_eq!(count, 3, "All 3 steps must be accounted for after resume");

    // Verify system state: file_b exists, file_c exists
    assert!(file_b.exists());
    assert_eq!(fs::read_to_string(&file_b).unwrap(), "initial-a");
    assert!(file_c.exists());
    assert_eq!(fs::read_to_string(&file_c).unwrap(), "final-c");
}

