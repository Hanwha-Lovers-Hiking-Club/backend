"""Node: keep new candidate experiences after scoped extraction retries."""

from app.domains.documents.pipeline.execution import select_candidates as select_state


def select_candidates(state):
    return select_state(state)
