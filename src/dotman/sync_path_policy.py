"""Shape-only Sync path policy errors, shared by observation and execution."""


class SyncPathError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
