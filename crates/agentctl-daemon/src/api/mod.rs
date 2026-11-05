pub mod handlers;

use self::handlers::{
    activate_handler, approve_handler, cancel_handler, deny_handler, intent_handler, kill_handler,
    level_handler, loglevel_handler, status_handler, status_snapshot_handler, ws_handler, AppState,
};
use axum::{
    routing::{get, post},
    Router,
};
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto::Builder;
use hyper_util::service::TowerToHyperService;
use std::path::Path;
use std::sync::Arc;
use tokio::net::UnixListener;

/// Builds the Axum router for the daemon.
pub fn create_router(state: Arc<AppState>) -> Router {
    Router::new()
        .route("/activate", post(activate_handler))
        .route("/cancel", post(cancel_handler))
        .route("/intent", post(intent_handler))
        .route("/status", get(status_handler))
        .route("/status/snapshot", get(status_snapshot_handler))
        .route("/loglevel", post(loglevel_handler))
        .route("/approve", post(approve_handler))
        .route("/deny", post(deny_handler))
        .route("/kill", post(kill_handler))
        .route("/level", post(level_handler))
        .route("/ws", get(ws_handler))
        .with_state(state)
}

/// Runs the Axum server bound strictly to the given Unix Domain Socket path.
pub async fn run_uds_server(state: Arc<AppState>, socket_path: &Path) -> std::io::Result<()> {
    if let Some(parent) = socket_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if socket_path.exists() {
        let _ = std::fs::remove_file(socket_path);
    }

    let listener = UnixListener::bind(socket_path)?;

    // Set socket permissions to 0666 and chown to SUDO_UID if running under sudo
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(socket_path, std::fs::Permissions::from_mode(0o666));

        let uid = unsafe { libc::getuid() };
        if uid == 0 {
            if let Ok(sudo_uid) = std::env::var("SUDO_UID") {
                if let Ok(target_uid) = sudo_uid.parse::<u32>() {
                    let target_gid = std::env::var("SUDO_GID")
                        .ok()
                        .and_then(|g| g.parse::<u32>().ok())
                        .unwrap_or(target_uid);

                    use std::os::unix::ffi::OsStrExt;
                    if let Some(parent) = socket_path.parent() {
                        if let Ok(c_parent) = std::ffi::CString::new(parent.as_os_str().as_bytes()) {
                            unsafe {
                                libc::chown(c_parent.as_ptr(), target_uid, target_gid);
                            }
                        }
                    }
                    if let Ok(c_path) = std::ffi::CString::new(socket_path.as_os_str().as_bytes()) {
                        unsafe {
                            libc::chown(c_path.as_ptr(), target_uid, target_gid);
                        }
                    }
                }
            }
        }
    }

    let app = create_router(state);

    loop {
        let (stream, _remote_addr) = listener.accept().await?;
        let tower_service = app.clone();

        tokio::spawn(async move {
            let io = TokioIo::new(stream);
            let hyper_service = TowerToHyperService::new(tower_service);

            if let Err(err) = Builder::new(TokioExecutor::new())
                .serve_connection_with_upgrades(io, hyper_service)
                .await
            {
                tracing::debug!("Error serving UDS connection: {}", err);
            }
        });
    }
}
