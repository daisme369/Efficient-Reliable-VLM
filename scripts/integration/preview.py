"""Explicit synthetic preview when a production PostgreSQL host is unavailable."""
import argparse
import os
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))
sys.path.insert(0, str(Path(__file__).parent))

from local_stack import LocalStack
from thorax.bootstrap import Container
from thorax.config import Settings
from thorax.infrastructure.db import Database
from thorax.infrastructure.db.models import Base


class SyntheticPreview(LocalStack):
    def __init__(self, config):
        super().__init__(config)
        if self.config["runtime"]["provider_mode"] != "mock":
            raise ValueError("Synthetic preview does not permit real inference or patient data")
        self.environment["DATABASE_URL"] = "sqlite:///" + (self.directory / "synthetic-preview.sqlite").as_posix()
        self.config_path = config.resolve()

    def container(self):
        os.environ.update(self.environment)
        settings = Settings.from_environment()
        return Container(settings, database=Database(settings.database_url, test_mode=True))

    def prepare_database(self):
        database = Database(self.environment["DATABASE_URL"], test_mode=True)
        try:
            Base.metadata.create_all(database.engine)
        finally:
            database.close()

    def application_process(self, service):
        return [sys.executable, Path(__file__), service, "--config", self.config_path]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "stop", "api", "worker"))
    parser.add_argument("--config", type=Path, default=ROOT / "infra/local.integration.toml")
    args = parser.parse_args()
    preview = SyntheticPreview(args.config)
    if args.command == "run":
        print("Synthetic preview only: SQLite test adapter; PostgreSQL acceptance is pending", flush=True)
        preview.run()
    elif args.command == "stop":
        preview.stop_file.touch()
    elif args.command == "api":
        import uvicorn
        from thorax.presentation.api import create_app
        uvicorn.run(create_app(preview.container()), host=preview.host, port=preview.config["network"]["api_port"], access_log=False)
    else:
        preview.container().worker.run(threading.Event())


if __name__ == "__main__":
    main()
