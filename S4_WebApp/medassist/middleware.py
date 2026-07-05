import logging
import time

logger = logging.getLogger("aggi.request")


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)

        # Skip HEAD requests — used by health-check/preview tools, not real users
        if request.method == "HEAD":
            return response

        duration_ms = round((time.monotonic() - start) * 1000)
        logger.info(
            "%s %s %s %dms",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
        )
        return response
