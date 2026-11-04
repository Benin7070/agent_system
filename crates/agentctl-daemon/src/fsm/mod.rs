pub mod states;

use self::states::FsmState;
use std::sync::Arc;
use thiserror::Error;
use tokio::sync::RwLock;

#[derive(Error, Debug, PartialEq, Eq)]
pub enum FsmError {
    #[error("Invalid state transition from {from:?} to {to:?}")]
    InvalidTransition { from: FsmState, to: FsmState },
}

#[derive(Debug, Clone)]
pub struct OrchestratorFsm {
    current_state: Arc<RwLock<FsmState>>,
    current_task: Arc<RwLock<Option<String>>>,
}

impl Default for OrchestratorFsm {
    fn default() -> Self {
        Self::new()
    }
}

impl OrchestratorFsm {
    pub fn new() -> Self {
        Self {
            current_state: Arc::new(RwLock::new(FsmState::Idle)),
            current_task: Arc::new(RwLock::new(None)),
        }
    }

    pub async fn get_state(&self) -> FsmState {
        *self.current_state.read().await
    }

    pub async fn get_current_task(&self) -> Option<String> {
        self.current_task.read().await.clone()
    }

    pub async fn set_task(&self, task_id: Option<String>) {
        let mut lock = self.current_task.write().await;
        *lock = task_id;
    }

    /// Attempts to transition the FSM to a new state according to valid state machine rules.
    pub async fn transition_to(&self, target: FsmState) -> Result<FsmState, FsmError> {
        let mut state = self.current_state.write().await;
        let from = *state;

        let valid = match (from, target) {
            // Identity is a no-op
            (a, b) if a == b => true,

            // Idle -> Listening (Activation)
            (FsmState::Idle, FsmState::Listening) => true,

            // Listening -> Processing (Intent submitted)
            (FsmState::Listening, FsmState::Processing) => true,

            // Processing -> AwaitingApproval or Executing
            (FsmState::Processing, FsmState::AwaitingApproval) => true,
            (FsmState::Processing, FsmState::Executing) => true,
            (FsmState::Processing, FsmState::Idle) => true, // direct non-action intent completion

            // AwaitingApproval -> Executing (approved) or Idle (denied)
            (FsmState::AwaitingApproval, FsmState::Executing) => true,
            (FsmState::AwaitingApproval, FsmState::Idle) => true,

            // Executing -> Idle (done or error)
            (FsmState::Executing, FsmState::Idle) => true,

            // Any non-idle state can transition to Cancelling
            (FsmState::Listening, FsmState::Cancelling) => true,
            (FsmState::Processing, FsmState::Cancelling) => true,
            (FsmState::AwaitingApproval, FsmState::Cancelling) => true,
            (FsmState::Executing, FsmState::Cancelling) => true,

            // Cancelling -> Idle (teardown complete)
            (FsmState::Cancelling, FsmState::Idle) => true,

            _ => false,
        };

        if !valid {
            return Err(FsmError::InvalidTransition { from, to: target });
        }

        *state = target;
        if target == FsmState::Idle {
            let mut task_lock = self.current_task.write().await;
            *task_lock = None;
        }

        Ok(target)
    }

    /// Handles a cancel request from any active state.
    /// Returns previous state and whether cancellation transition occurred.
    pub async fn cancel(&self) -> (FsmState, bool) {
        let current = self.get_state().await;
        if current == FsmState::Idle {
            return (current, false);
        }
        if current == FsmState::Cancelling {
            return (current, true);
        }

        let _ = self.transition_to(FsmState::Cancelling).await;
        let _ = self.transition_to(FsmState::Idle).await;
        (current, true)
    }
}
