import os
import subprocess
import sys

from sqlalchemy.engine import make_url


class PostgresBootstrap:
    def __init__(self, owner_url, runtime_url, alembic_ini):
        self.owner_url = owner_url
        self.owner_dsn = make_url(owner_url).set(drivername="postgresql").render_as_string(hide_password=False)
        runtime = make_url(runtime_url)
        self.role, self.password = runtime.username, runtime.password
        self.alembic_ini = alembic_ini
        if self.role == make_url(owner_url).username or not self.role or not self.password:
            raise ValueError("A separate database runtime role is required")

    def prepare(self):
        import psycopg
        from psycopg import sql

        role = sql.Identifier(self.role)
        with psycopg.connect(self.owner_dsn) as connection:
            existing = connection.execute(
                "SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls FROM pg_roles WHERE rolname=%s",
                (self.role,),
            ).fetchone()
            if existing is None:
                connection.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(role, sql.Literal(self.password)))
            elif existing[0]:
                raise ValueError("The runtime role must not have elevated privileges")
        subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(self.alembic_ini), "upgrade", "head"],
            env=os.environ | {"DATABASE_URL": self.owner_url}, check=True, capture_output=True,
        )
        with psycopg.connect(self.owner_dsn) as connection:
            for statement in (
                "GRANT USAGE ON SCHEMA public TO {}",
                "GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO {}",
                "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO {}",
                "REVOKE UPDATE ON events,images,patients,studies,past_reports FROM {}",
                "REVOKE INSERT,UPDATE ON alembic_version FROM {}",
            ):
                connection.execute(sql.SQL(statement).format(role))
