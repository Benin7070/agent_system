//! System status introspection, component heartbeats, and health monitoring.

use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::RwLock;

pub const DEFAULT_HEARTBEAT_TIMEOUT: Duration = Duration::from_millis(3000);
pub const SCHEMA_VERSION: &str = "1.0.0";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DaemonInfo {
    pub status: String, // starting, running, degraded, stopping, stopped, down
    pub pid: u32,
    pub uptime_secs: u64,
    pub version: String,
    pub git_hash: String,
    pub config_path: String,
    pub telemetry_schema_version: String,
    pub last_error: Option<LastErrorInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LastErrorInfo {
    pub time: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClientConnectionInfo {
    pub client_id: String,
    pub role: String, // ui, monitor, worker
    pub connected_at: String,
    pub connected_duration_secs: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionStatusInfo {
    pub session_id: Option<String>,
    pub turn_number: u32,
    pub time_since_start_secs: Option<f64>,
    pub exit_phrase_pending: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FsmStatusInfo {
    pub current_state: String,
    pub time_in_state_secs: f64,
    pub previous_state: Option<String>,
    pub transition_reason: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrbStatusInfo {
    pub orb_state: String,
    pub edge_glow_enabled: bool,
    pub last_level: f32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MicStatusInfo {
    pub state: String, // armed, paused, flushing
    pub reason: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TtsStatusInfo {
    pub state: String, // idle, synthesising, playing
    pub current_sentence: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LlmStatusInfo {
    pub status: String, // ready, no_api_key, error, backing_off
    pub model_name: String,
    pub last_request_result: Option<String>,
    pub last_latency_ms: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ComponentHealth {
    pub name: String,
    pub status: String, // up, down, degraded
    pub last_heartbeat_age_secs: f64,
    pub restarts_count: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelsStatusInfo {
    pub models_loaded: Vec<String>,
    pub ram_mb: f64,
    pub vram_mb: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DaemonStatus {
    pub daemon: DaemonInfo,
    pub connections: Vec<ClientConnectionInfo>,
    pub session: SessionStatusInfo,
    pub fsm: FsmStatusInfo,
    pub orb: OrbStatusInfo,
    pub mic: MicStatusInfo,
    pub tts: TtsStatusInfo,
    pub llm: LlmStatusInfo,
    pub components: HashMap<String, ComponentHealth>,
    pub models: ModelsStatusInfo,
}

struct InternalConnection {
    client_id: String,
    role: String,
    connected_at: Instant,
    connected_at_str: String,
}

struct ComponentState {
    last_heartbeat: Instant,
    restarts: u32,
    reported_status: String,
}

pub struct StatusManager {
    start_time: Instant,
    config_path: String,
    git_hash: String,
    heartbeat_timeout: Duration,
    last_error: Arc<RwLock<Option<LastErrorInfo>>>,
    connections: Arc<RwLock<HashMap<String, InternalConnection>>>,
    components: Arc<RwLock<HashMap<String, ComponentState>>>,
    mic_state: Arc<RwLock<(String, String)>>, // (state, reason)
    tts_state: Arc<RwLock<(String, u32)>>,    // (state, current_sentence)
    orb_state: Arc<RwLock<(String, bool, f32)>>, // (orb_state, edge_glow, level)
    llm_info: Arc<RwLock<LlmStatusInfo>>,
    fsm_state_time: Arc<RwLock<(String, Instant, Option<String>, Option<String>)>>, // (state, entered_at, prev, reason)
    session_info: Arc<RwLock<(Option<String>, u32, Option<Instant>, bool)>>, // (id, turn, start_time, exit_pending)
}

impl StatusManager {
    pub fn new(config_path: String) -> Self {
        let mut components = HashMap::new();
        let now = Instant::now();

        // Register default known components
        for comp in &["daemon", "ui", "stt", "tts"] {
            components.insert(
                comp.to_string(),
                ComponentState {
                    last_heartbeat: now,
                    restarts: 0,
                    reported_status: "up".to_string(),
                },
            );
        }

        Self {
            start_time: now,
            config_path,
            git_hash: env!("CARGO_PKG_VERSION").to_string(),
            heartbeat_timeout: DEFAULT_HEARTBEAT_TIMEOUT,
            last_error: Arc::new(RwLock::new(None)),
            connections: Arc::new(RwLock::new(HashMap::new())),
            components: Arc::new(RwLock::new(components)),
            mic_state: Arc::new(RwLock::new(("armed".to_string(), "ready".to_string()))),
            tts_state: Arc::new(RwLock::new(("idle".to_string(), 0))),
            orb_state: Arc::new(RwLock::new(("idle".to_string(), true, 0.0))),
            llm_info: Arc::new(RwLock::new(LlmStatusInfo {
                status: "ready".to_string(),
                model_name: "gpt-4o-mini".to_string(),
                last_request_result: None,
                last_latency_ms: None,
            })),
            fsm_state_time: Arc::new(RwLock::new((
                "idle".to_string(),
                now,
                None,
                Some("Startup".to_string()),
            ))),
            session_info: Arc::new(RwLock::new((None, 0, None, false))),
        }
    }

    pub async fn record_heartbeat(&self, component: &str, status: Option<&str>) {
        let mut comps = self.components.write().await;
        let entry = comps.entry(component.to_string()).or_insert_with(|| ComponentState {
            last_heartbeat: Instant::now(),
            restarts: 0,
            reported_status: "up".to_string(),
        });
        entry.last_heartbeat = Instant::now();
        if let Some(s) = status {
            entry.reported_status = s.to_string();
        }
    }

    pub async fn record_client_connect(&self, client_id: &str, role: &str) {
        let mut conns = self.connections.write().await;
        conns.insert(
            client_id.to_string(),
            InternalConnection {
                client_id: client_id.to_string(),
                role: role.to_string(),
                connected_at: Instant::now(),
                connected_at_str: Utc::now().to_rfc3339(),
            },
        );
    }

    pub async fn record_client_disconnect(&self, client_id: &str) {
        let mut conns = self.connections.write().await;
        conns.remove(client_id);
    }

    pub async fn record_fsm_transition(&self, new_state: &str, prev_state: Option<&str>, reason: Option<&str>) {
        let mut fsm = self.fsm_state_time.write().await;
        *fsm = (
            new_state.to_string(),
            Instant::now(),
            prev_state.map(|s| s.to_string()),
            reason.map(|r| r.to_string()),
        );

        // Also update orb state
        let mut orb = self.orb_state.write().await;
        orb.0 = new_state.to_string();
    }

    pub async fn update_orb_level(&self, level: f32) {
        let mut orb = self.orb_state.write().await;
        orb.2 = level;
    }

    pub async fn update_mic_state(&self, state: &str, reason: &str) {
        let mut mic = self.mic_state.write().await;
        *mic = (state.to_string(), reason.to_string());
    }

    pub async fn update_tts_state(&self, state: &str, sentence: u32) {
        let mut tts = self.tts_state.write().await;
        *tts = (state.to_string(), sentence);
    }

    pub async fn update_llm_status(
        &self,
        status: &str,
        model: Option<&str>,
        last_result: Option<&str>,
        latency_ms: Option<u64>,
    ) {
        let mut llm = self.llm_info.write().await;
        llm.status = status.to_string();
        if let Some(m) = model {
            llm.model_name = m.to_string();
        }
        if let Some(res) = last_result {
            llm.last_request_result = Some(res.to_string());
        }
        if let Some(lat) = latency_ms {
            llm.last_latency_ms = Some(lat);
        }
    }

    pub async fn update_session(
        &self,
        session_id: Option<&str>,
        turn: u32,
        exit_pending: bool,
    ) {
        let mut sess = self.session_info.write().await;
        let start = match (sess.0.as_deref(), session_id) {
            (Some(prev), Some(cur)) if prev == cur => sess.2,
            (_, Some(_)) => Some(Instant::now()),
            _ => None,
        };
        *sess = (session_id.map(|s| s.to_string()), turn, start, exit_pending);
    }

    pub async fn record_error(&self, message: &str) {
        let mut err = self.last_error.write().await;
        *err = Some(LastErrorInfo {
            time: Utc::now().to_rfc3339(),
            message: message.to_string(),
        });
    }

    pub async fn get_snapshot(&self) -> DaemonStatus {
        let uptime_secs = self.start_time.elapsed().as_secs();

        // 1. Process memory RSS
        let ram_mb = get_process_rss_mb();

        // 2. Connections
        let conns_guard = self.connections.read().await;
        let connections: Vec<ClientConnectionInfo> = conns_guard
            .values()
            .map(|c| ClientConnectionInfo {
                client_id: c.client_id.clone(),
                role: c.role.clone(),
                connected_at: c.connected_at_str.clone(),
                connected_duration_secs: c.connected_at.elapsed().as_secs_f64(),
            })
            .collect();

        // 3. Components health
        let comps_guard = self.components.read().await;
        let mut components = HashMap::new();
        let mut any_down = false;

        for (name, c) in comps_guard.iter() {
            let age_secs = c.last_heartbeat.elapsed().as_secs_f64();
            let is_down = age_secs > self.heartbeat_timeout.as_secs_f64();
            let status = if is_down {
                any_down = true;
                "down".to_string()
            } else {
                c.reported_status.clone()
            };

            components.insert(
                name.clone(),
                ComponentHealth {
                    name: name.clone(),
                    status,
                    last_heartbeat_age_secs: age_secs,
                    restarts_count: c.restarts,
                },
            );
        }

        // Overall daemon status
        let daemon_status = if any_down {
            "degraded".to_string()
        } else {
            "running".to_string()
        };

        let last_error = self.last_error.read().await.clone();

        // 4. Session
        let sess_guard = self.session_info.read().await;
        let time_since_start_secs = sess_guard.2.map(|s| s.elapsed().as_secs_f64());
        let session = SessionStatusInfo {
            session_id: sess_guard.0.clone(),
            turn_number: sess_guard.1,
            time_since_start_secs,
            exit_phrase_pending: sess_guard.3,
        };

        // 5. FSM
        let fsm_guard = self.fsm_state_time.read().await;
        let fsm = FsmStatusInfo {
            current_state: fsm_guard.0.clone(),
            time_in_state_secs: fsm_guard.1.elapsed().as_secs_f64(),
            previous_state: fsm_guard.2.clone(),
            transition_reason: fsm_guard.3.clone(),
        };

        // 6. Orb & UI
        let orb_guard = self.orb_state.read().await;
        let orb = OrbStatusInfo {
            orb_state: orb_guard.0.clone(),
            edge_glow_enabled: orb_guard.1,
            last_level: orb_guard.2,
        };

        // 7. Mic
        let mic_guard = self.mic_state.read().await;
        let mic = MicStatusInfo {
            state: mic_guard.0.clone(),
            reason: mic_guard.1.clone(),
        };

        // 8. TTS
        let tts_guard = self.tts_state.read().await;
        let tts = TtsStatusInfo {
            state: tts_guard.0.clone(),
            current_sentence: tts_guard.1,
        };

        // 9. LLM
        let llm = self.llm_info.read().await.clone();

        // 10. Models
        let models = ModelsStatusInfo {
            models_loaded: vec!["faster-whisper (base.en)".to_string(), "kokoro-v1.0 (af_bella)".to_string()],
            ram_mb,
            vram_mb: None,
        };

        DaemonStatus {
            daemon: DaemonInfo {
                status: daemon_status,
                pid: std::process::id(),
                uptime_secs,
                version: "0.1.0".to_string(),
                git_hash: self.git_hash.clone(),
                config_path: self.config_path.clone(),
                telemetry_schema_version: SCHEMA_VERSION.to_string(),
                last_error,
            },
            connections,
            session,
            fsm,
            orb,
            mic,
            tts,
            llm,
            components,
            models,
        }
    }
}

fn get_process_rss_mb() -> f64 {
    if let Ok(content) = std::fs::read_to_string("/proc/self/status") {
        for line in content.lines() {
            if line.starts_with("VmRSS:") {
                let parts: Vec<&str> = line.split_whitespace().collect();
                if parts.len() >= 2 {
                    if let Ok(kb) = parts[1].parse::<f64>() {
                        return kb / 1024.0;
                    }
                }
            }
        }
    }
    0.0
}
