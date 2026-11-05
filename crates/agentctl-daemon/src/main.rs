use agentctl_daemon::api::handlers::AppState;
use agentctl_daemon::api::run_uds_server;
use agentctl_daemon::core::audit::AuditLog;
use agentctl_daemon::core::gate::ApprovalGate;
use agentctl_daemon::core::kill::kill_all_managed;
use agentctl_daemon::core::scheduler::Scheduler;
use agentctl_daemon::fsm::OrchestratorFsm;
use sqlx::sqlite::SqlitePoolOptions;
use std::env;
use std::path::PathBuf;
use std::sync::Arc;

fn get_socket_path() -> PathBuf {
    // When running as root (e.g. sudo), prefer SUDO_UID's runtime dir so the
    // UI subprocess (which runs as the real user) can connect to the same socket.
    let uid = unsafe { libc::getuid() };
    if uid == 0 {
        if let Ok(sudo_uid) = env::var("SUDO_UID") {
            if let Ok(n) = sudo_uid.parse::<u32>() {
                let runtime = PathBuf::from(format!("/run/user/{}", n));
                if runtime.exists() {
                    let dir = runtime.join("agentctl");
                    let _ = std::fs::create_dir_all(&dir);
                    return dir.join("daemon.sock");
                }
            }
        }
    }
    if let Ok(runtime_dir) = env::var("XDG_RUNTIME_DIR") {
        PathBuf::from(runtime_dir).join("agentctl").join("daemon.sock")
    } else {
        PathBuf::from(format!("/tmp/agentctl-{}/daemon.sock", uid))
    }
}

fn get_db_path() -> PathBuf {
    if let Ok(data_home) = env::var("XDG_DATA_HOME") {
        PathBuf::from(data_home).join("agentctl").join("agentctl.db")
    } else if let Ok(home) = env::var("HOME") {
        PathBuf::from(home)
            .join(".local")
            .join("share")
            .join("agentctl")
            .join("agentctl.db")
    } else {
        PathBuf::from("/tmp/agentctl.db")
    }
}

fn load_dotenv() {
    let mut candidate_paths = Vec::new();

    // 1. Current working directory .env
    candidate_paths.push(PathBuf::from(".env"));

    // 2. Relative to executable path
    if let Ok(exe) = env::current_exe() {
        if let Some(parent) = exe.parent() {
            candidate_paths.push(parent.join(".env"));
            if let Some(grandparent) = parent.parent() {
                candidate_paths.push(grandparent.join(".env"));
                if let Some(root) = grandparent.parent() {
                    candidate_paths.push(root.join(".env"));
                }
            }
        }
    }

    // 3. User config ~/.config/agentctl/.env or /home/$SUDO_USER/.config/agentctl/.env
    if let Ok(sudo_user) = env::var("SUDO_USER") {
        candidate_paths.push(PathBuf::from(format!("/home/{}/.config/agentctl/.env", sudo_user)));
        candidate_paths.push(PathBuf::from(format!("/home/{}/Programming/agents/.env", sudo_user)));
    }
    if let Ok(home) = env::var("HOME") {
        candidate_paths.push(PathBuf::from(home).join(".config").join("agentctl").join(".env"));
    }

    for path in candidate_paths {
        if path.is_file() {
            if let Ok(content) = std::fs::read_to_string(&path) {
                let mut loaded = 0;
                for line in content.lines() {
                    let trimmed = line.trim();
                    if trimmed.is_empty() || trimmed.starts_with('#') {
                        continue;
                    }
                    if let Some((key, val)) = trimmed.split_once('=') {
                        let key = key.trim();
                        let mut val = val.trim();
                        if (val.starts_with('"') && val.ends_with('"'))
                            || (val.starts_with('\'') && val.ends_with('\''))
                        {
                            if val.len() >= 2 {
                                val = &val[1..val.len() - 1];
                            }
                        }
                        if !key.is_empty()
                            && !val.is_empty()
                            && (env::var(key).is_err()
                                || env::var(key).map(|v| v.is_empty()).unwrap_or(false))
                        {
                            env::set_var(key, val);
                            loaded += 1;
                        }
                    }
                }
                println!("[Config] Loaded {} environment variables from {}", loaded, path.display());
                break;
            }
        }
    }
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    load_dotenv();
    tracing_subscriber::fmt::init();

    let args: Vec<String> = env::args().collect();

    // 1. CLI subcommand: status
    if args.len() > 1 && args[1] == "status" {
        let is_json = args.iter().any(|a| a == "--json");
        let socket_path = get_socket_path();
        if !socket_path.exists() {
            if is_json {
                println!("{}", serde_json::json!({ "status": "stopped", "error": "Daemon not running", "code": 3 }));
            } else {
                println!("================================================================================");
                println!("\x1b[31m\x1b[1mAGENTCTL STATUS: UNKNOWN (daemon not reachable)\x1b[0m");
                println!("================================================================================");
                println!("Daemon socket {} is not answering.", socket_path.display());
            }
            std::process::exit(3);
        }

        // Query status endpoint over UDS
        match tokio::net::UnixStream::connect(&socket_path).await {
            Ok(mut stream) => {
                use tokio::io::{AsyncReadExt, AsyncWriteExt};
                let req = b"GET /status/snapshot HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n";
                if stream.write_all(req).await.is_err() {
                    std::process::exit(3);
                }
                let mut buf = Vec::new();
                let _ = stream.read_to_end(&mut buf).await;
                if let Ok(body_str) = String::from_utf8(buf) {
                    if let Some(pos) = body_str.find("\r\n\r\n") {
                        let json_part = &body_str[pos + 4..];
                        if let Ok(status) = serde_json::from_str::<agentctl_daemon::status::DaemonStatus>(json_part) {
                            let code = if status.daemon.status == "running" {
                                0
                            } else if status.daemon.status == "degraded" {
                                1
                            } else {
                                3
                            };

                            if is_json {
                                println!("{}", serde_json::to_string_pretty(&status).unwrap_or_default());
                            } else {
                                println!("================================================================================");
                                let col = if status.daemon.status == "running" { "\x1b[32m" } else { "\x1b[33m" };
                                println!(
                                    "\x1b[1mAGENTCTL STATUS:\x1b[0m {}{}\x1b[0m (PID: {} | Uptime: {}s | v{} ({}) | Schema: {})",
                                    col, status.daemon.status.to_uppercase(), status.daemon.pid, status.daemon.uptime_secs, status.daemon.version, status.daemon.git_hash, status.daemon.telemetry_schema_version
                                );
                                println!("Config: {}", status.daemon.config_path);
                                println!("--------------------------------------------------------------------------------");
                                println!("FSM:        {} (for {:.1}s, prev: {}, reason: {})", status.fsm.current_state.to_uppercase(), status.fsm.time_in_state_secs, status.fsm.previous_state.as_deref().unwrap_or("none"), status.fsm.transition_reason.as_deref().unwrap_or("none"));
                                println!("Session:    {} | Turn: {} | Exit Pending: {}", status.session.session_id.as_deref().unwrap_or("none"), status.session.turn_number, if status.session.exit_phrase_pending { "YES" } else { "NO" });
                                println!("Orb / UI:   {} | Edge Glow: {} | Level: {:.3}", status.orb.orb_state.to_uppercase(), if status.orb.edge_glow_enabled { "ON" } else { "OFF" }, status.orb.last_level);
                                println!("Mic / TTS:  Mic: {} ({}) | TTS: {} (sentence: {})", status.mic.state.to_uppercase(), status.mic.reason, status.tts.state.to_uppercase(), status.tts.current_sentence);
                                println!("LLM:        {} (model: {} | last: {} in {}ms)", status.llm.status.to_uppercase(), status.llm.model_name, status.llm.last_request_result.as_deref().unwrap_or("none"), status.llm.last_latency_ms.unwrap_or(0));
                                let comps: Vec<String> = status.components.iter().map(|(n, c)| format!("{}: {}", n, c.status.to_uppercase())).collect();
                                println!("Components: {}", comps.join(" | "));
                                println!("Models:     {} | RAM: {:.1} MB", status.models.models_loaded.join(", "), status.models.ram_mb);
                                println!("================================================================================");
                            }
                            std::process::exit(code);
                        }
                    }
                }
                std::process::exit(4);
            }
            Err(_) => {
                if is_json {
                    println!("{}", serde_json::json!({ "status": "stopped", "error": "Daemon not reachable", "code": 3 }));
                } else {
                    println!("================================================================================");
                    println!("\x1b[31m\x1b[1mAGENTCTL STATUS: UNKNOWN (daemon not reachable)\x1b[0m");
                    println!("================================================================================");
                    println!("Daemon socket {} is not answering.", socket_path.display());
                }
                std::process::exit(3);
            }
        }
    }

    if let Ok(key) = env::var("OPENAI_API_KEY") {
        let key_trimmed = key.trim();
        if !key_trimmed.is_empty() {
            println!("[Config] OpenAI API key detected ({} chars).", key_trimmed.len());
        } else {
            println!("[Config] Warning: OPENAI_API_KEY is empty in .env.");
        }
    } else {
        println!("[Config] Warning: No OPENAI_API_KEY found in environment or .env.");
    }

    if args.len() > 1 && args[1] == "kill" {
        let task_id = args.get(2).map(|s| s.as_str());
        println!("Invoking direct kill switch for task: {:?}", task_id);
        let report = kill_all_managed(task_id).await?;
        println!(
            "Kill switch completed in {}ms. Stopped {} containers.",
            report.elapsed_ms, report.stopped_count
        );
        return Ok(());
    }

    let socket_path = get_socket_path();
    let db_path = get_db_path();

    if let Some(parent) = db_path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    let db_url = format!("sqlite://{}?mode=rwc", db_path.display());
    tracing::info!("Connecting to database: {}", db_url);

    let pool = SqlitePoolOptions::new()
        .max_connections(5)
        .connect(&db_url)
        .await?;

    let audit = AuditLog::new(pool.clone());
    audit.init().await?;

    let scheduler = Scheduler::new(pool.clone(), audit.clone());
    scheduler.init().await?;

    let gate = ApprovalGate::new();
    let fsm = OrchestratorFsm::new();

    // 2. Initialize Telemetry Logger & Status Manager
    let log_text = env::var("AGENTCTL_LOG_TEXT")
        .map(|v| v == "1" || v.eq_ignore_ascii_case("true"))
        .unwrap_or(false);
    let log_level = env::var("AGENTCTL_LOG_LEVEL")
        .ok()
        .and_then(|s| agentctl_daemon::logging::LogLevel::from_str_loose(&s))
        .unwrap_or(agentctl_daemon::logging::LogLevel::Info);

    let log_config = agentctl_daemon::logging::LogConfig {
        log_text,
        ..Default::default()
    };
    let logger = agentctl_daemon::logging::Logger::new(log_config, log_level);
    let config_path_str = env::var("AGENTCTL_CONFIG_PATH")
        .unwrap_or_else(|_| env::current_dir().map(|p| p.join(".env").display().to_string()).unwrap_or_default());
    let status = Arc::new(agentctl_daemon::status::StatusManager::new(config_path_str.clone()));

    // Spawn internal daemon heartbeat task
    let daemon_hb = status.clone();
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(std::time::Duration::from_millis(1000));
        loop {
            interval.tick().await;
            daemon_hb.record_heartbeat("daemon", Some("up")).await;
        }
    });

    // Log process start and config
    logger.info(
        "daemon.process_start",
        "daemon",
        format!("AgentCTL Daemon started (PID: {})", std::process::id()),
        serde_json::json!({
            "pid": std::process::id(),
            "version": env!("CARGO_PKG_VERSION"),
            "schema_version": agentctl_daemon::status::SCHEMA_VERSION,
            "config_path": &config_path_str
        }),
    );
    logger.info(
        "daemon.config_loaded",
        "daemon",
        "Environment configuration loaded",
        serde_json::json!({
            "log_text": log_text,
            "log_level": log_level.as_str()
        }),
    );

    if log_text {
        logger.warn(
            "daemon.privacy_alert",
            "daemon",
            "WARNING: log_text is ENABLED. Speech transcripts and LLM responses are being written to logs.",
            serde_json::json!({}),
        );
        println!("================================================================================");
        println!("  WARNING: log_text is ENABLED. Speech and LLM text will be logged!             ");
        println!("================================================================================");
    }

    let state = Arc::new(AppState::with_logger_and_status(fsm, gate, audit, logger.clone(), status.clone()));

    // Shared slot holding the resident warm overlay UI child process
    let overlay_child: Arc<tokio::sync::Mutex<Option<tokio::process::Child>>> =
        Arc::new(tokio::sync::Mutex::new(None));

    // 3. Graceful termination handler (SIGINT / SIGTERM)
    let sig_logger = logger.clone();
    let start_instant = std::time::Instant::now();
    let oc = overlay_child.clone();
    tokio::spawn(async move {
        use tokio::signal::unix::{signal, SignalKind};
        if let (Ok(mut sigterm), Ok(mut sigint)) = (signal(SignalKind::terminate()), signal(SignalKind::interrupt())) {
            tokio::select! {
                _ = sigterm.recv() => {
                    sig_logger.info("daemon.signal_received", "daemon", "Received SIGTERM", serde_json::json!({ "signal": "SIGTERM" }));
                    sig_logger.shutdown("signal", "SIGTERM", 0, start_instant.elapsed().as_secs());
                    agentctl_daemon::harness::local_manager::LocalLlmManager::stop();
                    if let Some(mut child) = oc.lock().await.take() {
                        let _ = child.kill().await;
                    }
                    std::process::exit(0);
                }
                _ = sigint.recv() => {
                    sig_logger.info("daemon.signal_received", "daemon", "Received SIGINT", serde_json::json!({ "signal": "SIGINT" }));
                    sig_logger.shutdown("signal", "SIGINT", 0, start_instant.elapsed().as_secs());
                    agentctl_daemon::harness::local_manager::LocalLlmManager::stop();
                    if let Some(mut child) = oc.lock().await.take() {
                        let _ = child.kill().await;
                    }
                    std::process::exit(0);
                }
            }
        }
    });

    // 4. Auto-manage local LLM inference server (llama-server) if provider is local or auto
    let provider = agentctl_daemon::harness::llm::LlmProvider::default();
    if provider != agentctl_daemon::harness::llm::LlmProvider::OpenAi {
        tokio::spawn(async {
            if let Err(e) = agentctl_daemon::harness::local_manager::LocalLlmManager::ensure_started(8080).await {
                tracing::debug!("Local LLM auto-start check: {}", e);
            }
        });
    }

    if args.len() > 1 && (args[1] == "hotkey" || args[1] == "hotkey-listen") {
        println!("Starting dedicated KEY_PROG2 (149) hotkey listener...");
        let dev = agentctl_daemon::hotkey::find_hotkey_device(agentctl_daemon::hotkey::KEY_PROG2);
        println!("Discovered hotkey device: {:?}", dev);
        agentctl_daemon::hotkey::run_hotkey_listener_loop(
            state.clone(),
            overlay_child.clone(),
        )
        .await;
        return Ok(());
    }

    if args.len() > 1 && args[1] == "hotkey-trigger" {
        println!("Manually triggering KEY_PROG2 (149) toggle...");
        let action = agentctl_daemon::hotkey::handle_hotkey_toggle(
            &state,
            &overlay_child,
        )
        .await?;
        println!("Hotkey toggle result: {:?}", action);
        return Ok(());
    }

    // Pre-warm the overlay UI in the background so the first hotkey press is instantaneous
    let oc_prewarm = overlay_child.clone();
    tokio::spawn(async move {
        tokio::time::sleep(std::time::Duration::from_millis(600)).await;
        agentctl_daemon::hotkey::spawn_overlay(&oc_prewarm, true).await;
    });

    // Start background hotkey listener for KEY_PROG2 (149)
    agentctl_daemon::hotkey::spawn_hotkey_listener(state.clone(), overlay_child.clone());

    tracing::info!("AgentCTL Daemon listening on UDS: {}", socket_path.display());
    println!("AgentCTL Daemon running on {}", socket_path.display());

    run_uds_server(state, &socket_path).await?;
    Ok(())
}
