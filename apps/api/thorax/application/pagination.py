import base64
import hashlib
import hmac
import json

from sqlalchemy import tuple_

from thorax.domain.errors import DomainError


class Pagination:
    def __init__(self, secret: str, limits: dict):
        self.secret, self.limits = secret.encode(), limits

    def page(self, repo, query, scope: str, cursor: str | None, limit: int | None, *, columns, message=False):
        size = self.limits["page_size"] if limit is None else limit
        maximum = self.limits["message_page_max" if message else "page_max"]
        if not 1 <= size <= maximum:
            raise DomainError("INVALID_PAGE_SIZE", 422)
        if cursor:
            try:
                raw, signature = cursor.rsplit(".", 1)
                expected = hmac.new(self.secret, raw.encode(), hashlib.sha256).hexdigest()
                if not hmac.compare_digest(signature, expected):
                    raise ValueError()
                data = json.loads(base64.urlsafe_b64decode(raw))
                if data["scope"] != scope or len(data["values"]) != len(columns):
                    raise ValueError()
                values = [column.type.python_type(value) if column.type.python_type in (int, str) else column.type.python_type.fromisoformat(value) for column, value in zip(columns, data["values"])]
                query = query.where(tuple_(*columns) < tuple_(*values))
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                raise DomainError("INVALID_CURSOR", 422) from error
        rows = list(repo.session.scalars(query.order_by(*(c.desc() for c in columns)).limit(size + 1)))
        next_cursor = None
        if len(rows) > size:
            rows = rows[:size]
            data = {"scope": scope, "values": [getattr(rows[-1], c.key) for c in columns]}
            raw = base64.urlsafe_b64encode(json.dumps(data, default=str).encode()).decode()
            next_cursor = raw + "." + hmac.new(self.secret, raw.encode(), hashlib.sha256).hexdigest()
        return rows, next_cursor
