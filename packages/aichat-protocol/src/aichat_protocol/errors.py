"""Stable error codes, their HTTP status and their CLI exit code."""

from enum import StrEnum


class ErrorCode(StrEnum):
    BAD_REQUEST = "bad_request"
    INVALID = "invalid"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    DEPTH_EXCEEDED = "depth_exceeded"
    NOT_FOUND = "not_found"
    ALREADY_EXISTS = "already_exists"
    QUESTION_CLOSED = "question_closed"
    ALREADY_ANSWERED = "already_answered"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"
    NOT_ANSWERABLE = "not_answerable"
    SECRET_IN_TEXT = "secret_in_text"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    RATE_LIMITED = "rate_limited"
    TOO_MANY_WAITS = "too_many_waits"
    QUOTA_EXCEEDED = "quota_exceeded"
    INTERNAL = "internal"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.INVALID: 422,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.DEPTH_EXCEEDED: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.ALREADY_EXISTS: 409,
    ErrorCode.QUESTION_CLOSED: 409,
    ErrorCode.ALREADY_ANSWERED: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.NOT_ANSWERABLE: 422,
    ErrorCode.SECRET_IN_TEXT: 422,
    ErrorCode.PAYLOAD_TOO_LARGE: 413,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.TOO_MANY_WAITS: 429,
    ErrorCode.QUOTA_EXCEEDED: 507,
    ErrorCode.INTERNAL: 500,
}


class ExitCode:
    OK = 0
    ERROR = 1
    TIMEOUT = 2
    CLOSED = 3
    NOT_FOUND = 4
    FORBIDDEN = 5
    AUTH = 6


EXIT_CODES: dict[ErrorCode, int] = {
    ErrorCode.QUESTION_CLOSED: ExitCode.CLOSED,
    ErrorCode.ALREADY_ANSWERED: ExitCode.CLOSED,
    ErrorCode.NOT_FOUND: ExitCode.NOT_FOUND,
    ErrorCode.FORBIDDEN: ExitCode.FORBIDDEN,
    ErrorCode.DEPTH_EXCEEDED: ExitCode.FORBIDDEN,
    ErrorCode.UNAUTHORIZED: ExitCode.AUTH,
}


def exit_code_for(code: str) -> int:
    try:
        return EXIT_CODES.get(ErrorCode(code), ExitCode.ERROR)
    except ValueError:
        return ExitCode.ERROR
