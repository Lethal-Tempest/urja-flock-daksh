from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from urja_api.portal.client import PortalAuthError, PortalError


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str) -> None:
        self.status, self.error, self.message = status, error, message


def not_found(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} not found")


def bad_request(message: str) -> ApiError:
    return ApiError(400, "bad_request", message)


def _body(error: str, message: str, **extra) -> dict:
    return {"error": error, "message": message, **extra}


def install(app: FastAPI) -> None:
    """All errors go out as {"error": <code>, "message": <text>}."""

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError):
        return JSONResponse(_body(exc.error, exc.message), status_code=exc.status)

    @app.exception_handler(PortalAuthError)
    async def _portal_auth(_: Request, exc: PortalAuthError):
        return JSONResponse(_body("upstream_auth_failed", str(exc)), status_code=502)

    @app.exception_handler(PortalError)
    async def _portal(_: Request, exc: PortalError):
        return JSONResponse(_body("upstream_unavailable", str(exc)), status_code=502)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError):
        details = [{"field": ".".join(str(p) for p in e["loc"][1:]), "problem": e["msg"]} for e in exc.errors()]
        return JSONResponse(_body("invalid_request", "Request parameters are invalid", details=details), status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "http_error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)
