"""Map every error to the ``{"error": {"code", "message"}}`` envelope."""

import logging

from aichat_protocol import PROTOCOL_HEADER, PROTOCOL_VERSION
from aichat_protocol.errors import HTTP_STATUS, ErrorCode
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from ..domain import DomainError

log = logging.getLogger("aichat.api")


def error_response(code: ErrorCode, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=HTTP_STATUS[code],
        content={"error": {"code": code.value, "message": message}},
    )


async def _domain(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    return error_response(exc.code, exc.message)


async def _validation(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    first = exc.errors()[0] if exc.errors() else {}
    where = ".".join(str(x) for x in first.get("loc", ()))
    return error_response(ErrorCode.INVALID, f"{where}: {first.get('msg', 'invalid request')}")


async def _http(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HTTPException)
    code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.BAD_REQUEST
    return error_response(code, str(exc.detail))


async def _unexpected(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unexpected error", exc_info=exc)
    return error_response(ErrorCode.INTERNAL, "internal error")


def install(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, _domain)
    app.add_exception_handler(RequestValidationError, _validation)
    app.add_exception_handler(HTTPException, _http)
    app.add_exception_handler(Exception, _unexpected)

    @app.middleware("http")
    async def protocol_version(request: Request, call_next):
        version = request.headers.get(PROTOCOL_HEADER)
        if version is not None and version != PROTOCOL_VERSION:
            return error_response(
                ErrorCode.BAD_REQUEST,
                f"protocol {version} not supported; server speaks {PROTOCOL_VERSION}",
            )
        return await call_next(request)
