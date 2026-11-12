import argparse
import getpass
import json
from pathlib import Path

from sqlalchemy import select

from thorax.bootstrap import Container
from thorax.config import Settings
from thorax.infrastructure.db.models import User


class AdministrationCLI:
    def run(self, argv=None):
        parser = argparse.ArgumentParser(description="Controlled local setup; no model download or training")
        commands = parser.add_subparsers(dest="command", required=True)
        admin = commands.add_parser("create-admin")
        admin.add_argument("--email", required=True)
        admin.add_argument("--name", required=True)
        image = commands.add_parser("prepare-image")
        image.add_argument("source", type=Path)
        fixture = commands.add_parser("import-fixture")
        fixture.add_argument("--admin-email", required=True)
        fixture.add_argument("--fixture-key", required=True)
        args = parser.parse_args(argv)
        container = Container(Settings.from_environment())
        try:
            if args.command == "create-admin":
                first = getpass.getpass("Admin password: ")
                if first != getpass.getpass("Confirm password: "):
                    raise ValueError("Passwords differ")
                email, name = args.email.strip().casefold(), args.name.strip()
                limits = container.settings.policy["limits"]
                if not name or len(name) > limits["name_chars"] or len(email) > limits["email_chars"] or "@" not in email:
                    raise ValueError("Invalid account fields")
                with container.database.unit_of_work() as uow:
                    if uow.session.scalar(select(User).where(User.role == "admin")):
                        raise ValueError("First admin already exists")
                    user = uow.repository.add(User(name=name, email=email, password_hash=container.auth.hash_password(first), role="admin"))
                    uow.repository.event(user.id, None, "admin_bootstrapped")
                print("Admin created")
            elif args.command == "prepare-image":
                metadata = container.images.prepare(args.source, container.settings.policy["inference"]["preprocessing_version"])
                print(json.dumps(metadata))
            else:
                import secrets
                with container.database.unit_of_work() as uow:
                    user = uow.session.scalar(select(User).where(User.email == args.admin_email.strip().casefold(), User.role == "admin", User.is_active.is_(True)))
                    if not user:
                        raise ValueError("Active admin required")
                    actor = user.id
                container.admin.import_fixture(actor, args.fixture_key, secrets.token_urlsafe())
                print("Fixture imported")
        finally:
            container.close()


def main():
    AdministrationCLI().run()


if __name__ == "__main__":
    main()
