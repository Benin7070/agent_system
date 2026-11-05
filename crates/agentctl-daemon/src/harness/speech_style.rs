//! Speech style configuration and deterministic text normaliser for Kokoro TTS.

use regex::Regex;
use std::sync::LazyLock;

/// Constructs the conversational system prompt tailored for Kokoro TTS delivery.
pub fn build_system_prompt(agent_name: &str, user_name: &str) -> String {
    format!(
        "You are {agent_name}, a helpful and warm personal voice assistant speaking to {user_name}.\n\
        Follow these strict vocal delivery rules:\n\
        1. Plain spoken English: write strictly for the ear with short to medium sentences (under 25 words).\n\
        2. Natural rhythm: use contractions (\"it's\", \"you're\", \"can't\") and occasional natural conversational markers (\"Well,\", \"Okay,\").\n\
        3. Punctuation conveys cadence: commas and periods pace the sentence, question marks raise pitch, ellipses (...) or dashes (--) indicate slight pauses. Use exclamation marks very rarely.\n\
        4. Absolute format restrictions: NEVER use markdown (no asterisks, no hashes, no bullet points, no numbered lists), NEVER use emoji, NEVER output code blocks, and NEVER read raw URLs aloud.\n\
        5. Spoken numbers and symbols: write out numbers, dates, times, units, and abbreviations exactly as spoken (e.g. \"twenty five\", \"five dollars\", \"two point five percent\").\n\
        6. Conciseness: be warm, calm, and concise. Keep replies under 3 sentences unless explicitly asked for in-depth explanation.\n\
        7. Complex content: if asked for code, tables, or long lists, speak a brief high-level summary and mention it is available on screen.\n\
        8. Language policy: English only. If the user speaks Tamil, politely reply in English explaining that Tamil speech synthesis is not yet supported."
    )
}

// Pre-compiled regex patterns for deterministic normalization
static RE_EMOJI: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[\u{1F600}-\u{1F64F}\u{1F300}-\u{1F5FF}\u{1F680}-\u{1F6FF}\u{1F700}-\u{1F77F}\u{1F780}-\u{1F7FF}\u{1F800}-\u{1F8FF}\u{1F900}-\u{1F9FF}\u{1FA00}-\u{1FA6F}\u{1FA70}-\u{1FAFF}\u{2600}-\u{26FF}\u{2700}-\u{27BF}]").unwrap()
});

static RE_MD_LINK: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\[([^\]]+)\]\([^\)]+\)").unwrap()
});

static RE_URL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"https?://\S+|www\.\S+").unwrap()
});

static RE_CODE_BLOCK: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"```[\s\S]*?```").unwrap()
});

static RE_INLINE_CODE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"`([^`]+)`").unwrap()
});

static RE_MD_FORMAT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"[*_#~>]").unwrap()
});

static RE_REPEATED_EXCLAMATION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"!{2,}").unwrap()
});

static RE_REPEATED_QUESTION: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\?{2,}").unwrap()
});

static RE_REPEATED_COMMA: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r",{2,}").unwrap()
});

static RE_REPEATED_DOT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\.{4,}").unwrap()
});

static RE_PERCENT: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(\d+(?:\.\d+)?)\s*%").unwrap()
});

static RE_CURRENCY_USD: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\$(\d+(?:\.\d+)?)").unwrap()
});

static RE_CURRENCY_GBP: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"£(\d+(?:\.\d+)?)").unwrap()
});

static RE_CURRENCY_EUR: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"€(\d+(?:\.\d+)?)").unwrap()
});

static RE_TIME: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(\d{1,2}):(\d{2})\b").unwrap()
});

static RE_DECIMAL: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"\b(\d+)\.(\d+)\b").unwrap()
});

/// Converts integers (0-999) into spoken English.
pub fn number_to_words(n: u64) -> String {
    if n == 0 {
        return "zero".to_string();
    }

    const ONES: &[&str] = &[
        "", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
        "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen",
        "seventeen", "eighteen", "nineteen",
    ];
    const TENS: &[&str] = &[
        "", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    ];

    if n < 20 {
        return ONES[n as usize].to_string();
    }
    if n < 100 {
        let t = TENS[(n / 10) as usize];
        let o = ONES[(n % 10) as usize];
        return if o.is_empty() { t.to_string() } else { format!("{}-{}", t, o) };
    }
    if n < 1000 {
        let h = ONES[(n / 100) as usize];
        let rem = n % 100;
        return if rem == 0 {
            format!("{} hundred", h)
        } else {
            format!("{} hundred and {}", h, number_to_words(rem))
        };
    }
    if n < 1_000_000 {
        let th = number_to_words(n / 1000);
        let rem = n % 1000;
        return if rem == 0 {
            format!("{} thousand", th)
        } else {
            format!("{} thousand {}", th, number_to_words(rem))
        };
    }
    n.to_string()
}

/// Expands numbers, percentages, and currencies to spoken English words.
fn expand_numbers_and_symbols(text: &str) -> String {
    // 1. Currency
    let mut s = RE_CURRENCY_USD.replace_all(text, |caps: &regex::Captures| {
        let num = &caps[1];
        if let Ok(val) = num.parse::<u64>() {
            format!("{} dollars", number_to_words(val))
        } else {
            format!("{} dollars", num)
        }
    }).to_string();

    s = RE_CURRENCY_GBP.replace_all(&s, |caps: &regex::Captures| {
        let num = &caps[1];
        if let Ok(val) = num.parse::<u64>() {
            format!("{} pounds", number_to_words(val))
        } else {
            format!("{} pounds", num)
        }
    }).to_string();

    s = RE_CURRENCY_EUR.replace_all(&s, |caps: &regex::Captures| {
        let num = &caps[1];
        if let Ok(val) = num.parse::<u64>() {
            format!("{} euros", number_to_words(val))
        } else {
            format!("{} euros", num)
        }
    }).to_string();

    // 2. Percentages
    s = RE_PERCENT.replace_all(&s, |caps: &regex::Captures| {
        let num = &caps[1];
        if let Ok(val) = num.parse::<u64>() {
            format!("{} percent", number_to_words(val))
        } else {
            format!("{} percent", num)
        }
    }).to_string();

    // 3. Time (e.g. 10:30 -> ten thirty, 5:00 -> five o'clock)
    s = RE_TIME.replace_all(&s, |caps: &regex::Captures| {
        let h = caps[1].parse::<u64>().unwrap_or(0);
        let m = caps[2].parse::<u64>().unwrap_or(0);
        let h_str = number_to_words(h);
        if m == 0 {
            format!("{} o'clock", h_str)
        } else if m < 10 {
            format!("{} oh {}", h_str, number_to_words(m))
        } else {
            format!("{} {}", h_str, number_to_words(m))
        }
    }).to_string();

    // 4. Decimals (e.g. 3.5 -> three point five)
    s = RE_DECIMAL.replace_all(&s, |caps: &regex::Captures| {
        let int_part = caps[1].parse::<u64>().map(number_to_words).unwrap_or_else(|_| caps[1].to_string());
        let dec_part = caps[2].chars().map(|c| {
            if let Some(digit) = c.to_digit(10) {
                number_to_words(digit as u64)
            } else {
                c.to_string()
            }
        }).collect::<Vec<_>>().join(" ");
        format!("{} point {}", int_part, dec_part)
    }).to_string();

    // 5. Standalone integers
    let mut words = Vec::new();
    for token in s.split_whitespace() {
        if let Ok(val) = token.parse::<u64>() {
            words.push(number_to_words(val));
        } else {
            words.push(token.to_string());
        }
    }
    words.join(" ")
}

/// Expands common abbreviations into full spoken English words.
fn expand_abbreviations(text: &str) -> String {
    let mut s = text.to_string();
    let abbrevs = [
        (r"\bDr\.", "Doctor"),
        (r"\bMr\.", "Mister"),
        (r"\bMrs\.", "Missus"),
        (r"\bMs\.", "Miz"),
        (r"\bProf\.", "Professor"),
        (r"\bvs\.", "versus"),
        (r"\be\.g\.,?", "for example"),
        (r"\bi\.e\.,?", "that is"),
        (r"\betc\.", "etcetera"),
        (r"\bapprox\.", "approximately"),
        (r"\bSt\.", "Street"),
        (r"\bRd\.", "Road"),
        (r"\bAve\.", "Avenue"),
        (r"\bBlvd\.", "Boulevard"),
        (r"\bmph\b", "miles per hour"),
        (r"\bkm/h\b", "kilometers per hour"),
        (r"\bkg\b", "kilograms"),
        (r"\blbs\b", "pounds"),
    ];

    for (pat, repl) in abbrevs {
        if let Ok(re) = Regex::new(pat) {
            s = re.replace_all(&s, repl).to_string();
        }
    }
    s
}

/// Cleans raw LLM output into spoken-ready text.
pub fn normalize_for_tts(text: &str) -> String {
    // 1. Remove markdown code blocks & inline code
    let mut s = RE_CODE_BLOCK.replace_all(text, " [code snippet omitted] ").to_string();
    s = RE_INLINE_CODE.replace_all(&s, "$1").to_string();

    // 2. Replace markdown links with link text
    s = RE_MD_LINK.replace_all(&s, "$1").to_string();

    // 3. Strip URLs
    s = RE_URL.replace_all(&s, "link").to_string();

    // 4. Strip markdown formatting characters (*, _, #, ~, >)
    s = RE_MD_FORMAT.replace_all(&s, "").to_string();

    // 5. Strip emojis
    s = RE_EMOJI.replace_all(&s, "").to_string();

    // 6. Expand abbreviations
    s = expand_abbreviations(&s);

    // 7. Expand numbers, percentages, and currencies
    s = expand_numbers_and_symbols(&s);

    // 8. Collapse repeated punctuation
    s = RE_REPEATED_EXCLAMATION.replace_all(&s, "!").to_string();
    s = RE_REPEATED_QUESTION.replace_all(&s, "?").to_string();
    s = RE_REPEATED_COMMA.replace_all(&s, ",").to_string();
    s = RE_REPEATED_DOT.replace_all(&s, "...").to_string();

    // 9. Normalize whitespace
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Splits continuous normalized text into discrete sentence chunks suitable for Kokoro synthesis.
/// Target chunk size: ~200 characters. Never breaks inside abbreviations or numbers.
pub fn split_into_tts_chunks(text: &str, max_chars: usize) -> Vec<String> {
    let normalized = normalize_for_tts(text);
    if normalized.is_empty() {
        return Vec::new();
    }

    let mut chunks = Vec::new();
    let mut current = String::new();

    // Split on terminal punctuation followed by whitespace
    let re_sentence = Regex::new(r"([.!?]+)\s+").unwrap();
    let mut last_idx = 0;

    for mat in re_sentence.find_iter(&normalized) {
        let sentence_part = &normalized[last_idx..mat.start() + mat.as_str().trim_end().len()];
        last_idx = mat.end();

        if current.is_empty() {
            current.push_str(sentence_part);
        } else if current.len() + 1 + sentence_part.len() <= max_chars {
            current.push(' ');
            current.push_str(sentence_part);
        } else {
            chunks.push(current);
            current = sentence_part.to_string();
        }
    }

    // Trailing part
    if last_idx < normalized.len() {
        let trailing = &normalized[last_idx..];
        if current.is_empty() {
            current.push_str(trailing);
        } else if current.len() + 1 + trailing.len() <= max_chars {
            current.push(' ');
            current.push_str(trailing);
        } else {
            chunks.push(current);
            current = trailing.to_string();
        }
    }

    if !current.is_empty() {
        chunks.push(current);
    }

    chunks
}
