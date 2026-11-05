//! Exit phrase detection for the voice assistant.
//!
//! Match rules (to avoid false exits):
//! - The utterance is an exit only if it has at most 6 words.
//! - It contains an exit phrase at its start or its end.
//! - Non-exit words must be polite wrap-up tokens (e.g. "okay", "thanks", "thank you").
//! - Comparison is case-insensitive and ignores punctuation.

pub const DEFAULT_EXIT_PHRASES: &[&str] = &[
    "bye bye",
    "bye",
    "goodbye",
    "exit",
    "quit",
    "that's all",
    "thats all",
];

const ALLOWED_AFFIX_TOKENS: &[&str] = &[
    "ok", "okay", "alright", "well", "thanks", "thank", "you", "now", "please", "good", "yeah", "yes", "so"
];

/// Sanitizes text into clean lowercase words without punctuation.
fn extract_words(text: &str) -> Vec<String> {
    text.split_whitespace()
        .map(|w| {
            w.chars()
                .filter(|c| c.is_alphanumeric() || *c == '\'')
                .collect::<String>()
                .to_lowercase()
        })
        .filter(|w| !w.is_empty())
        .collect()
}

/// Checks if an utterance should trigger conversation exit.
pub fn is_exit_utterance(text: &str, custom_phrases: Option<&[&str]>) -> bool {
    let words = extract_words(text);
    if words.is_empty() || words.len() > 6 {
        return false;
    }

    let phrases = custom_phrases.unwrap_or(DEFAULT_EXIT_PHRASES);

    for phrase in phrases {
        let p_words = extract_words(phrase);
        if p_words.is_empty() || p_words.len() > words.len() {
            continue;
        }

        let p_len = p_words.len();

        // 1. Exact match
        if words == p_words {
            return true;
        }

        // 2. Starts with phrase (e.g. "that's all, thanks")
        if words[..p_len] == p_words {
            let remainder = &words[p_len..];
            let all_allowed = remainder.iter().all(|w| ALLOWED_AFFIX_TOKENS.contains(&w.as_str()));
            if all_allowed {
                return true;
            }
        }

        // 3. Ends with phrase (e.g. "okay bye bye")
        if words[words.len() - p_len..] == p_words {
            let remainder = &words[..words.len() - p_len];
            let all_allowed = remainder.iter().all(|w| ALLOWED_AFFIX_TOKENS.contains(&w.as_str()));
            if all_allowed {
                return true;
            }
        }
    }

    false
}
