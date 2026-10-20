from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase, override_settings

from apps.core import runtime
from apps.core.models import RuntimeSetting

from .helpers import TOKEN


class NormaliseAndValidateTests(SimpleTestCase):
    def test_a_bot_prefix_is_stripped_from_the_token(self):
        self.assertEqual(runtime.normalise("TELEGRAM_BOT_TOKEN", f"bot{TOKEN}"), TOKEN)

    def test_quotes_and_spaces_are_stripped(self):
        self.assertEqual(runtime.normalise("TELEGRAM_WEBHOOK_SECRET", ' "abc" '), "abc")

    def test_base_url_keeps_scheme_and_host(self):
        self.assertEqual(
            runtime.normalise("PUBLIC_BASE_URL", "https://x.example.com/?a=1"),
            "https://x.example.com")

    def test_a_bad_token_is_refused(self):
        self.assertTrue(runtime.validate("TELEGRAM_BOT_TOKEN", "nonsense"))
        self.assertEqual(runtime.validate("TELEGRAM_BOT_TOKEN", TOKEN), "")

    def test_the_secret_charset_is_enforced(self):
        self.assertTrue(runtime.validate("TELEGRAM_WEBHOOK_SECRET", "has space"))
        self.assertEqual(runtime.validate("TELEGRAM_WEBHOOK_SECRET", "a-b_c9"), "")

    def test_the_base_url_must_be_https_host_only(self):
        self.assertTrue(runtime.validate("PUBLIC_BASE_URL", "http://x.example.com"))
        self.assertTrue(runtime.validate("PUBLIC_BASE_URL", "https://x.example.com/path"))
        self.assertEqual(runtime.validate("PUBLIC_BASE_URL", "https://x.example.com"), "")

    def test_an_unknown_key_raises(self):
        with self.assertRaises(KeyError):
            runtime.get("NOT_A_SETTING")
        with self.assertRaises(KeyError):
            runtime.override(NOT_A_SETTING=1)


class ResolutionTests(TestCase):
    def setUp(self):
        cache.clear()

    def _set(self, key, value):
        row = RuntimeSetting.objects.get(key=key)
        row.value = value
        row.save()

    @override_settings(TELEGRAM_WEBHOOK_SECRET="from-settings")
    def test_a_row_wins_over_settings(self):
        self._set("TELEGRAM_WEBHOOK_SECRET", "from-row")
        self.assertEqual(runtime.get("TELEGRAM_WEBHOOK_SECRET"), "from-row")

    @override_settings(TELEGRAM_WEBHOOK_SECRET="from-settings")
    def test_a_blank_row_falls_back_to_settings(self):
        self._set("TELEGRAM_WEBHOOK_SECRET", "")
        self.assertEqual(runtime.get("TELEGRAM_WEBHOOK_SECRET"), "from-settings")

    def test_the_registry_default_is_last(self):
        self._set("ACTIVITY_LOG_RETENTION_DAYS", "")
        self.assertEqual(runtime.get("ACTIVITY_LOG_RETENTION_DAYS"), 30)

    def test_values_are_coerced_to_their_kind(self):
        self._set("ACTIVITY_LOG_RETENTION_DAYS", "7")
        self.assertEqual(runtime.get("ACTIVITY_LOG_RETENTION_DAYS"), 7)

    def test_saving_a_row_takes_effect_immediately(self):
        self._set("PUBLIC_BASE_URL", "https://a.example.com")
        self.assertEqual(runtime.get("PUBLIC_BASE_URL"), "https://a.example.com")
        self._set("PUBLIC_BASE_URL", "https://b.example.com")
        self.assertEqual(runtime.get("PUBLIC_BASE_URL"), "https://b.example.com")

    def test_save_normalises_and_clean_validates(self):
        self._set("TELEGRAM_BOT_TOKEN", f"bot{TOKEN}")
        self.assertEqual(RuntimeSetting.objects.get(key="TELEGRAM_BOT_TOKEN").value, TOKEN)

        row = RuntimeSetting.objects.get(key="TELEGRAM_BOT_TOKEN")
        row.value = "nonsense"
        with self.assertRaises(ValidationError):
            row.clean()

    def test_the_webhook_url_is_built_from_the_base_url(self):
        self._set("PUBLIC_BASE_URL", "https://x.example.com")
        self.assertEqual(runtime.webhook_url(), "https://x.example.com/bot/webhook/")

    def test_no_base_url_means_no_webhook_url(self):
        self._set("PUBLIC_BASE_URL", "")
        self.assertEqual(runtime.webhook_url(), "")

    def test_override_wins_over_everything(self):
        self._set("PUBLIC_BASE_URL", "https://row.example.com")
        with runtime.override(PUBLIC_BASE_URL="https://override.example.com"):
            self.assertEqual(runtime.get("PUBLIC_BASE_URL"), "https://override.example.com")
        self.assertEqual(runtime.get("PUBLIC_BASE_URL"), "https://row.example.com")

    def test_secrets_are_masked(self):
        self._set("TELEGRAM_WEBHOOK_SECRET", "abcdef1234")
        row = RuntimeSetting.objects.get(key="TELEGRAM_WEBHOOK_SECRET")
        self.assertEqual(row.masked(), "•" * 8 + "1234")


class SyncRegistryTests(TestCase):
    def test_every_registry_key_has_a_row_after_migrate(self):
        keys = set(RuntimeSetting.objects.values_list("key", flat=True))
        self.assertEqual(keys, set(runtime.BY_KEY))

    def test_sync_never_overwrites_a_value(self):
        row = RuntimeSetting.objects.get(key="PUBLIC_BASE_URL")
        row.value = "https://kept.example.com"
        row.save()

        self.assertEqual(runtime.sync_registry(), 0)
        row.refresh_from_db()
        self.assertEqual(row.value, "https://kept.example.com")

    @override_settings(TELEGRAM_BOT_TOKEN=TOKEN)
    def test_a_new_row_starts_from_the_env_value(self):
        RuntimeSetting.objects.filter(key="TELEGRAM_BOT_TOKEN").delete()
        self.assertEqual(runtime.sync_registry(), 1)
        self.assertEqual(RuntimeSetting.objects.get(key="TELEGRAM_BOT_TOKEN").value, TOKEN)

    def test_describing_fields_follow_the_registry(self):
        RuntimeSetting.objects.filter(key="PUBLIC_BASE_URL").update(label="stale")
        runtime.sync_registry()
        self.assertEqual(RuntimeSetting.objects.get(key="PUBLIC_BASE_URL").label,
                         runtime.BY_KEY["PUBLIC_BASE_URL"].label)


@runtime.override(PUBLIC_BASE_URL="https://decorated.example.com")
class OverrideDecoratorTests(SimpleTestCase):
    """If the class decorator broke collection, this class would vanish."""

    def test_the_class_decorator_applies(self):
        self.assertEqual(runtime.get("PUBLIC_BASE_URL"), "https://decorated.example.com")


class StackedOverrideTests(SimpleTestCase):
    """Two class decorators: after the test, neither override is left behind."""

    def test_both_apply_and_both_are_undone(self):
        @runtime.override(TELEGRAM_WEBHOOK_SECRET="outer-secret")
        @runtime.override(PUBLIC_BASE_URL="https://inner.example.com")
        class Stacked(SimpleTestCase):
            def test_it(self):
                pass

        case = Stacked("test_it")
        case.setUp()
        self.assertEqual(runtime.get("TELEGRAM_WEBHOOK_SECRET"), "outer-secret")
        self.assertEqual(runtime.get("PUBLIC_BASE_URL"), "https://inner.example.com")
        case.tearDown()
        self.assertEqual(runtime._overrides, {})
