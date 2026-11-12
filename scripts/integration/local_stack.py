"""Run an isolated local stack with PostgreSQL and the actual Python backend."""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "apps/api"))

from thorax.bootstrap import Container
from thorax.config import Settings
from demo_seed import DemoSeeder
from postgres_bootstrap import PostgresBootstrap


class LocalStack:
    def __init__(self, config: Path):
        self.config = tomllib.loads(config.read_text(encoding="utf-8"))
        runtime, network = self.config["runtime"], self.config["network"]
        self.policy = tomllib.loads((ROOT / runtime["policy_file"]).read_text(encoding="utf-8"))
        self.directory = (ROOT / runtime["directory"]).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.pg_bin = (ROOT / runtime["postgres_bin"]).resolve()
        self.web = (ROOT / runtime["web_directory"]).resolve()
        self.host = network["host"]
        if self.host != "127.0.0.1":
            raise ValueError("This disposable local stack binds only to loopback")
        self.processes, self.logs = [], []
        self.owned_database = False
        self.stop_file = self.directory / "stop-requested"
        secret_file = self.directory / "access.json"
        if not secret_file.exists():
            secret_file.write_text(json.dumps({name: secrets.token_urlsafe(self.policy["security"]["token_bytes"]) for name in ("owner_password", "runtime_password", "session_secret", "idempotency_secret", "inference_token", "admin_password", "doctor_password")}, indent=2), encoding="utf-8")
        self.secrets = json.loads(secret_file.read_text(encoding="utf-8"))
        database = self.config["database"]
        self.environment = os.environ | {
            "DATABASE_URL": self.database_url(database["runtime_role"], self.secrets["runtime_password"]),
            "SESSION_SECRET": self.secrets["session_secret"],
            "IDEMPOTENCY_HASH_SECRET": self.secrets["idempotency_secret"],
            "INFERENCE_SERVICE_TOKEN": self.secrets["inference_token"],
            "APP_ORIGIN": f"http://{self.host}:{network['web_port']}",
            "ALLOW_INSECURE_LOOPBACK_COOKIE": "true",
            "THORAX_POLICY_PATH": str(ROOT / runtime["policy_file"]),
            "IMAGE_STORAGE_ROOT": str(self.directory / "images"),
            "FIXTURE_ROOT": str(self.directory / "fixtures"),
            "INFERENCE_BASE_URL": f"http://{self.host}:{network['inference_port']}",
            "AI_PROVIDER_MODE": runtime["provider_mode"],
            "THORAX_MOCK_RESPONSES_PATH": str(self.directory / "mock-responses.json"),
            "PYTHONPATH": os.pathsep.join((str(ROOT / "apps/api"), str(ROOT / "src"))),
            "NEXT_TELEMETRY_DISABLED": "1",
            "THORAX_API_ORIGIN": f"http://{self.host}:{network['api_port']}",
        }

    def database_url(self, user, password, database=None):
        network = self.config["network"]
        return f"postgresql+psycopg://{user}:{password}@{network['host']}:{network['database_port']}/{database or self.config['database']['name']}"

    @staticmethod
    def execute(arguments, **kwargs):
        return subprocess.run([str(a) for a in arguments], check=True, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, **kwargs)

    def prepare_database(self):
        import psycopg
        from psycopg import sql
        data = self.directory / "postgres-data"
        password_file = self.directory / "postgres-password"
        password_file.write_text(self.secrets["owner_password"], encoding="utf-8")
        try:
            if not (data / "PG_VERSION").exists():
                self.execute([self.pg_bin / "initdb.exe", "-D", data, "-U", self.config["database"]["owner"], "--auth=scram-sha-256", "--pwfile", password_file, "--encoding=UTF8", "--locale=C"])
        finally:
            password_file.unlink(missing_ok=True)
        status = subprocess.run([str(self.pg_bin / "pg_ctl.exe"), "-D", str(data), "status"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        if status.returncode:
            self.execute([self.pg_bin / "pg_ctl.exe", "-D", data, "-o", f"-h {self.host} -p {self.config['network']['database_port']}", "-l", self.directory / "postgres.log", "-w", "start"])
            self.owned_database = True
        database = self.config["database"]
        admin_url = self.database_url(database["owner"], self.secrets["owner_password"], "postgres").replace("postgresql+psycopg:", "postgresql:")
        with psycopg.connect(admin_url, autocommit=True) as connection:
            if not connection.execute("SELECT 1 FROM pg_database WHERE datname=%s", (database["name"],)).fetchone():
                connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database["name"])))
        owner_url = self.database_url(database["owner"], self.secrets["owner_password"])
        PostgresBootstrap(owner_url, self.environment["DATABASE_URL"], ROOT / "apps/api/alembic.ini").prepare()

    def prepare_fixtures(self):
        os.environ.update(self.environment)
        self.environment["THORAX_API_ORIGIN"] = f"http://{self.host}:{self.config['network']['api_port']}"
        (self.web / ".env.local").write_text(f"THORAX_API_ORIGIN={self.environment['THORAX_API_ORIGIN']}\nNEXT_TELEMETRY_DISABLED=1\n", encoding="utf-8")
        exported = self.execute([shutil.which(self.config["runtime"]["node"]), ROOT / "scripts/integration/export_demo.mjs"], cwd=self.web)
        container = self.container()
        try:
            DemoSeeder(self.config).seed(container, json.loads(exported.stdout), self.web / "lib/server/assets", self.secrets, Path(self.environment["THORAX_MOCK_RESPONSES_PATH"]))
        finally:
            container.close()

    def start_process(self, name, arguments, cwd):
        log = (self.directory / f"{name}.log").open("ab")
        self.logs.append(log)
        self.processes.append(subprocess.Popen([str(a) for a in arguments], cwd=cwd, env=self.environment, stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))

    def container(self):
        return Container(Settings.from_environment())

    def application_process(self, service):
        network = self.config["network"]
        if service == "worker":
            return [sys.executable, "-m", "thorax.worker.main"]
        return [sys.executable, "-m", "uvicorn", "thorax.presentation.api:create_app", "--factory", "--host", self.host, "--port", network["api_port"], "--no-access-log"]

    def run(self):
        self.stop_file.unlink(missing_ok=True)
        try:
            self.prepare_database()
            self.prepare_fixtures()
            network = self.config["network"]
            self.start_process("inference", [sys.executable, "-m", "uvicorn", "thorax.inference.api:create_app", "--factory", "--host", self.host, "--port", network["inference_port"], "--no-access-log"], ROOT / "apps/api")
            deadline = time.monotonic() + self.config["runtime"]["startup_seconds"]
            while time.monotonic() < deadline:
                try:
                    import urllib.request
                    request = urllib.request.Request(self.environment["INFERENCE_BASE_URL"] + "/internal/v1/capabilities", headers={"Authorization": f"Bearer {self.secrets['inference_token']}"})
                    with urlopen(request, timeout=self.policy["jobs"]["poll_seconds"]) as response:
                        if response.status == 200 and json.load(response).get("ready") is True:
                            break
                except OSError:
                    time.sleep(self.policy["jobs"]["poll_seconds"])
            else:
                raise RuntimeError("Inference did not become ready; inspect local inference.log")
            self.start_process("api", self.application_process("api"), ROOT / "apps/api")
            self.start_process("worker", self.application_process("worker"), ROOT / "apps/api")
            self.start_process("web", [shutil.which(self.config["runtime"]["node"]), self.web / "node_modules/next/dist/bin/next", self.config["runtime"]["frontend_command"], "--hostname", self.host, "--port", network["web_port"]], self.web)
            print(json.dumps({"phase": "started", "url": self.environment["APP_ORIGIN"], "credentials_file": str(self.directory / "access.json")}), flush=True)
            while not self.stop_file.exists():
                if any(process.poll() is not None for process in self.processes):
                    raise RuntimeError("A stack process exited; inspect local runtime logs")
                time.sleep(self.policy["jobs"]["poll_seconds"])
        finally:
            for process in reversed(self.processes):
                process.terminate()
                process.wait(timeout=self.config["runtime"]["shutdown_seconds"])
            for log in self.logs:
                log.close()
            if self.owned_database:
                self.execute([self.pg_bin / "pg_ctl.exe", "-D", self.directory / "postgres-data", "-m", "fast", "-w", "stop"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "stop"))
    parser.add_argument("--config", type=Path, default=ROOT / "infra/local.integration.toml")
    args = parser.parse_args()
    stack = LocalStack(args.config)
    if args.command == "stop":
        stack.stop_file.touch()
    else:
        stack.run()


if __name__ == "__main__":
    main()
