"""Stable errors shared across runtime boundaries."""


class PluginStoreError(RuntimeError):
    """A plugin repository operation failed."""


class ControlRequestError(ValueError):
    """A control-socket request failed boundary validation."""

    def __init__(self, message: str, code: str = "bad_request") -> None:
        super().__init__(message)
        self.code = code
