//! Harness outer layer owning the conversation loop, LLM backend, and speech style.

pub mod exit;
pub mod history;
pub mod llm;
pub mod local_manager;
pub mod session;
pub mod speech_style;

pub use exit::is_exit_utterance;
pub use history::{ChatMessage, ConversationHistory};
pub use llm::{LlmBackend, LlmError, OpenAiBackend, OpenAiConfig};
pub use session::{HarnessConfig, HarnessSession, SessionEvent};
pub use speech_style::{build_system_prompt, normalize_for_tts, split_into_tts_chunks};

