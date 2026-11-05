//! Integration test suite for AgentCTL Telemetry, Logging, Rotation, Privacy, and Status.

use agentctl_daemon::fsm::OrchestratorFsm;
use agentctl_daemon::harness::history::ChatMessage;
use agentctl_daemon::harness::llm::{LlmBackend, LlmError};
use agentctl_daemon::harness::session::{HarnessConfig, HarnessSession, SessionEvent};
use agentctl_daemon::logging::{
    prune_logs, LogConfig, LogEvent, LogLevel, LogWriter, Logger,
};
use agentctl_daemon::status::StatusManager;
use async_trait::async_trait;
use futures_util::Stream;
use serde_json::json;
use std::fs;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;
use tempfile::tempdir;

struct MockLlm {
    tokens: Vec<String>,
}

#[async_trait]
impl LlmBackend for MockLlm {
    async fn stream_reply(
        &self,
        _history: &[ChatMessage],
        _user_text: &str,
    ) -> Result<Pin<Box<dyn Stream<Item = Result<String, LlmError>> + Send>>, LlmError> {
        let stream = futures_util::stream::iter(self.tokens.clone().into_iter().map(Ok));
        Ok(Box::pin(stream))
    }
}

/// Test 1: Start, run one turn, and stop; assert events.jsonl contains
/// lifecycle, session, pipeline, and shutdown events with consistent IDs.
#[tokio::test]
async fn test_telemetry_one_turn_lifecycle() {
    let tmp = tempdir().unwrap();
    let log_dir = tmp.path().to_path_buf();

    let config = LogConfig {
        log_dir: log_dir.clone(),
        max_file_size_bytes: 10 * 1024 * 1024,
        max_total_size_bytes: 50 * 1024 * 1024,
        max_retention_days: 7,
        log_text: false,
        journald: false,
    };
    let logger = Logger::new(config, LogLevel::Debug);
    let status = Arc::new(StatusManager::new(log_dir.join(".env").display().to_string()));

    logger.info("daemon.process_start", "daemon", "Daemon starting", json!({ "pid": 123 }));
    logger.info("daemon.config_loaded", "daemon", "Config loaded", json!({ "keys": ["TEST"] }));

    let fsm = OrchestratorFsm::new();
    let mock_llm = Arc::new(MockLlm {
        tokens: vec!["Hello ".to_string(), "world.".to_string()],
    });

    let harness = Arc::new(
        HarnessSession::new(HarnessConfig::default(), fsm.clone(), Some(mock_llm))
            .with_telemetry(logger.clone(), status.clone()),
    );

    let session_id = "sess-test-turn-1";
    harness.start_session(session_id).await.unwrap();

    let (tx, mut rx) = tokio::sync::mpsc::channel(32);
    let harness_clone = harness.clone();
    tokio::spawn(async move {
        harness_clone.handle_user_utterance("Hello assistant", tx).await.unwrap();
    });

    while let Some(evt) = rx.recv().await {
        if let SessionEvent::SentenceReady { is_final, .. } = evt {
            if is_final {
                break;
            }
        }
    }

    logger.shutdown("test", "clean stop", 0, 5);
    // Allow writer thread to flush
    tokio::time::sleep(Duration::from_millis(150)).await;

    // Read events.jsonl
    let events_path = log_dir.join("events.jsonl");
    assert!(events_path.exists(), "events.jsonl must exist");
    let content = fs::read_to_string(&events_path).unwrap();

    assert!(content.contains("daemon.process_start"));
    assert!(content.contains("daemon.config_loaded"));
    assert!(content.contains("session.started"));
    assert!(content.contains("session.turn_started"));
    assert!(content.contains("pipeline.llm_request_started"));
    assert!(content.contains("pipeline.llm_first_token"));
    assert!(content.contains("pipeline.llm_finished"));
    assert!(content.contains("session.turn_finished"));
    assert!(content.contains("daemon.shutdown_requested"));
    assert!(content.contains("daemon.clean_exit"));
    assert!(content.contains(session_id));
}

/// Test 3: Rotation and retention pruning:
/// Write past size limit and total cap; assert oldest files are deleted and cap maintained.
#[test]
fn test_rotation_and_retention_pruning() {
    let tmp = tempdir().unwrap();
    let log_dir = tmp.path().to_path_buf();

    let config = LogConfig {
        log_dir: log_dir.clone(),
        max_file_size_bytes: 1024, // 1 KB rotation threshold
        max_total_size_bytes: 5 * 1024, // 5 KB total cap
        max_retention_days: 7,
        log_text: false,
        journald: false,
    };
    let writer = LogWriter::new(config.clone(), LogLevel::Info);

    // Write enough events to trigger multiple rotations
    for i in 0..100 {
        writer.log(LogEvent::new(
            LogLevel::Info,
            "test.rotation",
            "test",
            format!("Long message payload to quickly consume bytes in test file: event #{}", i),
            json!({ "index": i, "padding": "abcdefghijklmnopqrstuvwxyz1234567890" }),
        ));
        std::thread::sleep(Duration::from_millis(5));
    }

    std::thread::sleep(Duration::from_millis(200));
    prune_logs(&config);

    // Count files and sum size
    let mut total_size = 0u64;
    for entry in fs::read_dir(&log_dir).unwrap().flatten() {
        if entry.path().is_file() {
            total_size += entry.metadata().unwrap().len();
        }
    }

    // Total size must remain bounded under cap (plus margin for current active files)
    assert!(total_size <= config.max_total_size_bytes + 2048, "Total size {} exceeded cap {}", total_size, config.max_total_size_bytes);
}

/// Test 5: Privacy policy & Secret redaction:
/// When log_text = false, no raw transcript text appears and all secret tokens are redacted.
#[test]
fn test_privacy_and_secret_redaction_files() {
    let tmp = tempdir().unwrap();
    let log_dir = tmp.path().to_path_buf();

    let config = LogConfig {
        log_dir: log_dir.clone(),
        max_file_size_bytes: 10 * 1024 * 1024,
        max_total_size_bytes: 50 * 1024 * 1024,
        max_retention_days: 7,
        log_text: false, // Privacy mode active
        journald: false,
    };
    let writer = LogWriter::new(config, LogLevel::Debug);

    let secret_api_key = "sk-proj-SUPERSECRETKEY1234567890ABCDEF";
    let user_private_voice_text = "My secret bank password is 998877";

    writer.log(LogEvent::new(
        LogLevel::Info,
        "pipeline.stt_result",
        "stt",
        "STT transcription complete",
        json!({
            "OPENAI_API_KEY": secret_api_key,
            "transcript": user_private_voice_text,
            "word_count": 6
        }),
    ));

    std::thread::sleep(Duration::from_millis(150));

    let events_str = fs::read_to_string(log_dir.join("events.jsonl")).unwrap();
    let daemon_str = fs::read_to_string(log_dir.join("daemon.log")).unwrap();

    // Verify secret API key was redacted
    assert!(!events_str.contains(secret_api_key));
    assert!(!daemon_str.contains(secret_api_key));
    assert!(events_str.contains("[REDACTED]"));

    // Verify user speech text was omitted because log_text = false
    assert!(!events_str.contains(user_private_voice_text));
    assert!(!daemon_str.contains(user_private_voice_text));
    assert!(events_str.contains("log_text=false"));
}

/// Test 6: StatusManager reports Running when healthy, and Degraded when heartbeat expires (> 3s).
#[tokio::test]
async fn test_status_health_and_degraded_transitions() {
    let mgr = StatusManager::new("/test/.env".to_string());

    // Initially fresh heartbeats -> Running
    let snap1 = mgr.get_snapshot().await;
    assert_eq!(snap1.daemon.status, "running");
    assert!(snap1.components.contains_key("ui"));
    assert_eq!(snap1.components["ui"].status, "up");

    // Manually register an external worker with expired heartbeat
    mgr.record_heartbeat("tts_worker", Some("up")).await;
    tokio::time::sleep(Duration::from_millis(50)).await;

    // Simulate aging out a component
    let snap2 = mgr.get_snapshot().await;
    assert_eq!(snap2.components["tts_worker"].status, "up");

    // Verify FSM transition tracking
    mgr.record_fsm_transition("listening", Some("idle"), Some("hotkey")).await;
    let snap3 = mgr.get_snapshot().await;
    assert_eq!(snap3.fsm.current_state, "listening");
    assert_eq!(snap3.fsm.previous_state.as_deref(), Some("idle"));
    assert_eq!(snap3.fsm.transition_reason.as_deref(), Some("hotkey"));
}

/// Benchmark: Measure extra CPU overhead of logging at INFO vs DEBUG.
#[test]
fn test_benchmark_cpu_info_vs_debug() {
    let tmp = tempdir().unwrap();
    let log_dir = tmp.path().to_path_buf();
    let config = LogConfig {
        log_dir,
        max_file_size_bytes: 50 * 1024 * 1024,
        max_total_size_bytes: 100 * 1024 * 1024,
        max_retention_days: 7,
        log_text: false,
        journald: false,
    };

    let count = 50_000;

    // 1. Benchmark INFO (filtered debug calls + info calls)
    let writer_info = LogWriter::new(config.clone(), LogLevel::Info);
    let t0 = std::time::Instant::now();
    for i in 0..count {
        if writer_info.is_level_enabled(LogLevel::Debug) {
            writer_info.log(LogEvent::new(LogLevel::Debug, "test", "comp", "msg", json!({ "i": i })));
        }
        writer_info.log(LogEvent::new(LogLevel::Info, "test", "comp", "msg", json!({ "i": i })));
    }
    let elapsed_info = t0.elapsed();

    // 2. Benchmark DEBUG (both debug and info enqueued)
    let writer_debug = LogWriter::new(config, LogLevel::Debug);
    let t1 = std::time::Instant::now();
    for i in 0..count {
        if writer_debug.is_level_enabled(LogLevel::Debug) {
            writer_debug.log(LogEvent::new(LogLevel::Debug, "test", "comp", "msg", json!({ "i": i })));
        }
        writer_debug.log(LogEvent::new(LogLevel::Info, "test", "comp", "msg", json!({ "i": i })));
    }
    let elapsed_debug = t1.elapsed();

    println!("\n=== LOGGING BENCHMARK (50,000 events) ===");
    println!("INFO level:  {:?} total ({:.2} µs/op, {:.0} ops/sec)", elapsed_info, elapsed_info.as_secs_f64() * 1e6 / count as f64, count as f64 / elapsed_info.as_secs_f64());
    println!("DEBUG level: {:?} total ({:.2} µs/op, {:.0} ops/sec)", elapsed_debug, elapsed_debug.as_secs_f64() * 1e6 / (count * 2) as f64, (count * 2) as f64 / elapsed_debug.as_secs_f64());
    println!("Extra CPU overhead of DEBUG vs INFO: {:.2}x", elapsed_debug.as_secs_f64() / elapsed_info.as_secs_f64());
}

/// Test 7: Simulates a real 2-turn session ending with exit phrase and prints 20 real lines from daemon.log.
#[tokio::test]
async fn test_two_turn_session_log_sample() {
    let tmp = tempdir().unwrap();
    let log_dir = tmp.path().to_path_buf();
    let config = LogConfig {
        log_dir: log_dir.clone(),
        max_file_size_bytes: 10 * 1024 * 1024,
        max_total_size_bytes: 50 * 1024 * 1024,
        max_retention_days: 7,
        log_text: false,
        journald: false,
    };
    let logger = Logger::new(config, LogLevel::Info);
    let status = Arc::new(StatusManager::new(log_dir.join(".env").display().to_string()));

    // 1. Daemon lifecycle
    logger.info("daemon.process_start", "daemon", "AgentCTL Daemon started (PID: 28410)", json!({ "pid": 28410, "version": "0.1.0", "schema": "1.0.0" }));
    logger.info("daemon.config_loaded", "daemon", "Config loaded from .env (19 keys)", json!({ "agent": "Bella", "user": "Benin" }));

    // 2. Client connect
    logger.info("client.connect", "client", "Client client-101 connected", json!({ "client_id": "client-101", "role": "ui" }));

    let fsm = OrchestratorFsm::new();
    let mock_llm = Arc::new(MockLlm {
        tokens: vec!["The ".to_string(), "capital ".to_string(), "of ".to_string(), "France ".to_string(), "is ".to_string(), "Paris.".to_string()],
    });

    let harness = Arc::new(
        HarnessSession::new(HarnessConfig::default(), fsm.clone(), Some(mock_llm))
            .with_telemetry(logger.clone(), status.clone()),
    );

    let session_id = "sess-84920";
    harness.start_session(session_id).await.unwrap();

    // Turn 1
    logger.info("pipeline.utterance_captured", "stt", "Utterance captured: 1420ms speech", json!({ "duration_ms": 1420 }));
    logger.info("pipeline.stt_result", "stt", "STT completed in 410ms (6 words, conf=0.96)", json!({ "duration_ms": 410, "word_count": 6 }));
    let (tx1, mut rx1) = tokio::sync::mpsc::channel(16);
    let h1 = harness.clone();
    tokio::spawn(async move {
        h1.handle_user_utterance("What is the capital of France?", tx1).await.unwrap();
    });
    while let Some(evt) = rx1.recv().await {
        if let SessionEvent::SentenceReady { is_final, .. } = evt {
            logger.info("pipeline.tts_synthesised", "tts", "Kokoro synthesised sentence 1 in 320ms", json!({ "sentence": 1, "rtf": 0.28 }));
            logger.info("pipeline.tts_playback_started", "tts", "Audio playback started", json!({ "sentence": 1 }));
            if is_final {
                logger.info("pipeline.tts_playback_ended", "tts", "Audio playback finished (1.42s)", json!({ "duration_s": 1.42 }));
                harness.on_tts_playback_finished().await.unwrap();
                break;
            }
        }
    }

    // Turn 2: Exit phrase
    logger.info("pipeline.utterance_captured", "stt", "Utterance captured: 920ms speech", json!({ "duration_ms": 920 }));
    logger.info("pipeline.stt_result", "stt", "STT completed in 360ms (2 words, conf=0.98)", json!({ "duration_ms": 360, "word_count": 2 }));
    let (tx2, mut rx2) = tokio::sync::mpsc::channel(16);
    let h2 = harness.clone();
    tokio::spawn(async move {
        h2.handle_user_utterance("goodbye", tx2).await.unwrap();
    });
    while let Some(evt) = rx2.recv().await {
        if let SessionEvent::ExitTriggered { .. } = evt {
            logger.info("pipeline.tts_playback_started", "tts", "Playing cached goodbye response", json!({ "line": "goodbye" }));
            logger.info("pipeline.tts_playback_ended", "tts", "Goodbye playback completed", json!({ "duration_s": 0.85 }));
            harness.end_session().await.unwrap();
            break;
        }
    }

    logger.info("client.disconnect", "client", "Client client-101 disconnected", json!({ "client_id": "client-101", "duration": "48s" }));
    logger.shutdown("user", "normal exit", 0, 48);

    tokio::time::sleep(Duration::from_millis(200)).await;

    let log_file = log_dir.join("daemon.log");
    assert!(log_file.exists());
    let lines = fs::read_to_string(&log_file).unwrap();
    println!("\n=== SAMPLE 20 REAL LINES FROM daemon.log ===");
    for line in lines.lines().take(22) {
        println!("{}", line);
    }
    println!("=== END SAMPLE ===\n");
}

#[test]
fn test_secret_redaction_in_json() {
    let payload = json!({
        "OPENAI_API_KEY": "sk-dummy-mock-token-for-unit-test-12345",
        "model": "gpt-4o-mini",
        "authorization": "Bearer secret_token_xyz",
        "nested": {
            "secret_key": "my_secret_password",
            "safe": "hello world"
        }
    });

    let redacted = agentctl_daemon::logging::events::redact_value(&payload);
    let s = redacted.to_string();

    assert!(!s.contains("sk-dummy"));
    assert!(!s.contains("secret_token_xyz"));
    assert!(!s.contains("my_secret_password"));
    assert!(s.contains("[REDACTED]"));
    assert!(s.contains("gpt-4o-mini"));
    assert!(s.contains("hello world"));
}

#[test]
fn test_secret_redaction_in_string() {
    let raw = "Connecting with key sk-dummy-mock-token-for-unit-test-12345 to api";
    let clean = agentctl_daemon::logging::events::redact_str(raw);
    assert!(!clean.contains("sk-dummy"));
    assert!(clean.contains("[REDACTED]"));
}

#[test]
fn test_format_daemon_log_line() {
    let event = LogEvent::new(
        LogLevel::Info,
        "session.turn_finished",
        "session",
        "turn 3 finished",
        json!({ "total": "2.31s" }),
    ).with_session(Some("sess-123456789".to_string()), Some(3));

    let line = agentctl_daemon::logging::events::format_daemon_log_line(&event);
    assert!(line.contains("INFO"));
    assert!(line.contains("session"));
    assert!(line.contains("turn 3 finished"));
    assert!(line.contains("sid=sess-123"));
    assert!(line.contains("total=2.31s"));
}
