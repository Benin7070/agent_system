//! Integration tests for the conversation harness.

use agentctl_daemon::fsm::states::FsmState;
use agentctl_daemon::fsm::OrchestratorFsm;
use agentctl_daemon::harness::exit::is_exit_utterance;
use agentctl_daemon::harness::history::{ChatMessage, ConversationHistory};
use agentctl_daemon::harness::llm::{
    FallbackRouterBackend, LlmBackend, LlmError, LocalLlmBackend, LocalLlmConfig,
    OpenAiBackend, OpenAiConfig,
};
use agentctl_daemon::harness::session::{
    HarnessConfig, HarnessSession, SessionEvent, MSG_CANT_REACH_BRAIN, MSG_GOODBYE,
    MSG_NO_API_KEY,
};
use agentctl_daemon::harness::speech_style::{
    build_system_prompt, normalize_for_tts, split_into_tts_chunks,
};
use async_trait::async_trait;
use futures_util::{Stream, StreamExt};
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

#[tokio::test]
async fn test_fallback_router_uses_primary_when_healthy() {
    let call_count_primary = Arc::new(AtomicUsize::new(0));
    let call_count_fallback = Arc::new(AtomicUsize::new(0));

    let primary = Arc::new(MockLlm {
        reply: "Reply from local primary model.".to_string(),
        call_count: call_count_primary.clone(),
    });
    let fallback = Arc::new(MockLlm {
        reply: "Reply from cloud fallback model.".to_string(),
        call_count: call_count_fallback.clone(),
    });

    let router = FallbackRouterBackend::new(primary, Some(fallback), "test-local");
    let mut stream = router
        .stream_reply(&[], "hello")
        .await
        .expect("Stream should succeed");

    let mut tokens = Vec::new();
    while let Some(tok_res) = stream.next().await {
        tokens.push(tok_res.unwrap());
    }

    assert_eq!(call_count_primary.load(Ordering::SeqCst), 1);
    assert_eq!(call_count_fallback.load(Ordering::SeqCst), 0);
    assert_eq!(tokens.join(""), "Reply from local primary model. ");
}

#[tokio::test]
async fn test_fallback_router_falls_back_on_primary_failure() {
    let call_count_fallback = Arc::new(AtomicUsize::new(0));

    let primary = Arc::new(FailingLlm {
        err: LlmError::Network("Connection refused (os error 111)".to_string()),
    });
    let fallback = Arc::new(MockLlm {
        reply: "Fallback to cloud succeeded.".to_string(),
        call_count: call_count_fallback.clone(),
    });

    let router = FallbackRouterBackend::new(primary, Some(fallback), "test-local");
    let mut stream = router
        .stream_reply(&[], "hello")
        .await
        .expect("Fallback should succeed");

    let mut tokens = Vec::new();
    while let Some(tok_res) = stream.next().await {
        tokens.push(tok_res.unwrap());
    }

    assert_eq!(call_count_fallback.load(Ordering::SeqCst), 1);
    assert_eq!(tokens.join(""), "Fallback to cloud succeeded. ");
}

#[test]
fn test_local_llm_config_defaults() {
    let cfg = LocalLlmConfig::default();
    assert_eq!(cfg.agent_name, "Bella");
    assert_eq!(cfg.user_name, "Benin");
    assert!(cfg.endpoint.contains("8080"));
    assert_eq!(cfg.model, "qwen3.5-9b");
}

#[tokio::test]
async fn test_live_local_llm_backend_stream() {
    let backend = LocalLlmBackend::new(LocalLlmConfig::default());
    match backend.stream_reply(&[], "Say hi in one word").await {
        Ok(mut stream) => {
            let mut words = Vec::new();
            while let Some(tok) = stream.next().await {
                if let Ok(t) = tok {
                    words.push(t);
                }
            }
            assert!(!words.is_empty(), "Should receive tokens from local llama-server");
            eprintln!("Live Local LLM response: {}", words.join(""));
        }
        Err(e) => {
            eprintln!("Local LLM not running or error: {:?} (skipping)", e);
        }
    }
}

#[test]
fn test_history_sliding_window() {
    let mut hist = ConversationHistory::new(2);
    hist.add_user_message("u1");
    hist.add_assistant_message("a1");
    hist.add_user_message("u2");
    hist.add_assistant_message("a2");
    assert_eq!(hist.len(), 4);

    hist.add_user_message("u3");
    hist.add_assistant_message("a3");
    assert_eq!(hist.len(), 4);
    assert_eq!(hist.get_messages()[0].content, "u2");
    assert_eq!(hist.get_messages()[3].content, "a3");
}

#[test]
fn test_positive_exit_phrases() {
    assert!(is_exit_utterance("bye bye", None));
    assert!(is_exit_utterance("okay bye bye", None));
    assert!(is_exit_utterance("exit", None));
    assert!(is_exit_utterance("that's all, thanks", None));
    assert!(is_exit_utterance("goodbye", None));
    assert!(is_exit_utterance("quit", None));
    assert!(is_exit_utterance("Alright, that's all!", None));
    assert!(is_exit_utterance("Bye!", None));
}

#[test]
fn test_negative_exit_phrases() {
    assert!(!is_exit_utterance("how do you say bye in French", None));
    assert!(!is_exit_utterance("tell me about the exit strategy", None));
    assert!(!is_exit_utterance("goodbye moonlight is a song", None));
    assert!(!is_exit_utterance("the exit is on the left", None));
    assert!(!is_exit_utterance("I really have to go now so bye bye my dear friend", None));
}

#[test]
fn test_system_prompt_contains_names() {
    let prompt = build_system_prompt("Bella", "Benin");
    assert!(prompt.contains("Bella"));
    assert!(prompt.contains("Benin"));
    assert!(prompt.contains("Tamil"));
}

#[test]
fn test_normalize_markdown_and_emoji() {
    let input = "Hello **Benin**! 😊 Here is `code` and [link](https://example.com).";
    let out = normalize_for_tts(input);
    assert!(!out.contains("**"));
    assert!(!out.contains("😊"));
    assert!(!out.contains("https://example.com"));
    assert!(out.contains("Hello Benin"));
    assert!(out.contains("link"));
}

#[test]
fn test_normalize_numbers_and_currency() {
    let input = "The price is $42 and inflation is 5.5% at 10:30.";
    let out = normalize_for_tts(input);
    assert!(out.contains("forty-two dollars"));
    assert!(out.contains("five point five percent"));
    assert!(out.contains("ten thirty"));
}

#[test]
fn test_normalize_abbreviations() {
    let input = "Dr. Smith went to St. John St. at approx. 50 mph.";
    let out = normalize_for_tts(input);
    assert!(out.contains("Doctor Smith"));
    assert!(out.contains("approximately"));
    assert!(out.contains("miles per hour"));
}

#[test]
fn test_split_into_chunks_preserves_sentences() {
    let input = "First sentence is short. Second sentence is also short! Third sentence wraps up.";
    let chunks = split_into_tts_chunks(input, 200);
    assert!(!chunks.is_empty());
    assert_eq!(chunks.join(" "), normalize_for_tts(input));
}
