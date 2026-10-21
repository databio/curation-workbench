"""The one exception -> HTTP mapping for every hosted route (Pigeon's shape).

Every error body is `{"detail": "<message>", "code": "<snake_case>"}`;
FastAPI's own request-validation 422 keeps its `detail` list.

* snapgate `ServiceError`s (NotFound, Forbidden, Conflict, InsufficientScope,
  and the hosted ones in `service.errors`) keep their status and code.
* `store.StoreError` becomes its status with `code` `invalid` (400),
  `forbidden` (403), `not_found` (404) or `conflict` (409), all messages in
  `errors`, and the current record or vote in `current` on a 409 when the
  store supplies one.
* `HTTPException` (snapgate's auth errors) keeps its status and headers;
  snapgate's `{"error", "message"}` detail becomes `detail` + `code`. Under
  `/auth/` snapgate's own body is kept (its frontend reads it) plus `code`.
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from snapgate import install_service_errors

from ...store import StoreError

STATUS_CODES: dict[int, str] = {
    400: "invalid",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    415: "unsupported_media_type",
    422: "invalid_input",
    428: "precondition_required",
    429: "rate_limited",
    503: "unavailable",
}


def error_body(status: int, detail, code: str | None = None) -> dict:
    return {"detail": detail, "code": code or STATUS_CODES.get(status, f"http_{status}")}


def _store_error(request: Request, exc: StoreError) -> JSONResponse:
    body = error_body(exc.status, exc.errors[0] if exc.errors else str(exc))
    body["errors"] = list(exc.errors)
    if exc.status == 409 and exc.current is not None:
        body["current"] = exc.current
    return JSONResponse(body, status_code=exc.status)


def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    headers = getattr(exc, "headers", None)
    detail, code = exc.detail, None
    if isinstance(detail, dict) and "message" in detail and request.url.path.startswith("/auth/"):
        body = {"detail": detail, "code": detail.get("error") or STATUS_CODES.get(exc.status_code)}
        return JSONResponse(body, status_code=exc.status_code, headers=headers)
    if isinstance(detail, dict) and "message" in detail:
        detail, code = str(detail["message"]), detail.get("error")
    return JSONResponse(error_body(exc.status_code, detail, code), status_code=exc.status_code,
                        headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    install_service_errors(app)
    app.add_exception_handler(StoreError, _store_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)


__all__ = ["STATUS_CODES", "error_body", "install_error_handlers"]
