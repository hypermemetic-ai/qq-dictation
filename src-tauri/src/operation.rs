use std::fmt;

/// Target supplied when a local transcription control starts a recording.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum StartTarget {
    /// Preserve legacy start-time focus capture and focused-input behavior.
    Auto,
    /// Deliver only to this exact public Herdr pane id.
    ExplicitPane(String),
}

/// Identity of the source that owns one dictation operation.
///
/// Ownership is explicit and carried through capture, processing, cancellation,
/// and completion. A binding name or the most recently active socket is never
/// used as a proxy for authority.
#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum OperationOwner {
    Local { binding_id: String },
    Remote { request_id: String },
}

impl OperationOwner {
    pub(crate) fn local(binding_id: impl Into<String>) -> Self {
        Self::Local {
            binding_id: binding_id.into(),
        }
    }

    pub(crate) fn remote(request_id: impl Into<String>) -> Self {
        Self::Remote {
            request_id: request_id.into(),
        }
    }

    pub(crate) fn is_local(&self) -> bool {
        matches!(self, Self::Local { .. })
    }

    pub(crate) fn remote_request_id(&self) -> Option<&str> {
        match self {
            Self::Remote { request_id } => Some(request_id),
            Self::Local { .. } => None,
        }
    }
}

impl fmt::Display for OperationOwner {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Local { binding_id } => write!(formatter, "local:{binding_id}"),
            Self::Remote { request_id } => write!(formatter, "remote:{request_id}"),
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum OperationOutcome {
    Succeeded,
    Failed,
    Cancelled,
}
