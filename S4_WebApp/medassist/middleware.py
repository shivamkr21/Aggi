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
        ip = (
            request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR", "-")
        )
        user = request.user.username if request.user.is_authenticated else "-"
        logger.info(
            "%s %s %s %dms [%s] user=%s",
            request.method,
            request.path,
            response.status_code,
            duration_ms,
            ip,
            user,
        )
        return response
