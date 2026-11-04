pub mod handlers;

use self::handlers::{
    activate_handler, approve_handler, cancel_handler, deny_handler, intent_handler, kill_handler,
    status_handler, AppState,
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
        .route("/approve", post(approve_handler))
        .route("/deny", post(deny_handler))
        .route("/kill", post(kill_handler))
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

    // Set socket permissions to 0600 (owner only)
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(socket_path, std::fs::Permissions::from_mode(0o600));
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
