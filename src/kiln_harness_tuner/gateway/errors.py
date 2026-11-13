"""The gateway's error type, in a module of its own so that the pure
modules (`model_info`) and `ops` can both raise it without an import
cycle."""


class GatewayError(RuntimeError):
    """A refused or failed operation; the message is the agent's answer."""
