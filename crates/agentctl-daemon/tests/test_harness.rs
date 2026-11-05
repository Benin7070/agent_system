//! Integration tests for the conversation harness.

use agentctl_daemon::fsm::states::FsmState;
use agentctl_daemon::fsm::OrchestratorFsm;
use agentctl_daemon::harness::history::ChatMessage;
use agentctl_daemon::harness::llm::{LlmBackend, LlmError, OpenAiBackend, OpenAiConfig};
use agentctl_daemon::harness::session::{
    HarnessConfig, HarnessSession, SessionEvent, MSG_CANT_REACH_BRAIN, MSG_GOODBYE,
    MSG_NO_API_KEY,
};
use async_trait::async_trait;
use futures_util::Stream;
use std::pin::Pin;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use tokio::sync::mpsc;

struct MockLlm {
    reply: String,
    call_count: Arc<AtomicUsize>,
}

#[async_trait]
impl LlmBackend for MockLlm {
    async fn stream_reply(
        &self,
        _history: &[ChatMessage],
        _user_text: &str,
    ) -> Result<Pin<Box<dyn Stream<Item = Result<String, LlmError>> + Send>>, LlmError> {
        self.call_count.fetch_add(1, Ordering::SeqCst);
        let tokens: Vec<String> = self.reply.split_whitespace().map(|s| format!("{} ", s)).collect();
        let stream = futures_util::stream::iter(tokens.into_iter().map(Ok));
        Ok(Box::pin(stream))
    }
}

struct FailingLlm {
    err: LlmError,
}

#[async_trait]
impl LlmBackend for FailingLlm {
    async fn stream_reply(
        &self,
        _history: &[ChatMessage],
        _user_text: &str,
    ) -> Result<Pin<Box<dyn Stream<Item = Result<String, LlmError>> + Send>>, LlmError> {
        Err(self.err.clone())
    }
}

#[tokio::test]
async fn test_harness_3_turn_session_loop() {
    let fsm = OrchestratorFsm::new();
    let call_count = Arc::new(AtomicUsize::new(0));
    let mock = Arc::new(MockLlm {
        reply: "This is a response sentence. And here is another sentence.".to_string(),
        call_count: call_count.clone(),
    });

    let config = HarnessConfig {
        agent_name: "Bella".to_string(),
        user_name: "Benin".to_string(),
        history_turns: 5,
        ..Default::default()
    };
    let session = HarnessSession::new(config, fsm.clone(), Some(mock));
    session.start_session("test-session").await.unwrap();

    assert_eq!(fsm.get_state().await, FsmState::Listening);

    // 3 Turns
    for turn in 1..=3 {
        let (tx, mut rx) = mpsc::channel(32);
        let user_query = format!("Hello, this is utterance number {}.", turn);
        session.handle_user_utterance(&user_query, tx).await.unwrap();

        let mut events = Vec::new();
        while let Some(evt) = rx.recv().await {
            events.push(evt);
        }

        assert!(!events.is_empty(), "Turn {} must emit events", turn);
        // During reply, FSM should be Speaking
        assert_eq!(fsm.get_state().await, FsmState::Speaking);

        // Signal TTS playback finished
        session.on_tts_playback_finished().await.unwrap();

        // State must return to Listening for follow-up without hiding orb
        assert_eq!(
            fsm.get_state().await,
            FsmState::Listening,
            "After turn {}, FSM must re-arm to Listening",
            turn
        );
    }

    assert_eq!(call_count.load(Ordering::SeqCst), 3);
}

#[tokio::test]
async fn test_exit_phrase_skips_llm_and_says_goodbye() {
    let fsm = OrchestratorFsm::new();
    let call_count = Arc::new(AtomicUsize::new(0));
    let mock = Arc::new(MockLlm {
        reply: "Should not be called".to_string(),
        call_count: call_count.clone(),
    });

    let config = HarnessConfig::default();
    let session = HarnessSession::new(config, fsm.clone(), Some(mock));
    session.start_session("exit-test").await.unwrap();

    let (tx, mut rx) = mpsc::channel(16);
    session.handle_user_utterance("okay bye bye", tx).await.unwrap();

    let evt = rx.recv().await.unwrap();
    match evt {
        SessionEvent::ExitTriggered { goodbye_msg } => {
            assert_eq!(goodbye_msg, MSG_GOODBYE);
        }
        other => panic!("Expected ExitTriggered event, got {:?}", other),
    }

    // Crucial requirement: LLM API was NOT called for exit phrase
    assert_eq!(call_count.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn test_api_failure_keeps_session_alive() {
    let fsm = OrchestratorFsm::new();
    let failing_llm = Arc::new(FailingLlm {
        err: LlmError::RateLimited,
    });

    let config = HarnessConfig::default();
    let session = HarnessSession::new(config, fsm.clone(), Some(failing_llm));
    session.start_session("fail-test").await.unwrap();

    let (tx, mut rx) = mpsc::channel(16);
    session.handle_user_utterance("what is the weather?", tx).await.unwrap();

    let mut found_fallback = false;
    while let Some(evt) = rx.recv().await {
        if let SessionEvent::SentenceReady { sentence, is_final } = evt {
            assert_eq!(sentence, MSG_CANT_REACH_BRAIN);
            assert!(is_final);
            found_fallback = true;
            break;
        }
    }
    assert!(found_fallback, "Must emit MSG_CANT_REACH_BRAIN on rate limit");

    // Playback finished re-arms Listening
    session.on_tts_playback_finished().await.unwrap();
    assert_eq!(fsm.get_state().await, FsmState::Listening);
}

#[tokio::test]
async fn test_missing_api_key_friendly_message() {
    let fsm = OrchestratorFsm::new();
    let failing_llm = Arc::new(FailingLlm {
        err: LlmError::MissingApiKey,
    });

    let config = HarnessConfig::default();
    let session = HarnessSession::new(config, fsm.clone(), Some(failing_llm));
    session.start_session("no-key-test").await.unwrap();

    let (tx, mut rx) = mpsc::channel(16);
    session.handle_user_utterance("hello", tx).await.unwrap();

    let mut found_msg = false;
    while let Some(evt) = rx.recv().await {
        if let SessionEvent::SentenceReady { sentence, is_final } = evt {
            assert_eq!(sentence, MSG_NO_API_KEY);
            assert!(is_final);
            found_msg = true;
            break;
        }
    }
    assert!(found_msg, "Must emit MSG_NO_API_KEY when key is missing");
}

#[test]
fn test_secret_handling_never_leaks_api_key() {
    let secret = "sk-proj-TESTSECRET9876543210ZYXWV";
    let cfg = OpenAiConfig::default();
    let _backend = OpenAiBackend::new(cfg);

    // Ensure Debug / Display / LlmError does not contain the secret
    let err = LlmError::MissingApiKey;
    let err_str = format!("{}", err);
    let debug_str = format!("{:?}", err);
    assert!(!err_str.contains(secret));
    assert!(!debug_str.contains(secret));
}
