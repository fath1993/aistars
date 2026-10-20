"""
Languages: every language of the world in the admin, used ones offered to
users, and each user's language picked in a fixed order.
"""
import json

from django.test import TestCase

from apps.core import languages, menus
from apps.core.models import Account, ActivityLog, Country, Language

from .helpers import (
    complete_translations,
    FakeTelegram,
    callback_update,
    configured,
    keyboard_callbacks,
    message_update,
    open_panel,
    post_update,
    register,
)


class WorldLanguagesTests(TestCase):
    def test_every_language_is_listed(self):
        self.assertGreater(Language.objects.count(), 180)

    def test_names_are_in_english_and_in_the_language(self):
        german = Language.objects.get(code="de")
        self.assertEqual((german.name, german.native_name), ("German", "Deutsch"))
        self.assertEqual(Language.objects.get(code="zh-hant").native_name, "繁體中文")

    def test_english_romanian_and_russian_start_used(self):
        self.assertEqual(languages.used_codes(), ["en", "ro", "ru"])

    def test_romanian_and_russian_keep_being_translated(self):
        self.assertEqual(sorted(Language.objects.filter(translate=True)
                                .values_list("code", flat=True)), ["ro", "ru"])

    def test_names_are_the_languages_own(self):
        self.assertEqual(languages.name("ro"), "Română")
        self.assertEqual(languages.names(["ru", "xx"]), {"ru": "Русский", "xx": "xx"})


class ResolveTests(TestCase):
    def account(self, language="", country=None, telegram="en"):
        return Account(telegram_user_id=1, language=language, language_code=telegram,
                       country=Country.objects.get(code=country) if country else None)

    def test_the_users_choice_wins(self):
        self.assertEqual(languages.resolve(self.account("ru", "RO")), "ru")

    def test_then_the_countrys_default(self):
        self.assertEqual(languages.resolve(self.account("", "RO", "ru")), "ro")

    def test_then_the_telegram_language(self):
        self.assertEqual(languages.resolve(self.account("", "GB", "ru")), "ru")

    def test_telegram_regional_tags_fall_back_to_the_language(self):
        self.assertEqual(languages.resolve(self.account("", None, "ru-RU")), "ru")

    def test_telegram_regional_tags_match_a_regional_language(self):
        Language.objects.filter(code="pt-br").update(used=True)
        self.assertEqual(languages.resolve(self.account("", None, "pt-BR")), "pt-br")

    def test_then_english(self):
        self.assertEqual(languages.resolve(self.account("", "GB", "fr")), "en")

    def test_an_unused_choice_is_skipped(self):
        Language.objects.filter(code="ru").update(used=False)
        self.assertEqual(languages.resolve(self.account("ru", "RO")), "ro")

    def test_with_nothing_used_it_is_english(self):
        Language.objects.update(used=False)
        self.assertEqual(languages.resolve(self.account("ru", "RO", "ru")), "en")


@configured
class PickerTests(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        register(42)
        post_update(message_update("/start"))
        self.panel = open_panel()

    def test_profile_has_a_language_button(self):
        post_update(callback_update("menu:profile", self.panel))
        callbacks = keyboard_callbacks(self.tg.last("editMessageText"))
        self.assertEqual(callbacks, ["lang:", "menu:privacy", "menu:home", "menu:home"])

    def test_the_list_shows_the_used_languages_and_ticks_the_current(self):
        Language.objects.filter(code="ru").update(used=False)
        post_update(callback_update("lang:", self.panel))
        edit = self.tg.last("editMessageText")
        self.assertEqual(keyboard_callbacks(edit),
                         ["lang:en", "lang:ro", "menu:profile", "menu:home"])
        labels = [row[0]["text"] for row in json.loads(edit["reply_markup"])["inline_keyboard"]]
        self.assertEqual(labels[:2], ["✓ English", "Română"])

    def test_choosing_a_language_saves_it_and_answers_in_it(self):
        complete_translations("ro")
        post_update(callback_update("lang:ro", self.panel))
        self.assertEqual(Account.objects.get(telegram_user_id=42).language, "ro")
        self.assertIn("[ro] Profile", self.tg.last("editMessageText")["text"])
        self.assertIn("[ro] Language: Română", self.tg.last("editMessageText")["text"])
        self.assertTrue(ActivityLog.objects.filter(event="language_changed").exists())

    def test_the_whole_bot_follows_the_choice(self):
        complete_translations("ru")
        post_update(callback_update("lang:ru", self.panel))
        post_update(message_update("/start"))
        self.assertEqual(self.tg.last("sendMessage")["text"].split("\n\n")[1],
                         "[ru] Chat with AI, create images and turn voice into text, right here "
                         "in Telegram.")
        buttons = json.loads(self.tg.last("sendMessage")["reply_markup"])["inline_keyboard"][1]
        self.assertEqual([b["text"] for b in buttons], ["👤 [ru] Profile", "❓ [ru] Help"])

    def test_an_unused_language_cannot_be_chosen(self):
        Language.objects.filter(code="ru").update(used=False)
        post_update(callback_update("lang:ru", self.panel))
        self.assertEqual(Account.objects.get(telegram_user_id=42).language, "")
        self.assertEqual(self.tg.last("editMessageText")["text"], menus.language_text())

    def test_english_text_stays_english_by_default(self):
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.home_text())
        self.assertIn("Chat with AI, create images", menus.home_text())
