pub mod states;

use self::states::FsmState;
use std::sync::Arc;
use thiserror::Error;
use tokio::sync::{broadcast, RwLock};

#[derive(Error, Debug, PartialEq, Eq)]
pub enum FsmError {
    #[error("Invalid state transition from {from:?} to {to:?}")]
    InvalidTransition { from: FsmState, to: FsmState },
}

#[derive(Debug, Clone)]
pub struct OrchestratorFsm {
    current_state: Arc<RwLock<FsmState>>,
    current_task: Arc<RwLock<Option<String>>>,
    state_tx: broadcast::Sender<FsmState>,
}

impl Default for OrchestratorFsm {
    fn default() -> Self {
        Self::new()
    }
}

impl OrchestratorFsm {
    pub fn new() -> Self {
        let (state_tx, _) = broadcast::channel(64);
        Self {
            current_state: Arc::new(RwLock::new(FsmState::Idle)),
            current_task: Arc::new(RwLock::new(None)),
            state_tx,
        }
    }

    pub fn subscribe_state(&self) -> broadcast::Receiver<FsmState> {
        self.state_tx.subscribe()
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

            // Processing -> AwaitingApproval, Executing, Speaking, or Idle
            (FsmState::Processing, FsmState::AwaitingApproval) => true,
            (FsmState::Processing, FsmState::Executing) => true,
            (FsmState::Processing, FsmState::Speaking) => true,
            (FsmState::Processing, FsmState::Idle) => true,

            // AwaitingApproval -> Executing (approved), Denied (rejected), or Idle
            (FsmState::AwaitingApproval, FsmState::Executing) => true,
            (FsmState::AwaitingApproval, FsmState::Denied) => true,
            (FsmState::AwaitingApproval, FsmState::Idle) => true,

            // Denied -> Idle (after rejection animation)
            (FsmState::Denied, FsmState::Idle) => true,

            // Executing -> Speaking or Idle (done or error)
            (FsmState::Executing, FsmState::Speaking) => true,
            (FsmState::Executing, FsmState::Idle) => true,

            // Speaking -> Idle or Listening
            (FsmState::Speaking, FsmState::Idle) => true,
            (FsmState::Speaking, FsmState::Listening) => true,

            // Any non-idle state can transition to Cancelling
            (FsmState::Listening, FsmState::Cancelling) => true,
            (FsmState::Processing, FsmState::Cancelling) => true,
            (FsmState::AwaitingApproval, FsmState::Cancelling) => true,
            (FsmState::Executing, FsmState::Cancelling) => true,
            (FsmState::Speaking, FsmState::Cancelling) => true,
            (FsmState::Denied, FsmState::Cancelling) => true,

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

        // Notify state subscribers (non-blocking)
        let _ = self.state_tx.send(target);

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
