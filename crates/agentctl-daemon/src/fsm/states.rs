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
        }
    }
}
