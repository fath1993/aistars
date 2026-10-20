from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase

from apps.core import diagnostics, runtime
from apps.core.models import Account, AccountStatus, ActivityLog, ControlPanel

from .helpers import SECRET, TOKEN, FakeTelegram

BASE = "https://msc.example.com"
WEBHOOK = f"{BASE}/bot/webhook/"


class FakeTelegramWithWebhook(FakeTelegram):
    """Answers getWebhookInfo the way a healthy, registered bot would."""

    def __init__(self, url=WEBHOOK, **info):
        super().__init__()
        self.info = {"url": url, "pending_update_count": 0, **info}

    def __call__(self, method, **payload):
        if method == "getWebhookInfo":
            self.calls.append((method, payload))
            return self.info
        return super().__call__(method, **payload)


class AdminPagesTests(TestCase):
    def setUp(self):
        cache.clear()
        user = get_user_model().objects.create_superuser("admin", "a@example.com", "pw")
        self.client.force_login(user)
        self.tg = FakeTelegramWithWebhook()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_every_changelist_loads(self):
        Account.objects.create(telegram_user_id=1)
        for name in ("account", "runtimesetting", "controlpanel", "spendledger",
                     "activitylog", "language", "country", "sourcetext", "translation"):
            response = self.client.get(f"/admin/core/{name}/")
            self.assertEqual(response.status_code, 200, name)

    def test_the_languages_page_lists_every_language_with_progress(self):
        from apps.core.models import SourceText

        from .helpers import complete_translations

        complete_translations("ro")
        total = SourceText.objects.filter(active=True).count()
        response = self.client.get("/admin/core/language/")
        for text in ("Deutsch", "Română", "Русский", "source",
                     f"100% ({total}/{total})", f"0% (0/{total})"):
            self.assertContains(response, text)

    def test_languages_cannot_be_added_or_deleted(self):
        self.assertEqual(self.client.get("/admin/core/language/add/").status_code, 403)
        self.assertEqual(self.client.get("/admin/core/language/de/delete/").status_code, 403)

    def _change_language(self, code, **fields):
        from apps.core.models import Language

        language = Language.objects.get(code=code)
        data = {"used": "on" if language.used else "",
                "translate": "on" if language.translate else "",
                "translator": "", "translations-TOTAL_FORMS": "0",
                "translations-INITIAL_FORMS": "0"}
        data.update(fields)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            response = self.client.post(f"/admin/core/language/{code}/change/", data)
        return response, callbacks

    def test_ticking_translate_starts_a_background_translation(self):
        from apps.core.models import Language, TranslationStatus

        response, callbacks = self._change_language("de", translate="on")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(callbacks), 1)
        german = Language.objects.get(code="de")
        self.assertTrue(german.translate)
        self.assertEqual(german.status, TranslationStatus.QUEUED)

    def test_saving_a_language_already_translating_starts_nothing(self):
        _, callbacks = self._change_language("ro", used="")
        self.assertEqual(callbacks, [])

    def test_english_cannot_be_translated(self):
        from apps.core.models import Language

        _, callbacks = self._change_language("en", translate="on")
        self.assertFalse(Language.objects.get(code="en").translate)
        self.assertEqual(callbacks, [])

    def test_a_text_fixed_by_hand_is_marked_manual(self):
        from apps.core.models import Translation

        from .helpers import seed_translations

        seed_translations()
        rows = list(Translation.objects.filter(language="ro", source__active=True)
                    .select_related("source").order_by("source_id"))
        data = {"translations-TOTAL_FORMS": str(len(rows)),
                "translations-INITIAL_FORMS": str(len(rows))}
        for index, row in enumerate(rows):
            data[f"translations-{index}-id"] = str(row.pk)
            data[f"translations-{index}-language"] = "ro"
            data[f"translations-{index}-text"] = "Asistență" if row.source.text == "Help" \
                else row.text
        response, _ = self._change_language("ro", **data)
        self.assertEqual(response.status_code, 302)
        fixed = Translation.objects.get(language="ro", source__text="Help")
        self.assertEqual((fixed.text, fixed.manual, fixed.model), ("Asistență", True, ""))
        untouched = Translation.objects.get(language="ro", source__text="Profile")
        self.assertFalse(untouched.manual)

    def test_the_retranslate_action_uses_the_chosen_model(self):
        from unittest import mock as _mock

        from apps.ai.models import AIModel

        sol = AIModel.objects.get(model_id="gpt-6.1-sol")
        with _mock.patch("apps.core.translation.queue") as queue:
            self.client.post("/admin/core/language/", {
                "action": "retranslate", "_selected_action": ["ro", "en"],
                "model": sol.pk, "overwrite_manual": "on"})
        queue.assert_called_once_with("ro", model_id=sol.pk, overwrite=True,
                                      overwrite_manual=True)

    def test_the_texts_and_translations_pages_load(self):
        for name in ("sourcetext", "translation"):
            self.assertEqual(self.client.get(f"/admin/core/{name}/").status_code, 200, name)

    def test_countries_can_be_disallowed_in_bulk(self):
        from apps.core.models import Country

        response = self.client.post("/admin/core/country/", {
            "action": "disallow", "_selected_action": ["RO", "MD"]})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Country.objects.filter(code__in=["RO", "MD"], allowed=True).count(), 0)
        self.assertTrue(ActivityLog.objects.filter(event="countries_disallowed").exists())

    def test_a_countrys_default_language_can_be_set(self):
        from apps.core.models import Country

        response = self.client.post("/admin/core/country/GB/change/",
                                    {"allowed": "on", "default_language": "ru"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Country.objects.get(code="GB").default_language_id, "ru")

    def test_the_account_list_shows_country_and_language(self):
        from .helpers import register

        register(7, country="RO")
        response = self.client.get("/admin/core/account/")
        self.assertContains(response, "Romania")
        self.assertContains(response, "Română (automatic)")

    def test_a_runtime_setting_can_be_edited(self):
        from apps.core.models import RuntimeSetting

        row = RuntimeSetting.objects.get(key="PUBLIC_BASE_URL")
        response = self.client.get(f"/admin/core/runtimesetting/{row.pk}/change/")
        self.assertEqual(response.status_code, 200)

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE)
    def test_the_status_page_loads_and_registers_the_webhook(self):
        response = self.client.get("/admin/core/controlpanel/diagnostics/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Everything is working")

        response = self.client.post("/admin/core/controlpanel/diagnostics/",
                                    {"action": "register_webhook"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.tg.last("setWebhook")["url"], WEBHOOK)

    def test_the_resume_button_clears_the_kill_switch(self):
        ControlPanel.objects.update_or_create(singleton=1, defaults={"paused": True})
        self.client.post("/admin/core/controlpanel/diagnostics/", {"action": "resume"})
        self.assertFalse(ControlPanel.get().paused)

    def test_block_and_unblock_actions(self):
        account = Account.objects.create(telegram_user_id=7)
        self.client.post("/admin/core/account/", {
            "action": "block", "_selected_action": [account.pk]})
        account.refresh_from_db()
        self.assertEqual(account.status, AccountStatus.BLOCKED)
        self.assertTrue(ActivityLog.objects.filter(event="blocked", account=account).exists())

        self.client.post("/admin/core/account/?status__exact=blocked", {
            "action": "unblock", "_selected_action": [account.pk]})
        account.refresh_from_db()
        self.assertEqual(account.status, AccountStatus.ACTIVE)
        self.assertTrue(ActivityLog.objects.filter(event="unblocked", account=account).exists())

    def test_runtime_settings_cannot_be_added_or_deleted(self):
        self.assertEqual(self.client.get("/admin/core/runtimesetting/add/").status_code, 403)


class DiagnosticsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.tg = FakeTelegramWithWebhook()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    @runtime.override(TELEGRAM_BOT_TOKEN="", TELEGRAM_WEBHOOK_SECRET="", PUBLIC_BASE_URL="")
    def test_an_unconfigured_install_fails_with_fixes(self):
        report = diagnostics.run()
        failing = {check.label for check in report.failures}
        self.assertEqual(failing, {"Bot token", "Webhook secret", "Public base URL"})
        self.assertTrue(all(check.fix for check in report.failures))

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE)
    def test_a_configured_install_is_healthy(self):
        self.assertTrue(diagnostics.run().healthy)

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE)
    def test_languages_and_regions_are_checked(self):
        from apps.core.models import Country, Language

        from .helpers import complete_translations

        complete_translations("ro", "ru")
        labels = {check.label: check for check in diagnostics.run().checks}
        self.assertEqual(labels["Languages offered"].detail, "English, Română, Русский")
        self.assertEqual(labels["Translation: Romanian"].detail, "complete")
        self.assertEqual(labels["Allowed countries"].status, diagnostics.OK)

        Language.objects.update(used=False)
        Country.objects.update(allowed=False)
        labels = {check.label: check for check in diagnostics.run().checks}
        self.assertEqual(labels["Languages offered"].status, diagnostics.WARN)
        self.assertEqual(labels["Allowed countries"].status, diagnostics.WARN)

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE)
    def test_untranslated_and_failed_languages_are_warnings(self):
        from apps.core.models import Language, SourceText, TranslationStatus

        from .helpers import complete_translations

        complete_translations("ro", "ru")
        SourceText.objects.create(text="Brand new text")
        Language.objects.filter(code="ru").update(status=TranslationStatus.FAILED,
                                                  status_detail="HTTP 401")
        report = diagnostics.run()
        labels = {check.label: check for check in report.checks}
        self.assertIn("1 text(s) still in English", labels["Translation: Romanian"].detail)
        self.assertEqual(labels["Translation: Russian"].detail, "HTTP 401")
        self.assertTrue(report.healthy, "never blocks a deploy")

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE)
    def test_the_translation_model_needs_a_key(self):
        from apps.ai.models import Provider

        labels = {check.label: check for check in diagnostics.run().checks}
        self.assertEqual(labels["Translation model"].status, diagnostics.WARN)
        Provider.objects.filter(kind="openai").update(api_key="sk-openai-test")
        labels = {check.label: check for check in diagnostics.run().checks}
        self.assertEqual(labels["Translation model"].status, diagnostics.OK)

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE)
    def test_a_webhook_without_payment_checks_is_flagged(self):
        self.tg.info["allowed_updates"] = ["message", "callback_query"]
        labels = {check.label: check for check in diagnostics.run().checks}
        self.assertEqual(labels["Payment updates"].status, diagnostics.WARN)
        self.tg.info["allowed_updates"] = ["message", "callback_query", "pre_checkout_query"]
        labels = {check.label: check for check in diagnostics.run().checks}
        self.assertNotIn("Payment updates", labels)

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE)
    def test_billing_is_checked(self):
        from apps.billing.models import CreditRequest, Pack

        labels = {check.label: check for check in diagnostics.run().checks}
        self.assertEqual(labels["Packs on sale"].detail, "3")
        self.assertEqual(labels["Support contact"].status, diagnostics.WARN)
        self.assertIn("a $1.00 cost is 🪙 108", labels["Pricing"].detail)

        Pack.objects.update(active=False)
        CreditRequest.objects.create(account=Account.objects.create(telegram_user_id=9),
                                     amount=5)
        labels = {check.label: check for check in diagnostics.run().checks}
        self.assertEqual(labels["Packs on sale"].status, diagnostics.WARN)
        self.assertEqual(labels["Credit requests"].detail, "1 waiting for a decision")

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE)
    def test_a_wrong_webhook_url_fails(self):
        self.tg.info["url"] = "https://elsewhere.example.com/hook"
        labels = {check.label for check in diagnostics.run().failures}
        self.assertEqual(labels, {"Webhook URL"})

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE)
    def test_the_kill_switch_shows_as_a_failure(self):
        ControlPanel.objects.update_or_create(singleton=1, defaults={
            "paused": True, "pause_reason": "manual"})
        labels = {check.label for check in diagnostics.run().failures}
        self.assertEqual(labels, {"Kill switch"})

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET="",
                      PUBLIC_BASE_URL=BASE)
    def test_the_webhook_is_not_registered_without_a_secret(self):
        ok, note = diagnostics.register_webhook()
        self.assertFalse(ok)
        self.assertIn("secret", note)
        self.assertNotIn("setWebhook", self.tg.methods())

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL="")
    def test_the_webhook_is_not_registered_without_a_base_url(self):
        ok, _ = diagnostics.register_webhook()
        self.assertFalse(ok)

    @runtime.override(TELEGRAM_BOT_TOKEN="", TELEGRAM_WEBHOOK_SECRET="", PUBLIC_BASE_URL="")
    def test_doctor_strict_exits_non_zero_on_failure(self):
        with self.assertRaises(SystemExit) as ctx:
            call_command("doctor", "--strict", stdout=StringIO())
        self.assertEqual(ctx.exception.code, 1)

    @runtime.override(TELEGRAM_BOT_TOKEN=TOKEN, TELEGRAM_WEBHOOK_SECRET=SECRET,
                      PUBLIC_BASE_URL=BASE, SUPPORT_CONTACT="@AIstarsSupport",
                      ARCHIVE_CHAT_ID=-1001234567890)
    def test_doctor_reports_success(self):
        from apps.ai.models import Provider

        from .helpers import complete_translations, register

        Provider.objects.filter(kind="anthropic").update(api_key="sk-ant-test")
        Provider.objects.filter(kind="openai").update(api_key="sk-test")
        complete_translations("ro", "ru")
        register(42, tester=True)
        out = StringIO()
        call_command("doctor", "--strict", stdout=out)
        self.assertIn("Everything checks out.", out.getvalue())


class HousekeepingTests(TestCase):
    def test_purge_tasks_run(self):
        from apps.core import tasks

        self.assertEqual(tasks.purge_activity_log(), 0)
        self.assertEqual(tasks.purge_spend_ledger(), 0)

    def test_activity_recording_never_raises(self):
        from apps.core import activity

        with mock.patch("apps.core.models.ActivityLog.objects.create",
                        side_effect=RuntimeError("db down")):
            self.assertIsNone(activity.info("system", "x", "y"))
