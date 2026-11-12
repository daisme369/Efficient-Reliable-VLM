import json
import logging
import re


class OperationalLog:
    logger = logging.getLogger("thorax.operational")

    @classmethod
    def configure(cls):
        if not cls.logger.handlers:
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter("%(message)s"))
            cls.logger.addHandler(handler)
        cls.logger.setLevel(logging.INFO)
        cls.logger.propagate = False

    @classmethod
    def write(cls, event, *, request_id=None, route=None, status=None, job_id=None,
              operation=None, latency_ms=None, error_code=None, model_version=None):
        record = {"event": event, "route": route, "status": status, "job_id": job_id,
                  "operation": operation, "latency_ms": latency_ms, "error_code": error_code}
        for name, value in (("request_id", request_id), ("model_version", model_version)):
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,128}", value):
                record[name] = value
        cls.logger.info(json.dumps({key: value for key, value in record.items() if value is not None}))
