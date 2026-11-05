//! Multi-turn conversation history management.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ChatMessage {
    pub role: String,
    pub content: String,
}

impl ChatMessage {
    pub fn system(content: impl Into<String>) -> Self {
        Self {
            role: "system".to_string(),
            content: content.into(),
        }
    }

    pub fn user(content: impl Into<String>) -> Self {
        Self {
            role: "user".to_string(),
            content: content.into(),
        }
    }

    pub fn assistant(content: impl Into<String>) -> Self {
        Self {
            role: "assistant".to_string(),
            content: content.into(),
        }
    }
}

#[derive(Debug, Clone)]
pub struct ConversationHistory {
    max_turns: usize,
    turns: Vec<ChatMessage>,
}

impl ConversationHistory {
    pub fn new(max_turns: usize) -> Self {
        Self {
            max_turns: if max_turns == 0 { 10 } else { max_turns },
            turns: Vec::new(),
        }
    }

    pub fn add_user_message(&mut self, text: impl Into<String>) {
        self.turns.push(ChatMessage::user(text));
        self.truncate_if_needed();
    }

    pub fn add_assistant_message(&mut self, text: impl Into<String>) {
        self.turns.push(ChatMessage::assistant(text));
        self.truncate_if_needed();
    }

    pub fn get_messages(&self) -> &[ChatMessage] {
        &self.turns
    }

    pub fn clear(&mut self) {
        self.turns.clear();
    }

    pub fn len(&self) -> usize {
        self.turns.len()
    }

    pub fn is_empty(&self) -> bool {
        self.turns.is_empty()
    }

    fn truncate_if_needed(&mut self) {
        // Keep at most max_turns * 2 messages (pairs of user/assistant)
        let max_messages = self.max_turns * 2;
        if self.turns.len() > max_messages {
            let drop_count = self.turns.len() - max_messages;
            self.turns.drain(0..drop_count);
        }
    }
}

