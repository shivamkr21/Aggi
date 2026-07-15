import logging
import time

access_logger = logging.getLogger("aggi.access")
server_logger = logging.getLogger("aggi.request")


class RequestLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start = time.monotonic()
        response = self.get_response(request)
        duration_ms = round((time.monotonic() - start) * 1000)

        ip = (
            request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
            or request.META.get("REMOTE_ADDR", "-")
        )
        user = request.user.username if request.user.is_authenticated else "-"
        ua = request.META.get("HTTP_USER_AGENT", "-")
        referer = request.META.get("HTTP_REFERER", "-")
        size = response.get("Content-Length", "-")
        status = response.status_code

        # Detailed line → access.log (every request, nothing skipped)
        access_logger.info(
            '%s "%s %s" %s %s "%s" "%s" %dms user=%s',
            ip, request.method, request.path, status, size, referer, ua, duration_ms, user,
        )

        # HEAD requests stop here — noise in server.log, full detail already in access.log
        if request.method == "HEAD":
            return response

        # Simplified line → server.log with level based on status code
        msg = "%s %s %s %dms [%s] user=%s"
        args = (request.method, request.path, status, duration_ms, ip, user)
        if status >= 500:
            server_logger.error(msg, *args)
        elif status >= 400:
            server_logger.warning(msg, *args)
        else:
            server_logger.info(msg, *args)

        return response
