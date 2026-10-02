INTERRUPTED_RESPONSE_ERROR = "LLM returned neither content nor tool calls"


class BrainError(Exception):
    """Expected request, state, configuration, or upstream error."""

class FileToolError(BrainError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class TurnCancelled(Exception):
    """Internal signal raised when a generation is stopped or refreshed."""

    def __init__(self, reason: str = "stop"):
        super().__init__(reason)
        self.reason = reason
