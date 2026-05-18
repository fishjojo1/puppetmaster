from __future__ import annotations


class PuppetError(Exception):
    def __init__(self, code: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint

    def as_dict(self) -> dict:
        error = {"code": self.code, "message": self.message}
        if self.hint:
            error["hint"] = self.hint
        return {"error": error}


def require(condition: bool, code: str, message: str, hint: str | None = None) -> None:
    if not condition:
        raise PuppetError(code, message, hint)

