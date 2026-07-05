import logging.handlers
import os
import time


class DatestampedRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that renames rotated logs with a datetime stamp
    instead of the default numeric suffix (.1, .2 ...).

    Rotated files are saved as:
        server_2026-07-06_10-23-01.log
    alongside the active server.log in the same directory.
    """

    def rotation_filename(self, default_name):
        base = os.path.splitext(self.baseFilename)[0]
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        return f"{base}_{stamp}.log"
