"""Node: restore only the source scope required by the next retry."""

from app.domains.documents.pipeline.execution import prepare_retry as prepare_state


def prepare_retry(state):
    return prepare_state(state)
