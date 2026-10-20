"""
Registration: nothing works until the user shares their own phone number, and
only numbers from allowed countries get in.
"""
import json

from django.test import TestCase
from django.utils import translation

from apps.core import menus, registration
from apps.core.models import Account, ActivityLog, Country

from .helpers import (
    complete_translations,
    FakeTelegram,
    callback_update,
    configured,
    contact_update,
    message_update,
    post_update,
    register,
)

ROMANIAN = "+40712345678"
BRITISH = "+447700900123"
IRANIAN = "+989121234567"
ANONYMOUS = "+88812345678"   # Telegram's anonymous numbers: no country


def markup(payload):
    return json.loads(payload["reply_markup"])


class PhoneNumberTests(TestCase):
    def test_the_country_comes_from_the_calling_code(self):
        self.assertEqual(registration.country_code(registration.parse(ROMANIAN)), "RO")
        self.assertEqual(registration.country_code(registration.parse(BRITISH)), "GB")

    def test_telegram_numbers_without_a_plus_are_read(self):
        self.assertEqual(registration.country_code(registration.parse("40712345678")), "RO")

    def test_a_number_the_library_thinks_invalid_keeps_its_country(self):
        # +44 7700 900xxx is a range reserved for fiction: not "valid", but
        # unmistakably British. New real ranges look the same to an old library.
        number = registration.parse(BRITISH)
        self.assertFalse(__import__("phonenumbers").is_valid_number(number))
        self.assertEqual(registration.country_code(number), "GB")

    def test_anonymous_numbers_have_no_country(self):
        self.assertEqual(registration.country_code(registration.parse(ANONYMOUS)), "")

    def test_garbage_has_no_country(self):
        self.assertIsNone(registration.parse("not a number"))
        self.assertEqual(registration.country_code(None), "")


class SeedTests(TestCase):
    def test_every_country_is_seeded(self):
        self.assertGreater(Country.objects.count(), 240)

    def test_countries_openai_does_not_support_start_disallowed(self):
        # Iran is refused whatever happens; OpenAI's list leaves out the rest.
        for code in ("IR", "RU", "BY", "CN", "HK", "MO", "CU", "KP", "SY", "VE"):
            self.assertFalse(Country.objects.get(code=code).allowed, code)

    def test_openais_supported_countries_start_allowed(self):
        for code in ("RO", "MD", "GB", "US", "DE", "KZ", "UA", "TR", "AE", "AF", "TJ"):
            self.assertTrue(Country.objects.get(code=code).allowed, code)
        self.assertEqual(Country.objects.filter(allowed=True).count(), 207)

    def test_default_languages(self):
        self.assertEqual(Country.objects.get(code="RO").default_language_id, "ro")
        self.assertEqual(Country.objects.get(code="KZ").default_language_id, "ru")
        self.assertIsNone(Country.objects.get(code="GB").default_language_id)

    def test_the_flag_is_built_from_the_code(self):
        self.assertEqual(Country.objects.get(code="RO").flag, "🇷🇴")


@configured
class UnregisteredTests(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_start_asks_for_the_phone_number_and_shows_no_panel(self):
        post_update(message_update("/start"))
        sent = self.tg.last("sendMessage")
        self.assertEqual(sent["text"], menus.register_text())
        button = markup(sent)["keyboard"][0][0]
        self.assertTrue(button["request_contact"])
        self.assertIsNone(Account.objects.get(telegram_user_id=42).ui_message_id)

    def test_every_command_asks_for_the_phone_number(self):
        for text in ("/help", "/menu", "hello"):
            post_update(message_update(text))
            self.assertEqual(self.tg.last("sendMessage")["text"], menus.register_text())

    def test_a_button_asks_for_the_phone_number(self):
        post_update(callback_update("menu:help", 5))
        self.assertEqual(self.tg.last("answerCallbackQuery")["text"],
                         menus.register_first_text())
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.register_text())
        self.assertNotIn("editMessageText", self.tg.methods())

    def test_the_prompt_is_in_the_telegram_language_when_it_is_enabled(self):
        complete_translations("ru")
        update = message_update("/start")
        update["message"]["from"]["language_code"] = "ru"
        post_update(update)
        self.assertIn("[ru] Welcome to AIstars", self.tg.last("sendMessage")["text"])

    def test_someone_elses_contact_is_refused(self):
        post_update(contact_update(ROMANIAN, contact_user_id=999))
        account = Account.objects.get(telegram_user_id=42)
        self.assertFalse(account.is_registered)
        self.assertEqual(account.phone, "")
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.not_own_number_text())

    def test_own_number_from_an_allowed_country_registers(self):
        post_update(contact_update(ROMANIAN))
        account = Account.objects.get(telegram_user_id=42)
        self.assertTrue(account.is_registered)
        self.assertEqual(account.phone, ROMANIAN)
        self.assertEqual(account.country_id, "RO")
        self.assertTrue(ActivityLog.objects.filter(event="registered").exists())

    def test_registering_removes_the_phone_button_and_shows_the_panel(self):
        post_update(contact_update(BRITISH))
        sends = [payload for method, payload in self.tg.calls if method == "sendMessage"]
        self.assertEqual(sends[0]["text"], menus.registered_text())
        self.assertEqual(markup(sends[0]), {"remove_keyboard": True})
        self.assertEqual(sends[-1]["text"], menus.home_text())
        self.assertIsNotNone(Account.objects.get(telegram_user_id=42).ui_message_id)

    def test_the_panel_speaks_the_countrys_default_language(self):
        # Telegram says English; Romania's default is Romanian, and it wins.
        complete_translations("ro")
        post_update(contact_update(ROMANIAN, language_code="en"))
        self.assertIn("[ro] Chat with AI", self.tg.last("sendMessage")["text"])

    def test_a_disallowed_country_is_refused(self):
        post_update(contact_update(IRANIAN))
        account = Account.objects.get(telegram_user_id=42)
        self.assertEqual(account.country_id, "IR")
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.region_text())
        self.assertIsNone(account.ui_message_id)
        self.assertTrue(ActivityLog.objects.filter(event="region_refused").exists())

    def test_a_number_with_no_country_is_refused(self):
        post_update(contact_update(ANONYMOUS))
        account = Account.objects.get(telegram_user_id=42)
        self.assertTrue(account.is_registered)
        self.assertIsNone(account.country)
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.region_text())


@configured
class RegionGateTests(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_refused_user_stays_refused(self):
        register(42, country="IR")
        post_update(message_update("/start"))
        self.assertEqual(self.tg.last("sendMessage")["text"], menus.region_text())

    def test_disallowing_a_country_locks_its_users_out_at_once(self):
        register(42, country="RO")
        Country.objects.filter(code="RO").update(allowed=False)
        post_update(message_update("/start"))
        with translation.override("ro"):   # Romania's default language
            expected = menus.region_text()
        self.assertEqual(self.tg.last("sendMessage")["text"], expected)
        self.assertNotIn("editMessageText", self.tg.methods())
        self.assertIsNone(Account.objects.get(telegram_user_id=42).ui_message_id)

    def test_allowing_it_again_lets_them_back_in(self):
        register(42, country="RO")
        Country.objects.filter(code="RO").update(allowed=False)
        Country.objects.filter(code="RO").update(allowed=True)
        post_update(message_update("/start"))
        self.assertIsNotNone(Account.objects.get(telegram_user_id=42).ui_message_id)

    def test_a_region_refused_button_is_answered_and_nothing_else(self):
        register(42, country="IR")
        post_update(callback_update("menu:help", 5))
        self.assertEqual(self.tg.methods(), ["answerCallbackQuery"])

    def test_blocked_wins_over_everything(self):
        from apps.core.models import AccountStatus

        register(42, country="RO", status=AccountStatus.BLOCKED)
        post_update(message_update("/start"))
        self.assertEqual(self.tg.methods(), ["sendMessage"])
