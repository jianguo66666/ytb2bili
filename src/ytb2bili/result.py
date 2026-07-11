"""统一的结果信封 + 错误类型。所有子命令都返回 Result，CLI 决定人读还是 --json。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class Ytb2biliError(Exception):
    """带有稳定错误码的业务异常，便于 AI/脚本据此分支。"""

    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


@dataclass
class Result:
    ok: bool
    command: str
    data: dict = field(default_factory=dict)
    error: dict | None = None

    def to_dict(self) -> dict:
        out: dict[str, Any] = {"ok": self.ok, "command": self.command}
        out.update(self.data)
        if self.error is not None:
            out["error"] = self.error
        return out


def ok(command: str, **data: Any) -> Result:
    return Result(ok=True, command=command, data=data)


def fail(command: str, code: str, message: str, **details: Any) -> Result:
    return Result(
        ok=False,
        command=command,
        error={"code": code, "message": message, **details},
    )
