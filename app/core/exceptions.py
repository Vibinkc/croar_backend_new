from typing import Any


class AppException(Exception):
    def __init__(self, message: str, status_code: int = 500, detail: Any | None = None) -> None:
        self.message = message
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


class ValidationException(AppException):
    def __init__(self, message: str, detail: Any | None = None) -> None:
        super().__init__(message, status_code=422, detail=detail)


class NotFoundException(AppException):
    def __init__(self, message: str, detail: Any | None = None) -> None:
        super().__init__(message, status_code=404, detail=detail)


class UnauthorizedException(AppException):
    def __init__(self, message: str, detail: Any | None = None) -> None:
        super().__init__(message, status_code=401, detail=detail)


class ForbiddenException(AppException):
    def __init__(self, message: str, detail: Any | None = None) -> None:
        super().__init__(message, status_code=403, detail=detail)


class DatabaseException(AppException):
    def __init__(self, message: str, detail: Any | None = None) -> None:
        super().__init__(message, status_code=500, detail=detail)
