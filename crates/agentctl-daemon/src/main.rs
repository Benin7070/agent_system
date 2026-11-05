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

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt::init();

    let args: Vec<String> = env::args().collect();
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

    let state = Arc::new(AppState::new(fsm, gate, audit));

    if args.len() > 1 && (args[1] == "hotkey" || args[1] == "hotkey-listen") {
        println!("Starting dedicated KEY_PROG2 (149) hotkey listener...");
        let dev = agentctl_daemon::hotkey::find_hotkey_device(agentctl_daemon::hotkey::KEY_PROG2);
        println!("Discovered hotkey device: {:?}", dev);
        agentctl_daemon::hotkey::run_hotkey_listener_loop(
            state.clone(),
            Arc::new(tokio::sync::Mutex::new(None)),
        )
        .await;
        return Ok(());
    }

    if args.len() > 1 && args[1] == "hotkey-trigger" {
        println!("Manually triggering KEY_PROG2 (149) toggle...");
        let action = agentctl_daemon::hotkey::handle_hotkey_toggle(
            &state,
            &Arc::new(tokio::sync::Mutex::new(None)),
        )
        .await?;
        println!("Hotkey toggle result: {:?}", action);
        return Ok(());
    }

    // Start background hotkey listener for KEY_PROG2 (149)
    agentctl_daemon::hotkey::spawn_hotkey_listener(state.clone());

    tracing::info!("AgentCTL Daemon listening on UDS: {}", socket_path.display());
    println!("AgentCTL Daemon running on {}", socket_path.display());

    run_uds_server(state, &socket_path).await?;
    Ok(())
}
