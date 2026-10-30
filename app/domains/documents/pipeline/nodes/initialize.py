"""Node: hydrate graph inputs and start fresh per-job execution bookkeeping."""

from app.domains.documents.pipeline.execution import initialize as initialize_state


def initialize(state):
    return initialize_state(state)
