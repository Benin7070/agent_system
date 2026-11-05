# AgentCTL Telemetry & Event Log Schema Specification
Schema Version: `1.0.0`

## 1. Overview
The AgentCTL telemetry subsystem provides non-blocking, privacy-preserving, structured event logging and real-time status introspection for the Linux agent orchestrator daemon, voice overlay, and client tools.

Log directory: `$XDG_STATE_HOME/agentctl/logs/` (default `~/.local/state/agentctl/logs/`), directory permissions `0700` (`rwx------`), files `0600` (`rw-------`).

Outputs:
1. `events.jsonl`: Structured JSON Lines for machine telemetry and log filtering.
2. `daemon.log`: Human-readable formatted line logs.
3. `crash-<timestamp>.log`: Written on panic/fatal crash with reason, backtrace, and ring buffer of the last 50 events.

---

## 2. Event Types & Taxonomy

All events carry:
- `timestamp`: RFC 3339 / ISO-8601 UTC timestamp with millisecond resolution (`YYYY-MM-DDTHH:MM:SS.mmmZ`).
- `level`: Log level (`debug`, `info`, `warn`, `error`).
- `event`: Period-delimited taxonomy name (e.g. `session.turn_finished`).
- `component`: Component name (`daemon`, `fsm`, `session`, `harness`, `llm`, `stt`, `tts`, `ui`, `client`).
- `session_id`: Optional string identifying the active conversational session.
- `turn_id`: Optional 64-bit integer tracking the turn number within the session.
- `message`: Human-readable summary string.
- `payload`: Structured JSON object with domain metadata.

### 2.1 Daemon Lifecycle (`daemon.*`)
- `daemon.process_start`: Emitted when `agentctl-daemon` initializes.
  - Payload: `{ "pid": u32, "version": str, "git_hash": str, "schema_version": "1.0.0", "config_path": str }`
- `daemon.config_loaded`: Emitted after parsing `.env` or config files. All secrets redacted.
  - Payload: `{ "loaded_keys": [str], "log_text": bool, "agent_name": str, "user_name": str }`
- `daemon.component_started`: Emitted when a subsystem or worker starts.
  - Payload: `{ "component": str, "details": str }`
- `daemon.component_stopped`: Emitted when a subsystem terminates cleanly.
  - Payload: `{ "component": str, "exit_code": i32 }`
- `daemon.component_restarted`: Emitted when a subsystem is restarted.
  - Payload: `{ "component": str, "restarts_count": u32 }`
- `daemon.component_crashed`: Emitted if a child process or component fails unexpectedly.
  - Payload: `{ "component": str, "exit_code": i32, "stderr_tail": str }`
- `daemon.signal_received`: Emitted when an OS signal arrives.
  - Payload: `{ "signal": "SIGTERM" | "SIGINT" | "SIGHUP" }`
- `daemon.shutdown_requested`: Emitted when termination sequence begins.
  - Payload: `{ "requester": str, "reason": str }`
- `daemon.clean_exit`: Emitted immediately prior to process exit.
  - Payload: `{ "exit_code": i32, "uptime_secs": u64 }`

### 2.2 Client Connections (`client.*`)
- `client.connect`: Emitted when a client connects to the daemon UDS/WebSocket.
  - Payload: `{ "client_id": str, "role": "ui" | "monitor" | "worker", "addr": str }`
- `client.disconnect`: Emitted when a client disconnects.
  - Payload: `{ "client_id": str, "role": str, "duration_secs": f64 }`

### 2.3 FSM Transitions (`fsm.*`)
- `fsm.transition`: Emitted on any state transition.
  - Payload: `{ "from": str, "to": str, "reason": str, "time_in_prev_state_secs": f64 }`

### 2.4 Session Lifecycle (`session.*`)
- `session.started`: Emitted when a conversation session starts.
  - Payload: `{ "session_id": str }`
- `session.turn_started`: Emitted when a user begins speaking in a turn.
  - Payload: `{ "session_id": str, "turn_id": u64 }`
- `session.turn_finished`: Emitted when reply playback ends.
  - Payload: `{ "session_id": str, "turn_id": u64, "total_duration_secs": f64 }`
- `session.exit_phrase_detected`: Emitted when an exit phrase is detected. Text contains only the matched keyword, never the user's full sentence.
  - Payload: `{ "matched_phrase": str }`
- `session.ended`: Emitted when the session is closed.
  - Payload: `{ "session_id": str, "total_turns": u32, "duration_secs": f64, "reason": "exit_phrase" | "idle_timeout" | "command" | "error" }`

### 2.5 Pipeline Events (`pipeline.*`)
- `pipeline.utterance_captured`: Emitted when voice activity detection detects speech.
  - Payload: `{ "duration_ms": u64, "speech_frames": u32 }`
- `pipeline.stt_result`: Emitted when Whisper completes transcription. Contains metadata only; transcript text omitted unless `log_text=true`.
  - Payload: `{ "duration_ms": u64, "audio_len_secs": f64, "word_count": u32, "confidence": f64, "text": Option<str> }`
- `pipeline.llm_request_started`: Emitted when calling LLM streaming endpoint.
  - Payload: `{ "model": str, "history_turns": usize }`
- `pipeline.llm_first_token`: Emitted on receipt of the first chunk of text from the LLM.
  - Payload: `{ "latency_ms": u64 }`
- `pipeline.llm_finished`: Emitted when LLM stream completes.
  - Payload: `{ "total_latency_ms": u64, "token_count": u32, "text": Option<str> }`
- `pipeline.llm_failed`: Emitted on LLM API or network failure.
  - Payload: `{ "error": str, "will_retry": bool }`
- `pipeline.llm_retried`: Emitted when retry is dispatched.
  - Payload: `{ "attempt": u32, "delay_ms": u64 }`
- `pipeline.tts_synthesised`: Emitted when Kokoro synthesizes a sentence into PCM audio.
  - Payload: `{ "sentence_idx": u32, "synth_duration_ms": u64, "audio_duration_secs": f64, "rtf": f64 }`
- `pipeline.tts_playback_started`: Emitted when audio playback starts.
  - Payload: `{ "sentence_idx": u32 }`
- `pipeline.tts_playback_ended`: Emitted when audio playback finishes.
  - Payload: `{ "sentence_idx": u32, "playback_duration_secs": f64 }`
- `pipeline.mic_paused`: Emitted when mic is muted.
  - Payload: `{ "reason": "paused while speaking" | "session idle" }`
- `pipeline.mic_rearmed`: Emitted when mic is unmuted.
  - Payload: `{ "reason": "follow-up listening" | "session start" }`

### 2.6 UI Events (`ui.*`)
- `ui.orb_state_changed`: Emitted when orb visual state changes.
  - Payload: `{ "state": "idle" | "listening" | "thinking" | "speaking" | "confirm" | "denied" }`
- `ui.opening`: Emitted when opening animation triggers.
- `ui.closing`: Emitted when closing animation triggers.
- `ui.glow_changed`: Emitted when screen-edge glow updates.
  - Payload: `{ "enabled": bool, "intensity": f32 }`

### 2.7 Alerts and Warnings (`alert.*`)
- `alert.slow_stage`: Emitted when an operational stage exceeds its SLA.
  - Payload: `{ "stage": str, "elapsed_ms": u64, "threshold_ms": u64 }`
- `alert.queue_drops`: Emitted when the in-memory bounded logger drops events under disk saturation.
  - Payload: `{ "dropped_count": u64, "level": str }`
- `alert.api_error`: Emitted on HTTP 4xx/5xx/timeout.
  - Payload: `{ "endpoint": str, "status_code": u16, "message": str }`
- `alert.heartbeat_missed`: Emitted when a registered component fails to send a heartbeat within the SLA (default 3.0s).
  - Payload: `{ "component": str, "last_heartbeat_age_secs": f64 }`
- `alert.exception`: Emitted on caught exception.
  - Payload: `{ "component": str, "error": str, "backtrace_short": str }`

---

## 3. Privacy & Secret Redaction Policy
1. By default, **METADATA ONLY** is written to disk. User speech transcript text and LLM response text are suppressed unless `logging.log_text = true` (`AGENTCTL_LOG_TEXT=1`).
2. If `log_text = true`, a prominent notice is logged at startup to both the file and terminal:
   `"WARNING: log_text is ENABLED. Speech and LLM response text will be written to logs."`
3. All sensitive credentials, including API keys (`sk-...`), Bearer authorization tokens, and environment variables matching `*KEY*`, `*SECRET*`, `*TOKEN*`, `*AUTH*`, `*PASS*` are automatically stripped or replaced with `[REDACTED]`.
4. The test suite includes automated secret-redaction verification asserting that no secret token ever appears in `events.jsonl` or `daemon.log`.

