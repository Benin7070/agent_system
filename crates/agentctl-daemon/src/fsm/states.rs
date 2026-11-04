use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "SCREAMING_SNAKE_CASE")]
pub enum FsmState {
    Idle,
    Listening,
    Processing,
    AwaitingApproval,
    Executing,
    Cancelling,
    Speaking,
    Denied,
}

impl FsmState {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Idle => "IDLE",
            Self::Listening => "LISTENING",
            Self::Processing => "PROCESSING",
            Self::AwaitingApproval => "AWAITING_APPROVAL",
            Self::Executing => "EXECUTING",
            Self::Cancelling => "CANCELLING",
            Self::Speaking => "SPEAKING",
            Self::Denied => "DENIED",
        }
    }

    /// Maps the internal FSM state to the UI wire state name expected by OrbWidget.
    pub fn to_ui_state(&self) -> &'static str {
        match self {
            Self::Idle => "idle",
            Self::Listening => "listening",
            Self::Processing => "thinking",
            Self::AwaitingApproval => "confirm",
            Self::Speaking => "speaking",
            Self::Denied => "denied",
            Self::Executing => "thinking",
            Self::Cancelling => "closing",
        }
    }
}
