"""Error responses in the one shape used everywhere: ``{"error": {"code", "message", ...}}``."""

from collections.abc import Mapping
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

# Below 500 the framework raises HTTPException for routing (404, 405) and unparsable bodies
# (400); every other client error maps to INVALID_PARAMETERS.
_HTTP_CODES = {404: "NOT_FOUND", 405: "ACTION_NOT_ALLOWED"}


class ApiError(Exception):
    """Raised by routes and dependencies to answer with ``status`` and an error ``code``."""

    def __init__(
        self, status: int, code: str, message: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def error_response(
    status: int,
    code: str,
    message: str,
    details: dict[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    """Build the JSON error response; ``details`` is omitted when ``None``."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return JSONResponse({"error": error}, status_code=status, headers=headers)


def add_error_handlers(app: FastAPI, *, validation_details: bool) -> None:
    """Render every error in the error shape; field details only with ``validation_details``."""

    @app.exception_handler(ApiError)
    async def api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code >= 500:
            code = "UPSTREAM_UNAVAILABLE"
        else:
            code = _HTTP_CODES.get(exc.status_code, "INVALID_PARAMETERS")
        return error_response(exc.status_code, code, str(exc.detail), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = {"errors": jsonable_encoder(exc.errors())} if validation_details else None
        return error_response(400, "INVALID_PARAMETERS", "Invalid parameters", details)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, exc: Exception) -> JSONResponse:
        # Runs in ServerErrorMiddleware, which re-raises exc so the server still logs it.
        return error_response(500, "UPSTREAM_UNAVAILABLE", "Internal error")
