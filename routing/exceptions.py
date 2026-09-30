import logging
from typing import Any

from rest_framework.exceptions import ParseError
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)


def api_exception_handler(exc: Exception, context: dict[str, Any]) -> Response | None:
    response = exception_handler(exc, context)
    if response is None:
        logger.exception("Unhandled API exception", exc_info=exc)
        return Response({"error": "Unexpected server error."}, status=500)

    if isinstance(exc, ParseError):
        message = "Malformed request."
    elif isinstance(response.data, dict):
        first_value = next(iter(response.data.values()), "Request validation failed.")
        message = str(first_value[0] if isinstance(first_value, list) and first_value else first_value)
    elif isinstance(response.data, list) and response.data:
        message = str(response.data[0])
    else:
        message = "Request validation failed."
    response.data = {"error": message}
    return response