use chrono::Utc;
use serde_json::Value;
use sha2::{Digest, Sha256};
use sqlx::{sqlite::SqlitePool, Row};
use thiserror::Error;

pub const GENESIS_HASH: &str = "0000000000000000000000000000000000000000000000000000000000000000";

#[derive(Error, Debug)]
pub enum AuditError {
    #[error("Database error: {0}")]
    Database(#[from] sqlx::Error),
    #[error("Tamper detected at row {id}: {reason}")]
    TamperDetected { id: i64, reason: String },
    #[error("Serialization error: {0}")]
    Serialization(#[from] serde_json::Error),
}

/// Recursively scrub secret fields from JSON values before writing to the audit log.
pub fn scrub_secrets(val: &Value) -> Value {
    match val {
        Value::Object(map) => {
            let mut scrubbed_map = serde_json::Map::new();
            for (k, v) in map {
                let lower = k.to_ascii_lowercase();
                if lower.contains("secret")
                    || lower.contains("password")
                    || lower.contains("token")
                    || lower.contains("api_key")
                    || lower.contains("apikey")
                    || lower.contains("authorization")
                    || lower.contains("private_key")
                    || lower.contains("credentials")
                {
                    scrubbed_map.insert(k.clone(), Value::String("[REDACTED]".to_string()));
                } else {
                    scrubbed_map.insert(k.clone(), scrub_secrets(v));
                }
            }
            Value::Object(scrubbed_map)
        }
        Value::Array(arr) => Value::Array(arr.iter().map(scrub_secrets).collect()),
        Value::String(s) => {
            if s.starts_with("Bearer ") || s.starts_with("sk-") {
                Value::String("[REDACTED]".to_string())
            } else {
                Value::String(s.clone())
            }
        }
        other => other.clone(),
    }
}

/// Computes SHA-256 of canonical payload + prev_hash.
pub fn compute_row_hash(payload_str: &str, prev_hash: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(payload_str.as_bytes());
    hasher.update(prev_hash.as_bytes());
    hex::encode(hasher.finalize())
}

#[derive(Clone)]
pub struct AuditLog {
    pool: SqlitePool,
}

impl AuditLog {
    pub fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    /// Initializes SQLite table for hash-chained audit logging.
    pub async fn init(&self) -> Result<(), AuditError> {
        sqlx::query(
            r#"
            CREATE TABLE IF NOT EXISTS audit_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp   TEXT    NOT NULL,
                event_type  TEXT    NOT NULL,
                payload     TEXT    NOT NULL,
                prev_hash   TEXT    NOT NULL,
                row_hash    TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_log_id ON audit_log(id);
            "#,
        )
        .execute(&self.pool)
        .await?;
        Ok(())
    }

    /// Retrieve the hash of the latest row, or GENESIS_HASH if empty.
    pub async fn get_last_hash(&self) -> Result<String, AuditError> {
        let row = sqlx::query("SELECT row_hash FROM audit_log ORDER BY id DESC LIMIT 1")
            .fetch_optional(&self.pool)
            .await?;

        match row {
            Some(r) => Ok(r.try_get("row_hash")?),
            None => Ok(GENESIS_HASH.to_string()),
        }
    }

    /// Append a new audited event. Scrubbing happens strictly BEFORE payload stringification.
    pub async fn append(&self, event_type: &str, payload: &Value) -> Result<String, AuditError> {
        let scrubbed = scrub_secrets(payload);
        let payload_str = serde_json::to_string(&scrubbed)?;
        let prev_hash = self.get_last_hash().await?;
        let row_hash = compute_row_hash(&payload_str, &prev_hash);
        let timestamp = Utc::now().to_rfc3339();

        sqlx::query(
            r#"
            INSERT INTO audit_log (timestamp, event_type, payload, prev_hash, row_hash)
            VALUES (?, ?, ?, ?, ?)
            "#,
        )
        .bind(timestamp)
        .bind(event_type)
        .bind(&payload_str)
        .bind(&prev_hash)
        .bind(&row_hash)
        .execute(&self.pool)
        .await?;

        Ok(row_hash)
    }

    /// Verify the entire hash chain from genesis to head.
    /// Fails immediately if any row payload or hash was tampered with.
    pub async fn verify_chain(&self) -> Result<(), AuditError> {
        let rows = sqlx::query("SELECT id, payload, prev_hash, row_hash FROM audit_log ORDER BY id ASC")
            .fetch_all(&self.pool)
            .await?;

        let mut expected_prev = GENESIS_HASH.to_string();

        for row in rows {
            let id: i64 = row.try_get("id")?;
            let payload: String = row.try_get("payload")?;
            let prev_hash: String = row.try_get("prev_hash")?;
            let row_hash: String = row.try_get("row_hash")?;

            if prev_hash != expected_prev {
                return Err(AuditError::TamperDetected {
                    id,
                    reason: format!(
                        "Broken chain: expected prev_hash {} but found {}",
                        expected_prev, prev_hash
                    ),
                });
            }

            let computed = compute_row_hash(&payload, &prev_hash);
            if computed != row_hash {
                return Err(AuditError::TamperDetected {
                    id,
                    reason: format!(
                        "Invalid row hash: computed {} but recorded {}",
                        computed, row_hash
                    ),
                });
            }

            expected_prev = row_hash;
        }

        Ok(())
    }
}
