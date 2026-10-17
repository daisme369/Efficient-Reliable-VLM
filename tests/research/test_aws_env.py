from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from cxr_research.utils.aws_env import load_aws_environment, main
from cxr_research.utils.errors import ContractError


class AwsEnvTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env_file = Path(self.directory.name) / ".env"

    def write_env(self, text):
        self.env_file.write_text(text, encoding="utf-8-sig")

    def test_aliases_quotes_comments_and_unrelated_keys(self):
        self.write_env(
            "# synthetic only\nexport aws_access_key_id = 'fake-key' # comment\n"
            'aws_secret_access_key="fake+/secret==#part"\n'
            "aws_session_token=fake-token== # comment\nAWS_REGION=example-region\n"
            "physio_pass=must-not-import\nmimic-cxr-jpg=private-source\n"
        )
        result = load_aws_environment(self.env_file, base_env={"PATH": "existing-path"})
        self.assertEqual(result, {
            "PATH": "existing-path", "AWS_ACCESS_KEY_ID": "fake-key",
            "AWS_SECRET_ACCESS_KEY": "fake+/secret==#part", "AWS_SESSION_TOKEN": "fake-token==",
            "AWS_REGION": "example-region",
        })

    def test_existing_credentials_win_as_group_without_file_token(self):
        self.write_env("aws_access_key_id=file-key\naws_secret_access_key=file-secret\naws_session_token=file-token\n")
        base = {"aws_access_key_id": "existing-key", "AWS_SECRET_ACCESS_KEY": "existing-secret"}
        original = dict(base)
        result = load_aws_environment(self.env_file, base_env=base)
        self.assertEqual(result["AWS_ACCESS_KEY_ID"], "existing-key")
        self.assertEqual(result["AWS_SECRET_ACCESS_KEY"], "existing-secret")
        self.assertNotIn("AWS_SESSION_TOKEN", result)
        self.assertEqual(base, original)

    def test_existing_profile_suppresses_file_credentials(self):
        self.write_env("aws_access_key_id=file-key\naws_secret_access_key=file-secret\naws_profile=file-profile\n")
        result = load_aws_environment(self.env_file, base_env={"AWS_PROFILE": "container-profile"})
        self.assertEqual(result["AWS_PROFILE"], "container-profile")
        self.assertNotIn("AWS_ACCESS_KEY_ID", result)

    def test_partial_credentials_are_never_mixed(self):
        self.write_env("aws_access_key_id=file-key\naws_secret_access_key=file-secret\n")
        with self.assertRaises(ContractError):
            load_aws_environment(self.env_file, base_env={"AWS_ACCESS_KEY_ID": "existing-key"})
        for text in ("aws_access_key_id=file-key", "aws_session_token=file-token"):
            self.write_env(text)
            with self.assertRaises(ContractError):
                load_aws_environment(self.env_file, base_env={})

    def test_parse_errors_do_not_include_values(self):
        for text in (
            'aws_profile="sensitive-unclosed',
            'aws_profile="sensitive"invalid',
            "aws_profile=sensitive\nAWS_PROFILE=duplicate",
            "aws_profile=", "aws_profile=sensitive\x00value", "AWS_PROFILE",
        ):
            with self.subTest(text_index=len(text)):
                self.write_env(text)
                with self.assertRaises(ContractError) as caught:
                    load_aws_environment(self.env_file, base_env={})
                self.assertNotIn("sensitive", str(caught.exception))

    def test_shell_expressions_are_literal_and_environment_is_unchanged(self):
        self.write_env("aws_profile='$(do-not-run);${HOME}`literal`'\n")
        original = dict(os.environ)
        result = load_aws_environment(self.env_file, base_env={})
        self.assertEqual(result["AWS_PROFILE"], "$(do-not-run);${HOME}`literal`")
        self.assertEqual(dict(os.environ), original)

    def test_missing_file_error_does_not_include_path(self):
        with self.assertRaises(ContractError) as caught:
            load_aws_environment(self.env_file, base_env={})
        self.assertNotIn(str(self.env_file), str(caught.exception))

    def test_check_only_prints_presence_and_does_not_call_aws(self):
        self.write_env("aws_access_key_id=fake-key\naws_secret_access_key=fake-secret\n")
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output), patch("subprocess.run") as run:
            self.assertEqual(main(["--env-file", str(self.env_file), "--check"]), 0)
            run.assert_not_called()
        self.assertIn("AWS_ACCESS_KEY_ID: set", output.getvalue())
        self.assertNotIn("fake-key", output.getvalue())
        self.assertNotIn("fake-secret", output.getvalue())

    def test_cli_passes_credentials_only_in_environment_and_preserves_exit_code(self):
        self.write_env("aws_access_key_id=fake-key\naws_secret_access_key=fake-secret\n")
        with patch.dict(os.environ, {}, clear=True), patch("shutil.which", return_value="/bin/aws"), patch("subprocess.run") as run:
            run.return_value.returncode = 7
            self.assertEqual(main(["--env-file", str(self.env_file), "--", "s3", "ls"]), 7)
            self.assertEqual(run.call_args.args[0], ["/bin/aws", "s3", "ls"])
            self.assertFalse(run.call_args.kwargs["shell"])
            self.assertEqual(run.call_args.kwargs["env"]["AWS_SECRET_ACCESS_KEY"], "fake-secret")

    def test_launch_errors_are_sanitized(self):
        self.write_env("aws_profile=fake-profile\n")
        output = io.StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stderr(output), patch("shutil.which", return_value="/bin/aws"), patch("subprocess.run", side_effect=OSError("sensitive-details")):
            self.assertEqual(main(["--env-file", str(self.env_file), "--", "--version"]), 2)
        self.assertNotIn("sensitive-details", output.getvalue())

    def test_real_child_receives_environment_without_aws_or_network(self):
        self.write_env("aws_access_key_id=fake-key\naws_secret_access_key=fake-secret\n")
        environment = load_aws_environment(self.env_file, base_env={
            key: value for key, value in os.environ.items() if not key.upper().startswith("AWS_")
        })
        result = subprocess.run([
            sys.executable, "-c",
            "import os; assert os.environ['AWS_ACCESS_KEY_ID'] == 'fake-key'; "
            "assert os.environ['AWS_SECRET_ACCESS_KEY'] == 'fake-secret'; print('child-ok')",
        ], env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "child-ok")


if __name__ == "__main__":
    unittest.main()
