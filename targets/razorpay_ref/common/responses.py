"""The target's response vocabulary (plan C.1).

Bodies are observations, never verdicts. Keeping the constructors in one place
makes it hard for a handler to invent a new shape.
"""

from __future__ import annotations

from fastapi.responses import JSONResponse


def processed() -> JSONResponse:
    return JSONResponse(status_code=200, content={"status": "processed"})


def duplicate(scope: str) -> JSONResponse:
    assert scope in {"event", "payment", "order"}
    return JSONResponse(status_code=200, content={"status": "duplicate", "scope": scope})


def rejected(status_code: int, reason: str) -> JSONResponse:
    assert 400 <= status_code < 500
    return JSONResponse(status_code=status_code, content={"status": "rejected", "reason": reason})


def error(status_code: int, reason: str) -> JSONResponse:
    assert 500 <= status_code < 600
    return JSONResponse(status_code=status_code, content={"status": "error", "reason": reason})
