//! LLM backend abstraction and OpenAI implementation with streaming HTTP completions.

use super::history::ChatMessage;
use super::speech_style::build_system_prompt;
use async_trait::async_trait;
use futures_util::Stream;
use futures_util::StreamExt;
use serde_json::json;
use std::env;
use std::pin::Pin;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::task::{Context, Poll};
use std::time::Duration;
use thiserror::Error;

static PRIVACY_LOGGED: AtomicBool = AtomicBool::new(false);

#[derive(Error, Debug, Clone)]
pub enum LlmError {
    #[error("Missing API key")]
    MissingApiKey,
    #[error("API request failed with status {0}")]
    ApiStatus(u16),
    #[error("Rate limited (429)")]
    RateLimited,
    #[error("Network connection error: {0}")]
    Network(String),
    #[error("Stream read timeout")]
    Timeout,
    #[error("JSON parse error: {0}")]
    Parse(String),
}

pub struct TokenStream {
    rx: tokio::sync::mpsc::Receiver<Result<String, LlmError>>,
}

impl Stream for TokenStream {
    type Item = Result<String, LlmError>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.rx.poll_recv(cx)
    }
}

#[async_trait]
pub trait LlmBackend: Send + Sync {
    async fn stream_reply(
        &self,
        history: &[ChatMessage],
        user_text: &str,
    ) -> Result<Pin<Box<dyn Stream<Item = Result<String, LlmError>> + Send>>, LlmError>;
}

#[derive(Clone)]
pub struct OpenAiConfig {
    pub model: String,
    pub endpoint: String,
    pub agent_name: String,
    pub user_name: String,
    pub connect_timeout: Duration,
    pub first_token_timeout: Duration,
    pub total_timeout: Duration,
    pub log_text: bool,
}

impl Default for OpenAiConfig {
    fn default() -> Self {
        Self {
            model: "gpt-4o-mini".to_string(),
            endpoint: "https://api.openai.com/v1/chat/completions".to_string(),
            agent_name: "Bella".to_string(),
            user_name: "Benin".to_string(),
            connect_timeout: Duration::from_secs(5),
            first_token_timeout: Duration::from_secs(10),
            total_timeout: Duration::from_secs(30),
            log_text: false,
        }
    }
}

pub struct OpenAiBackend {
    client: reqwest::Client,
    config: OpenAiConfig,
    api_key: Option<String>,
}

impl OpenAiBackend {
    pub fn new(config: OpenAiConfig) -> Self {
        let api_key = env::var("OPENAI_API_KEY").ok().filter(|k| !k.trim().is_empty());

        let client = reqwest::Client::builder()
            .connect_timeout(config.connect_timeout)
            .timeout(config.total_timeout)
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());

        // One-time privacy notice at startup
        if !PRIVACY_LOGGED.swap(true, Ordering::SeqCst) {
            tracing::info!(
                "PRIVACY NOTICE: Transcripts of conversational speech are sent to the external OpenAI API for processing."
            );
        }

        Self {
            client,
            config,
            api_key,
        }
    }

    pub fn has_api_key(&self) -> bool {
        self.api_key.as_ref().map(|k| !k.trim().is_empty()).unwrap_or(false)
            || env::var("OPENAI_API_KEY").map(|k| !k.trim().is_empty()).unwrap_or(false)
    }
}

#[async_trait]
impl LlmBackend for OpenAiBackend {
    async fn stream_reply(
        &self,
        history: &[ChatMessage],
        user_text: &str,
    ) -> Result<Pin<Box<dyn Stream<Item = Result<String, LlmError>> + Send>>, LlmError> {
        let key = match &self.api_key {
            Some(k) if !k.trim().is_empty() => k.clone(),
            _ => {
                if let Some(fresh) = env::var("OPENAI_API_KEY").ok().filter(|k| !k.trim().is_empty()) {
                    fresh
                } else {
                    return Err(LlmError::MissingApiKey);
                }
            }
        };

        let system_prompt = build_system_prompt(&self.config.agent_name, &self.config.user_name);

        let mut messages = Vec::new();
        messages.push(json!({
            "role": "system",
            "content": system_prompt
        }));

        for msg in history {
            messages.push(json!({
                "role": msg.role,
                "content": msg.content
            }));
        }

        messages.push(json!({
            "role": "user",
            "content": user_text
        }));

        let payload = json!({
            "model": self.config.model,
            "messages": messages,
            "stream": true
        });

        // Retry logic: 1 retry on 5xx or network error; zero retry on 4xx
        let mut attempts = 0;
        let resp = loop {
            attempts += 1;
            let req = self.client
                .post(&self.config.endpoint)
                .bearer_auth(&key)
                .header("Content-Type", "application/json")
                .json(&payload);

            let t0 = std::time::Instant::now();
            match req.send().await {
                Ok(r) => {
                    let status = r.status().as_u16();
                    if status == 429 {
                        return Err(LlmError::RateLimited);
                    }
                    if status >= 400 && status < 500 {
                        return Err(LlmError::ApiStatus(status));
                    }
                    if status >= 500 {
                        if attempts < 2 {
                            tracing::warn!("OpenAI API 5xx error (status {}), retrying once...", status);
                            tokio::time::sleep(Duration::from_millis(500)).await;
                            continue;
                        }
                        return Err(LlmError::ApiStatus(status));
                    }
                    tracing::info!(
                        "OpenAI API connection established in {:.2}ms (status {})",
                        t0.elapsed().as_secs_f64() * 1000.0,
                        status
                    );
                    break r;
                }
                Err(e) => {
                    if attempts < 2 {
                        tracing::warn!("OpenAI network connection failure ({}), retrying once...", e);
                        tokio::time::sleep(Duration::from_millis(500)).await;
                        continue;
                    }
                    return Err(LlmError::Network(e.to_string()));
                }
            }
        };

        let mut byte_stream = resp.bytes_stream();
        let log_text = self.config.log_text;
        let (tx, rx) = tokio::sync::mpsc::channel(128);

        tokio::spawn(async move {
            let mut buffer = String::new();
            let mut token_count = 0usize;
            let start = std::time::Instant::now();

            while let Some(chunk_res) = byte_stream.next().await {
                let chunk = match chunk_res {
                    Ok(c) => c,
                    Err(e) => {
                        let _ = tx.send(Err(LlmError::Network(e.to_string()))).await;
                        return;
                    }
                };
                let text = String::from_utf8_lossy(&chunk);
                buffer.push_str(&text);

                while let Some(pos) = buffer.find('\n') {
                    let line = buffer[..pos].trim().to_string();
                    buffer.drain(..=pos);

                    if line.is_empty() || line.starts_with(':') {
                        continue;
                    }

                    if line == "data: [DONE]" {
                        break;
                    }

                    if let Some(data) = line.strip_prefix("data: ") {
                        if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(data) {
                            if let Some(content) = parsed["choices"][0]["delta"]["content"].as_str() {
                                if !content.is_empty() {
                                    token_count += 1;
                                    if log_text {
                                        tracing::debug!("Token: {}", content);
                                    }
                                    if tx.send(Ok(content.to_string())).await.is_err() {
                                        return;
                                    }
                                }
                            }
                        }
                    }
                }
            }

            tracing::info!(
                "OpenAI streaming complete: received ~{} tokens in {:.2}s",
                token_count,
                start.elapsed().as_secs_f64()
            );
        });

        Ok(Box::pin(TokenStream { rx }))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LlmProvider {
    Local,
    OpenAi,
    Auto,
}

impl Default for LlmProvider {
    fn default() -> Self {
        match env::var("AGENTCTL_LLM_PROVIDER")
            .unwrap_or_default()
            .to_lowercase()
            .trim()
        {
            "local" => LlmProvider::Local,
            "openai" => LlmProvider::OpenAi,
            _ => LlmProvider::Auto,
        }
    }
}

#[derive(Clone, Debug)]
pub struct LocalLlmConfig {
    pub model: String,
    pub endpoint: String,
    pub agent_name: String,
    pub user_name: String,
    pub connect_timeout: Duration,
    pub first_token_timeout: Duration,
    pub total_timeout: Duration,
    pub log_text: bool,
}

impl Default for LocalLlmConfig {
    fn default() -> Self {
        let endpoint = env::var("AGENTCTL_LOCAL_LLM_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:8080/v1/chat/completions".to_string());
        let model = env::var("AGENTCTL_LOCAL_LLM_MODEL")
            .unwrap_or_else(|_| "qwen3.5-9b".to_string());
        Self {
            model,
            endpoint,
            agent_name: "Bella".to_string(),
            user_name: "Benin".to_string(),
            connect_timeout: Duration::from_secs(3),
            first_token_timeout: Duration::from_secs(10),
            total_timeout: Duration::from_secs(60),
            log_text: false,
        }
    }
}

pub struct LocalLlmBackend {
    client: reqwest::Client,
    config: LocalLlmConfig,
}

impl LocalLlmBackend {
    pub fn new(config: LocalLlmConfig) -> Self {
        let client = reqwest::Client::builder()
            .connect_timeout(config.connect_timeout)
            .timeout(config.total_timeout)
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());

        Self { client, config }
    }

    pub fn config(&self) -> &LocalLlmConfig {
        &self.config
    }
}

#[async_trait]
impl LlmBackend for LocalLlmBackend {
    async fn stream_reply(
        &self,
        history: &[ChatMessage],
        user_text: &str,
    ) -> Result<Pin<Box<dyn Stream<Item = Result<String, LlmError>> + Send>>, LlmError> {
        let system_prompt = build_system_prompt(&self.config.agent_name, &self.config.user_name);

        let mut messages = Vec::new();
        messages.push(json!({
            "role": "system",
            "content": system_prompt
        }));

        for msg in history {
            messages.push(json!({
                "role": msg.role,
                "content": msg.content
            }));
        }

        messages.push(json!({
            "role": "user",
            "content": user_text
        }));

        let payload = json!({
            "model": self.config.model,
            "messages": messages,
            "stream": true,
            "temperature": 0.7,
            "max_tokens": 512
        });

        let t0 = std::time::Instant::now();
        let resp = self
            .client
            .post(&self.config.endpoint)
            .header("Content-Type", "application/json")
            .header("Authorization", "Bearer local")
            .json(&payload)
            .send()
            .await
            .map_err(|e| {
                LlmError::Network(format!(
                    "Failed to connect to local LLM server at {}: {}. Ensure llama-server is running.",
                    self.config.endpoint, e
                ))
            })?;

        let status = resp.status().as_u16();
        if status >= 400 {
            return Err(LlmError::ApiStatus(status));
        }

        tracing::info!(
            "Local LLM (model '{}') connection established in {:.2}ms (status {})",
            self.config.model,
            t0.elapsed().as_secs_f64() * 1000.0,
            status
        );

        let mut byte_stream = resp.bytes_stream();
        let log_text = self.config.log_text;
        let (tx, rx) = tokio::sync::mpsc::channel(128);

        tokio::spawn(async move {
            let mut buffer = String::new();
            let mut token_count = 0usize;
            let start = std::time::Instant::now();

            while let Some(chunk_res) = byte_stream.next().await {
                let chunk = match chunk_res {
                    Ok(c) => c,
                    Err(e) => {
                        let _ = tx.send(Err(LlmError::Network(e.to_string()))).await;
                        return;
                    }
                };
                let text = String::from_utf8_lossy(&chunk);
                buffer.push_str(&text);

                while let Some(pos) = buffer.find('\n') {
                    let line = buffer[..pos].trim().to_string();
                    buffer.drain(..=pos);

                    if line.is_empty() || line.starts_with(':') {
                        continue;
                    }

                    if line == "data: [DONE]" {
                        break;
                    }

                    if let Some(data) = line.strip_prefix("data: ") {
                        if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(data) {
                            if let Some(content) = parsed["choices"][0]["delta"]["content"].as_str() {
                                if !content.is_empty() {
                                    token_count += 1;
                                    if log_text {
                                        tracing::debug!("Local Token: {}", content);
                                    }
                                    if tx.send(Ok(content.to_string())).await.is_err() {
                                        return;
                                    }
                                }
                            }
                        }
                    }
                }
            }

            let elapsed = start.elapsed().as_secs_f64();
            let speed = if elapsed > 0.0 {
                token_count as f64 / elapsed
            } else {
                0.0
            };
            tracing::info!(
                "Local LLM streaming complete: received ~{} tokens in {:.2}s ({:.1} tok/s)",
                token_count,
                elapsed,
                speed
            );
        });

        Ok(Box::pin(TokenStream { rx }))
    }
}

pub struct FallbackRouterBackend {
    primary: Arc<dyn LlmBackend>,
    fallback: Option<Arc<dyn LlmBackend>>,
    primary_name: String,
}

impl FallbackRouterBackend {
    pub fn new(primary: Arc<dyn LlmBackend>, fallback: Option<Arc<dyn LlmBackend>>, primary_name: &str) -> Self {
        Self {
            primary,
            fallback,
            primary_name: primary_name.to_string(),
        }
    }
}

#[async_trait]
impl LlmBackend for FallbackRouterBackend {
    async fn stream_reply(
        &self,
        history: &[ChatMessage],
        user_text: &str,
    ) -> Result<Pin<Box<dyn Stream<Item = Result<String, LlmError>> + Send>>, LlmError> {
        match self.primary.stream_reply(history, user_text).await {
            Ok(stream) => Ok(stream),
            Err(e) => {
                if let Some(ref fb) = self.fallback {
                    tracing::warn!(
                        "Primary LLM ({}) unavailable ({:?}); attempting fallback LLM backend...",
                        self.primary_name,
                        e
                    );
                    fb.stream_reply(history, user_text).await
                } else {
                    Err(e)
                }
            }
        }
    }
}

pub fn create_default_backend(agent_name: &str, user_name: &str) -> Arc<dyn LlmBackend> {
    let provider = LlmProvider::default();
    let local_cfg = LocalLlmConfig {
        agent_name: agent_name.to_string(),
        user_name: user_name.to_string(),
        ..Default::default()
    };
    let openai_cfg = OpenAiConfig {
        agent_name: agent_name.to_string(),
        user_name: user_name.to_string(),
        ..Default::default()
    };

    let openai_backend = OpenAiBackend::new(openai_cfg);
    let has_openai_key = openai_backend.has_api_key();
    let openai_arc = Arc::new(openai_backend);

    match provider {
        LlmProvider::Local => Arc::new(LocalLlmBackend::new(local_cfg)),
        LlmProvider::OpenAi => openai_arc,
        LlmProvider::Auto => {
            let local_arc = Arc::new(LocalLlmBackend::new(local_cfg));
            let fallback = if has_openai_key {
                Some(openai_arc as Arc<dyn LlmBackend>)
            } else {
                None
            };
            Arc::new(FallbackRouterBackend::new(local_arc, fallback, "local-llm"))
        }
    }
}
