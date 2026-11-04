use crate::core::audit::AuditLog;
use crate::core::executor::{execute, ExecutorError};
use crate::core::schemas::ProposedAction;
use serde_json::json;
use sqlx::{sqlite::SqlitePool, Row};
use thiserror::Error;

#[derive(Error, Debug)]
pub enum SchedulerError {
    #[error("Database error: {0}")]
    Db(#[from] sqlx::Error),
    #[error("Execution error: {0}")]
    Exec(#[from] ExecutorError),
    #[error("Serialization error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("Task cancelled")]
    Cancelled,
}

#[derive(Clone)]
pub struct Scheduler {
    pool: SqlitePool,
    audit: AuditLog,
}

impl Scheduler {
    pub fn new(pool: SqlitePool, audit: AuditLog) -> Self {
        Self { pool, audit }
    }

    pub async fn init(&self) -> Result<(), sqlx::Error> {
        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS task_checkpoints (
                task_id     TEXT    NOT NULL,
                step_index  INTEGER NOT NULL,
                action_json TEXT    NOT NULL,
                status      TEXT    NOT NULL, -- 'PENDING', 'COMPLETED', 'FAILED'
                PRIMARY KEY (task_id, step_index)
            );
            "#,
        )
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Registers a multi-step plan in checkpoints before execution starts.
    pub async fn register_plan(
        &self,
        task_id: &str,
        steps: &[ProposedAction],
    ) -> Result<(), SchedulerError> {
        for (idx, action) in steps.iter().enumerate() {
            let json_str = serde_json::to_string(action)?;
            sqlx::query(
                r#"
                INSERT INTO task_checkpoints (task_id, step_index, action_json, status)
                VALUES (?, ?, ?, 'PENDING')
                ON CONFLICT(task_id, step_index) DO NOTHING
                "#,
            )
            .bind(task_id)
            .bind(idx as i64)
            .bind(json_str)
            .execute(&self.pool)
            .await?;
        }
        Ok(())
    }

    /// Runs all pending steps for a task, with intent-before-execute logging and crash recovery.
    pub async fn run_or_resume(&self, task_id: &str) -> Result<usize, SchedulerError> {
        let rows = sqlx::query(
            "SELECT step_index, action_json, status FROM task_checkpoints WHERE task_id = ? ORDER BY step_index ASC",
        )
        .bind(task_id)
        .fetch_all(&self.pool)
        .await?;

        let mut completed_count = 0;

        for row in rows {
            let step_idx: i64 = row.try_get("step_index")?;
            let status: String = row.try_get("status")?;
            let action_json: String = row.try_get("action_json")?;

            if status == "COMPLETED" {
                completed_count += 1;
                continue; // Skip already completed steps (crash recovery)
            }

            let action: ProposedAction = serde_json::from_str(&action_json)?;

            // 1. Intent logged BEFORE execution
            let _ = self
                .audit
                .append(
                    "STEP_INTENT",
                    &json!({
                        "task_id": task_id,
                        "step_index": step_idx,
                        "action_type": action.action_type_name()
                    }),
                )
                .await;

            // 2. Execute action
            let _out = execute(&action).await?;

            // 3. Mark checkpoint completed in DB
            sqlx::query(
                "UPDATE task_checkpoints SET status = 'COMPLETED' WHERE task_id = ? AND step_index = ?",
            )
            .bind(task_id)
            .bind(step_idx)
            .execute(&self.pool)
            .await?;

            // 4. Log completion to audit chain
            let _ = self
                .audit
                .append(
                    "STEP_COMPLETED",
                    &json!({
                        "task_id": task_id,
                        "step_index": step_idx
                    }),
                )
                .await;

            completed_count += 1;
        }

        Ok(completed_count)
    }
}
