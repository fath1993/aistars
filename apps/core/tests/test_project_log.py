"""
Repo consistency: the version, the log, and each other.

Ported from AVideo, where three log entries were silently lost while the
version kept climbing. A convention nobody checks is a convention that drifts.
"""
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from django.utils import timezone

LOG = Path(settings.BASE_DIR) / "PROJECT_LOG.md"
ENV = Path(settings.BASE_DIR) / "aistars" / ".env"
ENV_EXAMPLE = Path(settings.BASE_DIR) / "aistars" / ".env.example"

ENTRY = re.compile(r"^### (\d{4}-\d{2}-\d{2}) — v([\d.]+) — (.+)$", re.M)


def entries():
    """(date, version, title) newest first."""
    return ENTRY.findall(LOG.read_text())


def app_version(path):
    match = re.search(r"^APP_VERSION=(.+)$", path.read_text(), re.M)
    return match.group(1).strip() if match else None


def as_tuple(version):
    return tuple(int(part) for part in version.split("."))


class ProjectLogTests(SimpleTestCase):
    def test_the_log_exists_and_has_entries(self):
        self.assertTrue(LOG.is_file())
        self.assertGreater(len(entries()), 0)

    def test_the_rules_are_at_the_top(self):
        text = LOG.read_text()
        rules = text.find("## Rules (read before every change)")
        changelog = text.find("## Changelog")
        self.assertNotEqual(rules, -1, "the Rules section is missing")
        self.assertNotEqual(changelog, -1, "the Changelog section is missing")
        self.assertLess(rules, changelog)
        self.assertLess(changelog, ENTRY.search(text).start(),
                        "entries must sit under ## Changelog")

    def test_running_version_matches_the_newest_entry(self):
        # settings.APP_VERSION comes from .env locally and from env_file in
        # Docker, so this holds wherever the suite runs.
        newest = entries()[0][1]
        self.assertEqual(
            settings.APP_VERSION, newest,
            f"APP_VERSION is {settings.APP_VERSION} but the newest log entry "
            f"is v{newest}. Either the change was not logged or the version "
            f"was not bumped.")

    def test_the_env_file_matches_when_present(self):
        if not ENV.is_file():
            self.skipTest("no .env on disk (inside Docker it comes from env_file)")
        self.assertEqual(app_version(ENV), entries()[0][1])

    def test_the_env_example_tracks_the_newest_entry(self):
        self.assertEqual(app_version(ENV_EXAMPLE), entries()[0][1])

    def test_versions_descend_without_repeats(self):
        versions = [as_tuple(v) for _, v, _ in entries()]
        for newer, older in zip(versions, versions[1:]):
            self.assertGreater(newer, older, f"v{newer} is listed above v{older}")

    def test_titles_are_commit_subjects(self):
        for _, version, title in entries():
            self.assertTrue(title.strip(), f"v{version} has an empty title")
            self.assertLess(len(title), 72, f"v{version} title is too long")
            self.assertFalse(title.endswith("."), f"v{version} title ends in a period")

    def test_dates_are_not_in_the_future(self):
        # Local date (Europe/Bucharest), not the container's UTC date: an
        # entry written just after midnight here is still "today".
        today = timezone.localdate().isoformat()
        for entry_date, version, _ in entries():
            self.assertLessEqual(entry_date, today, f"v{version} is dated {entry_date}")

    def test_dates_never_go_backwards(self):
        dates = [d for d, _, _ in entries()]
        self.assertEqual(dates, sorted(dates, reverse=True))

    def test_entries_are_substantial(self):
        # An entry that says only what changed, with no reasoning, is useless
        # six months later.
        text = LOG.read_text()
        positions = [m.start() for m in ENTRY.finditer(text)] + [len(text)]
        for (_, version, title), start, end in zip(entries(), positions, positions[1:]):
            self.assertGreater(len(text[start:end]), 200,
                               f"v{version} ({title[:30]}) has almost no body")


class TestSuiteIntegrityTests(SimpleTestCase):
    """
    A decorator that stops a class being collected reports green while the
    tests do not run. This catches it, and catches tests deleted by mistake.
    """

    def _modules(self):
        import importlib
        import pkgutil

        import apps.ai.tests
        import apps.billing.tests
        import apps.core.tests
        import apps.safety.tests
        import apps.studio.tests

        for package in (apps.core.tests, apps.ai.tests, apps.billing.tests,
                        apps.safety.tests, apps.studio.tests):
            for info in pkgutil.iter_modules(package.__path__):
                if info.name.startswith("test_"):
                    yield importlib.import_module(f"{package.__name__}.{info.name}")

    def test_everything_named_like_a_test_class_is_one(self):
        for module in self._modules():
            for name, obj in vars(module).items():
                if not name.endswith("Tests"):
                    continue
                self.assertTrue(
                    isinstance(obj, type) and issubclass(obj, SimpleTestCase),
                    f"{module.__name__}.{name} is a {type(obj).__name__}, not a "
                    f"TestCase — its tests are not running.")

    # Raise it as tests are added; lower it only when a feature is removed
    # together with its tests.
    MINIMUM_TESTS = 456

    def test_the_suite_is_not_shrinking(self):
        import unittest

        loader = unittest.TestLoader()
        total = sum(loader.loadTestsFromModule(m).countTestCases()
                    for m in self._modules())
        self.assertGreaterEqual(
            total, self.MINIMUM_TESTS,
            f"only {total} tests collected, expected at least {self.MINIMUM_TESTS}.")
