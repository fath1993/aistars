"""
The GitHub deploy key's forced command must refuse anything but a commit ID.

Only the refusals are tested: a valid ID hands over to dp.sh on the server,
which needs root, Docker and the real checkout.
"""
import os
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

SCRIPT = Path(settings.BASE_DIR) / "deploy" / "ci-deploy.sh"


def run(command):
    env = dict(os.environ)
    env.pop("SSH_ORIGINAL_COMMAND", None)
    if command is not None:
        env["SSH_ORIGINAL_COMMAND"] = command
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=10)


class CiDeployRefusalTests(SimpleTestCase):
    def assert_refused(self, command):
        result = run(command)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("refused", result.stderr)

    def test_no_command_is_refused(self):
        self.assert_refused(None)

    def test_an_empty_command_is_refused(self):
        self.assert_refused("")

    def test_a_shell_command_is_refused(self):
        self.assert_refused("rm -rf /")

    def test_a_commit_id_with_extra_words_is_refused(self):
        self.assert_refused("a" * 40 + "; reboot")

    def test_a_short_commit_id_is_refused(self):
        self.assert_refused("abc1234")

    def test_uppercase_hex_is_refused(self):
        self.assert_refused("A" * 40)

    def test_a_branch_name_is_refused(self):
        self.assert_refused("main")


DP_SH = Path(settings.BASE_DIR) / "dp.sh"


class DeployScriptTests(SimpleTestCase):
    def test_compose_runs_in_the_project_folder(self):
        # GitHub Actions runs dp.sh from root's home, so every docker compose
        # call after the git step needs the project folder first. Without it
        # the migration wait polled a folder with no compose file for 90s.
        text = DP_SH.read_text()
        after_git = text[text.index("\nEOF\n"):]
        code = [line for line in after_git.splitlines() if not line.lstrip().startswith("#")]
        first_cd = code.index('cd "$APP_DIR"')
        first_compose = next(i for i, line in enumerate(code) if "docker compose" in line)
        self.assertLess(first_cd, first_compose)
