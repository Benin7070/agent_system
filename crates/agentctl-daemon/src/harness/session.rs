//! Harness session manager owning the conversation loop.

use super::exit::is_exit_utterance;
use super::history::ConversationHistory;
use super::llm::{LlmBackend, LlmError, OpenAiBackend, OpenAiConfig};
use super::speech_style::split_into_tts_chunks;
use crate::fsm::states::FsmState;
use crate::fsm::OrchestratorFsm;
use futures_util::StreamExt;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::RwLock;

pub const MSG_GOODBYE: &str = "Goodbye!";
pub const MSG_CANT_REACH_BRAIN: &str = "Sorry, I can't reach my brain right now.";
pub const MSG_DIDNT_CATCH_THAT: &str = "Sorry, I didn't catch that.";
pub const MSG_NO_API_KEY: &str = "No OpenAI API key was found in the environment.";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionEvent {
    SentenceReady { sentence: String, is_final: bool },
    StateChanged(FsmState),
    ExitTriggered { goodbye_msg: String },
    Error(String),
}

#[derive(Debug, Clone)]
pub struct HarnessConfig {
    pub agent_name: String,
    pub user_name: String,
    pub history_turns: usize,
    pub idle_timeout: Option<Duration>,
    pub max_chunk_chars: usize,
}

impl Default for HarnessConfig {
    fn default() -> Self {
        Self {
            agent_name: "Bella".to_string(),
            user_name: "Benin".to_string(),
            history_turns: 10,
            idle_timeout: None,
            max_chunk_chars: 200,
        }
    }
}

pub struct HarnessSession {
    config: HarnessConfig,
    fsm: OrchestratorFsm,
    llm: Arc<dyn LlmBackend>,
    history: Arc<RwLock<ConversationHistory>>,
    unreliable_stt_count: Arc<RwLock<usize>>,
    active_session_id: Arc<RwLock<Option<String>>>,
}

impl HarnessSession {
    pub fn new(config: HarnessConfig, fsm: OrchestratorFsm, llm: Option<Arc<dyn LlmBackend>>) -> Self {
        let history = Arc::new(RwLock::new(ConversationHistory::new(config.history_turns)));
        let unreliable_stt_count = Arc::new(RwLock::new(0));
        let active_session_id = Arc::new(RwLock::new(None));

        let backend: Arc<dyn LlmBackend> = match llm {
            Some(b) => b,
            None => {
                let openai_cfg = OpenAiConfig {
                    agent_name: config.agent_name.clone(),
                    user_name: config.user_name.clone(),
                    ..Default::default()
                };
                Arc::new(OpenAiBackend::new(openai_cfg))
            }
        };

        Self {
            config,
            fsm,
            llm: backend,
            history,
            unreliable_stt_count,
            active_session_id,
        }
    }

    pub async fn start_session(&self, session_id: &str) -> Result<(), anyhow::Error> {
        let mut active = self.active_session_id.write().await;
        *active = Some(session_id.to_string());
        self.history.write().await.clear();
        *self.unreliable_stt_count.write().await = 0;

        self.fsm.transition_to(FsmState::Listening).await?;
        tracing::info!("Harness session '{}' started in Listening state", session_id);
        Ok(())
    }

    pub async fn is_active(&self) -> bool {
        self.active_session_id.read().await.is_some()
    }

    pub async fn get_session_id(&self) -> Option<String> {
        self.active_session_id.read().await.clone()
    }

    /// Handles a transcribed user utterance.
    /// Emits `SessionEvent`s through the provided channel.
    pub async fn handle_user_utterance(
        &self,
        user_text: &str,
        event_tx: tokio::sync::mpsc::Sender<SessionEvent>,
    ) -> Result<(), anyhow::Error> {
        let trimmed = user_text.trim();
        if trimmed.is_empty() {
            let mut count = self.unreliable_stt_count.write().await;
            *count += 1;
            if *count >= 2 {
                *count = 0;
                let _ = self.fsm.transition_to(FsmState::Speaking).await;
                let _ = event_tx
                    .send(SessionEvent::SentenceReady {
                        sentence: MSG_DIDNT_CATCH_THAT.to_string(),
                        is_final: true,
                    })
                    .await;
            }
            return Ok(());
        }

        // Reset unreliable count on valid speech
        *self.unreliable_stt_count.write().await = 0;

        // 1. Exit phrase check (BEFORE calling LLM API)
        if is_exit_utterance(trimmed, None) {
            tracing::info!("Exit phrase detected: '{}'", trimmed);
            let _ = self.fsm.transition_to(FsmState::Speaking).await;
            let _ = event_tx
                .send(SessionEvent::ExitTriggered {
                    goodbye_msg: MSG_GOODBYE.to_string(),
                })
                .await;
            return Ok(());
        }

        // 2. Transition to Thinking / Processing
        self.fsm.transition_to(FsmState::Processing).await?;
        let _ = event_tx.send(SessionEvent::StateChanged(FsmState::Processing)).await;

        // 3. Call LLM Backend
        let history_snapshot = self.history.read().await.get_messages().to_vec();
        let stream_result = self.llm.stream_reply(&history_snapshot, trimmed).await;

        let mut token_stream = match stream_result {
            Ok(s) => s,
            Err(e) => {
                tracing::warn!("LLM stream error: {}", e);
                let fallback_msg = match e {
                    LlmError::MissingApiKey => MSG_NO_API_KEY,
                    _ => MSG_CANT_REACH_BRAIN,
                };
                let _ = self.fsm.transition_to(FsmState::Speaking).await;
                let _ = event_tx
                    .send(SessionEvent::SentenceReady {
                        sentence: fallback_msg.to_string(),
                        is_final: true,
                    })
                    .await;
                return Ok(());
            }
        };

        // 4. Stream tokens, segment into speech chunks, and emit for Kokoro synthesis
        let mut full_reply = String::new();
        let mut accumulated_tokens = String::new();
        let mut speaking_started = false;

        while let Some(token_res) = token_stream.next().await {
            match token_res {
                Ok(token) => {
                    accumulated_tokens.push_str(&token);
                    full_reply.push_str(&token);

                    // Check if we reached a terminal sentence boundary
                    if accumulated_tokens.contains('.')
                        || accumulated_tokens.contains('?')
                        || accumulated_tokens.contains('!')
                    {
                        let chunks = split_into_tts_chunks(&accumulated_tokens, self.config.max_chunk_chars);
                        if chunks.len() > 1 {
                            // First chunks are completed sentences
                            for sentence in &chunks[..chunks.len() - 1] {
                                if !speaking_started {
                                    speaking_started = true;
                                    let _ = self.fsm.transition_to(FsmState::Speaking).await;
                                    let _ = event_tx.send(SessionEvent::StateChanged(FsmState::Speaking)).await;
                                }
                                let _ = event_tx
                                    .send(SessionEvent::SentenceReady {
                                        sentence: sentence.clone(),
                                        is_final: false,
                                    })
                                    .await;
                            }
                            accumulated_tokens = chunks.last().cloned().unwrap_or_default();
                        }
                    }
                }
                Err(e) => {
                    tracing::warn!("Token stream interruption: {}", e);
                    break;
                }
            }
        }

        // Remaining trailing tokens
        let trailing = accumulated_tokens.trim();
        if !trailing.is_empty() {
            let chunks = split_into_tts_chunks(trailing, self.config.max_chunk_chars);
            for (idx, sentence) in chunks.iter().enumerate() {
                if !speaking_started {
                    speaking_started = true;
                    let _ = self.fsm.transition_to(FsmState::Speaking).await;
                    let _ = event_tx.send(SessionEvent::StateChanged(FsmState::Speaking)).await;
                }
                let is_last = idx == chunks.len() - 1;
                let _ = event_tx
                    .send(SessionEvent::SentenceReady {
                        sentence: sentence.clone(),
                        is_final: is_last,
                    })
                    .await;
            }
        }

        // Save conversation history
        {
            let mut hist = self.history.write().await;
            hist.add_user_message(trimmed);
            hist.add_assistant_message(full_reply);
        }

        Ok(())
    }

    /// Called by the audio/TTS worker when spoken audio playback has finished.
    pub async fn on_tts_playback_finished(&self) -> Result<(), anyhow::Error> {
        let current = self.fsm.get_state().await;
        if current == FsmState::Speaking {
            self.fsm.transition_to(FsmState::Listening).await?;
            tracing::info!("Playback finished. Re-armed FSM to Listening state for follow-up.");
        }
        Ok(())
    }

    /// Graceful session termination.
    pub async fn end_session(&self) -> Result<(), anyhow::Error> {
        *self.active_session_id.write().await = None;
        let _ = self.fsm.transition_to(FsmState::Cancelling).await;
        let _ = self.fsm.transition_to(FsmState::Idle).await;
        self.history.write().await.clear();
        tracing::info!("Session terminated cleanly.");
        Ok(())
    }
}

