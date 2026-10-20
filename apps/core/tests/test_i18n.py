"""
The bot's texts: found in the code on every deploy, looked up in the active
language, and English whenever there is no translation.
"""
import tempfile
from pathlib import Path

from django.test import TestCase
from django.utils import translation

from apps.core import i18n
from apps.core.models import Language, SourceText, Translation

from .helpers import seed_translations


def code_tree(source):
    """A throwaway project folder with apps/demo/menus.py holding `source`."""
    root = Path(tempfile.mkdtemp(prefix="ais-code-"))
    (root / "apps" / "demo" / "tests").mkdir(parents=True)
    (root / "apps" / "demo" / "menus.py").write_text(source)
    (root / "apps" / "demo" / "tests" / "test_x.py").write_text('_("only in a test")\n')
    return root


class ExtractTests(TestCase):
    def test_every_text_of_the_bot_is_found(self):
        found = i18n.extract()
        self.assertIn("Help", found)
        self.assertIn("Choose the language of the bot.", found)
        self.assertTrue(found["Help"].startswith("apps/core/menus.py:"))

    def test_the_recorded_texts_are_exactly_the_texts_in_the_code(self):
        # Recorded after every migrate; the database's own texts (catalog:)
        # come on top.
        self.assertEqual(set(i18n.extract()),
                         set(SourceText.objects.filter(active=True)
                             .exclude(location__startswith=i18n.CATALOG)
                             .values_list("text", flat=True)))

    def test_joined_literals_tests_and_non_literals(self):
        root = code_tree('_("one " "text")\ngettext("two")\n_(name)\nprint("three")\n')
        self.assertEqual(list(i18n.extract(root)), ["one text", "two"])

    def test_no_text_contains_html_or_unnamed_placeholders(self):
        for text in i18n.extract():
            with self.subTest(text=text[:40]):
                self.assertNotRegex(text, r"<[a-zA-Z/]")
                self.assertNotRegex(text, r"%[sd]")


class SyncTests(TestCase):
    def setUp(self):
        seed_translations()

    def test_new_texts_are_added_and_returned(self):
        new = i18n.sync_sources(code_tree('_("Help")\n_("Brand new")\n'))
        self.assertEqual([source.text for source in new], ["Brand new"])

    def test_texts_gone_from_the_code_become_inactive_and_come_back(self):
        i18n.sync_sources(code_tree('_("Help")\n'))
        self.assertFalse(SourceText.objects.get(text="Profile").active)
        self.assertTrue(Translation.objects.filter(source__text="Profile").exists())
        i18n.sync_sources()
        self.assertTrue(SourceText.objects.get(text="Profile").active)

    def test_the_real_code_adds_nothing_new(self):
        self.assertEqual(i18n.sync_sources(), [])

    def test_after_a_partial_migrate_nothing_is_recorded(self):
        from unittest import mock

        from apps.core import apps as core_apps

        with mock.patch("django.db.backends.base.introspection.BaseDatabaseIntrospection"
                        ".table_names", return_value=[]), \
                mock.patch.object(i18n, "sync_sources") as sync:
            core_apps._sync_texts(sender=None)
        sync.assert_not_called()

    def test_after_a_migrate_new_texts_are_queued(self):
        from unittest import mock

        from apps.core import apps as core_apps, tasks

        with mock.patch.object(i18n, "sync_sources", return_value=["new"]), \
                mock.patch.object(tasks.translate_missing, "delay") as delay:
            core_apps._sync_texts(sender=None)
        delay.assert_called_once()


class LookupTests(TestCase):
    def setUp(self):
        seed_translations()

    def test_english_is_the_text_itself(self):
        with translation.override("en"):
            self.assertEqual(i18n.gettext("Help"), "Help")

    def test_a_translated_text(self):
        with translation.override("ro"):
            self.assertEqual(i18n.gettext("Help"), "Ajutor")

    def test_an_untranslated_text_is_english(self):
        Translation.objects.filter(language="ro", source__text="Help").delete()
        with translation.override("ro"):
            self.assertEqual(i18n.gettext("Help"), "Help")

    def test_a_language_with_nothing_translated_is_english(self):
        with translation.override("de"):
            self.assertEqual(i18n.gettext("Help"), "Help")

    def test_an_edit_is_seen_at_once(self):
        Translation.objects.filter(language="ro", source__text="Help").update(text="Asistență")
        with translation.override("ro"):
            self.assertEqual(i18n.gettext("Help"), "Asistență")

    def test_an_inactive_text_is_not_served(self):
        SourceText.objects.filter(text="Help").update(active=False)
        with translation.override("ro"):
            self.assertEqual(i18n.gettext("Help"), "Help")


class CatalogCacheTests(TestCase):
    """Production keeps a catalog per process; any save must reach it."""

    def setUp(self):
        from django.core.cache import cache
        from django.test import override_settings

        cache.clear()
        i18n._local.clear()
        seed_translations()
        self.override = override_settings(I18N_CACHE=True)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.addCleanup(i18n._local.clear)

    def test_the_catalog_is_kept_between_lookups(self):
        with translation.override("ro"):
            i18n.gettext("Help")
            with self.assertNumQueries(0):
                self.assertEqual(i18n.gettext("Profile"), "Profil")

    def test_saving_a_translation_reloads_it(self):
        with translation.override("ro"):
            self.assertEqual(i18n.gettext("Help"), "Ajutor")
            row = Translation.objects.get(language="ro", source__text="Help")
            row.text = "Asistență"
            row.save()
            self.assertEqual(i18n.gettext("Help"), "Asistență")

    def test_another_process_sees_the_new_version(self):
        from django.core.cache import cache

        with translation.override("ro"):
            i18n.gettext("Help")
            # Another process saves: the database changes and the shared
            # version goes up, but this process's catalog is untouched.
            Translation.objects.filter(language="ro", source__text="Help").update(text="Nou")
            cache.incr(i18n.VERSION_KEY)
            self.assertEqual(i18n.gettext("Help"), "Ajutor", "kept until its TTL ends")
            expires, version, texts = i18n._local["ro"]
            i18n._local["ro"] = (0, version, texts)
            self.assertEqual(i18n.gettext("Help"), "Nou")

    def test_languages_are_cached_separately(self):
        with translation.override("ru"):
            self.assertEqual(i18n.gettext("Help"), "Помощь")
        with translation.override("ro"):
            self.assertEqual(i18n.gettext("Help"), "Ajutor")
        self.assertEqual(set(i18n._local), {"ru", "ro"})
        self.assertTrue(Language.objects.filter(code="ru").exists())
