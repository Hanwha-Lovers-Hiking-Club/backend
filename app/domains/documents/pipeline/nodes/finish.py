"""Node: expose all final verification outcomes without embedding or saving."""

from app.domains.documents.pipeline.execution import finish as finish_state


def finish(state):
    return finish_state(state)
