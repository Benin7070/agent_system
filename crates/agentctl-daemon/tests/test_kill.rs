use agentctl_daemon::core::kill::kill_all_managed;

#[tokio::test]
async fn test_kill_switch_completes_under_two_seconds_without_api() {
    let report = kill_all_managed(None).await.expect("kill operation must succeed or return empty");
    assert!(
        report.elapsed_ms < 2000,
        "Kill operation took {}ms, which exceeds the 2000ms SLA",
        report.elapsed_ms
    );
}

#[tokio::test]
async fn test_kill_switch_with_task_filter() {
    let report = kill_all_managed(Some("task-999")).await.expect("kill with filter must run");
    assert!(report.elapsed_ms < 2000);
}

