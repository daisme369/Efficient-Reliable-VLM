"""Initialize or probe the private services without exposing a public readiness route."""
import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

import httpx
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select, text

from demo_seed import DemoSeeder
from postgres_bootstrap import PostgresBootstrap
from thorax.bootstrap import Container
from thorax.config import InferenceSettings, Settings
from thorax.infrastructure.db import Database
from thorax.infrastructure.db.models import Image
from thorax.infrastructure.image_store import ImageStore
from thorax.infrastructure.inference_http import HTTPInferenceProvider


class Deployment:
    def __init__(self, api_root):
        self.alembic_ini = api_root / "alembic.ini"

    def seed(self, config_path, source_path, assets_path):
        settings = Settings.from_environment()
        PostgresBootstrap(os.environ["THORAX_MIGRATION_URL"], settings.database_url, self.alembic_ini).prepare()
        config = tomllib.loads(config_path.read_text(encoding="utf-8"))
        source = json.loads(source_path.read_text(encoding="utf-8"))
        passwords = {key: os.environ[f"THORAX_{key.upper()}"] for key in ("admin_password", "doctor_password")}
        container = Container(settings)
        try:
            DemoSeeder(config).seed(container, source, assets_path, passwords, Path(os.environ["THORAX_MOCK_RESPONSES_PATH"]))
        finally:
            container.close()

    @staticmethod
    def inference_ready():
        HTTPInferenceProvider(InferenceSettings.from_environment()).capabilities()

    def api_ready(self, api_url, *, all_images=False):
        settings = Settings.from_environment()
        timeout = settings.policy["jobs"]["poll_seconds"]
        httpx.get(f"{api_url}/health", timeout=timeout).raise_for_status()
        self.inference_ready()
        database = Database(settings.database_url)
        try:
            with database.sessions() as session:
                revision = session.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                head = ScriptDirectory.from_config(Config(str(self.alembic_ini))).get_current_head()
                if revision != head:
                    raise ValueError("Database migration revision mismatch")
                query = select(Image).order_by(Image.id)
                images = session.scalars(query if all_images else query.limit(1)).all()
                if not images or not settings.fixture_root.is_dir():
                    raise ValueError("Demo data is missing")
                store = ImageStore(settings.image_root, settings.policy["limits"])
                for image in images:
                    store.read(image)
        finally:
            database.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-root", type=Path, required=True)
    commands = parser.add_subparsers(dest="command", required=True)
    seed = commands.add_parser("seed")
    for path in ("config", "source", "assets"):
        seed.add_argument(f"--{path}", type=Path, required=True)
    commands.add_parser("inference-ready")
    api = commands.add_parser("api-ready")
    api.add_argument("--api-url", required=True)
    api.add_argument("--all-images", action="store_true")
    args = parser.parse_args()
    deployment = Deployment(args.api_root)
    try:
        if args.command == "seed":
            deployment.seed(args.config, args.source, args.assets)
        elif args.command == "inference-ready":
            deployment.inference_ready()
        else:
            deployment.api_ready(args.api_url.rstrip("/"), all_images=args.all_images)
    except Exception as error:
        print(json.dumps({"phase": args.command, "status": "failed", "error_type": type(error).__name__}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
