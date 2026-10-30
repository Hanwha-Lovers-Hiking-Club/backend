"""Node: retain independent decisions and queue the gate's bounded retry work."""

from app.domains.documents.pipeline.execution import settle as settle_state


def settle(state):
    return settle_state(state)
