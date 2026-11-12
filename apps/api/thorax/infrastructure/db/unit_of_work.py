from sqlalchemy import create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from .repositories import Repository


class Database:
    def __init__(self, url: str, *, test_mode: bool = False):
        dialect = make_url(url).get_backend_name()
        if dialect != "postgresql" and not test_mode:
            raise ValueError("The application requires PostgreSQL; SQLite is only a unit-test adapter")
        options = {}
        if dialect == "sqlite":
            options = {"connect_args": {"check_same_thread": False}}
            if make_url(url).database in (None, "", ":memory:"):
                options["poolclass"] = StaticPool
        self.serialized_preview = dialect == "sqlite" and "poolclass" not in options
        self.engine = create_engine(url, pool_pre_ping=True, **options)
        if dialect == "sqlite":
            event.listen(self.engine, "connect", self._sqlite_foreign_keys)
            if self.serialized_preview:
                event.listen(self.engine, "begin", self._sqlite_begin)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def _sqlite_foreign_keys(self, connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
        if self.serialized_preview:
            connection.isolation_level = None

    @staticmethod
    def _sqlite_begin(connection):
        # SQLite has no row locks; serialize the synthetic preview's aggregates.
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    def unit_of_work(self):
        return UnitOfWork(self.sessions)

    def close(self):
        self.engine.dispose()


class UnitOfWork:
    def __init__(self, sessions):
        self.sessions = sessions

    def __enter__(self):
        self.session = self.sessions()
        self.repository = Repository(self.session)
        return self

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc_type is None:
                self.session.commit()
            else:
                self.session.rollback()
        finally:
            self.session.close()
