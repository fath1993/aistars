from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.core.models import ControlPanel, SpendLedger
from apps.core.services import budget


@override_settings(SPEND_DAILY_CAP_MICROS=10_000_000, SPEND_HOURLY_CAP_MICROS=4_000_000)
class SpendGuardTests(TestCase):
    def test_spend_accumulates_in_one_bucket_per_hour(self):
        budget.record_spend(1_500_000)
        budget.record_spend(500_000)
        self.assertEqual(SpendLedger.objects.count(), 1)
        self.assertEqual(budget.spent_today(), {"cost": 2_000_000, "calls": 2})
        self.assertEqual(budget.spent_this_hour()["cost"], 2_000_000)

    def test_negative_spend_is_recorded_as_zero(self):
        budget.record_spend(-5)
        self.assertEqual(budget.spent_today(), {"cost": 0, "calls": 1})

    def test_within_both_caps_is_allowed(self):
        budget.record_spend(1_000_000)
        budget.assert_can_spend(1_000_000)

    def test_a_request_that_would_cross_the_hourly_cap_is_refused(self):
        budget.record_spend(3_500_000)
        with self.assertRaises(budget.CapExceeded) as ctx:
            budget.assert_can_spend(1_000_000)
        self.assertEqual(ctx.exception.kind, "cap")
        self.assertIsNotNone(ctx.exception.retry_at)

    def test_a_request_that_would_cross_the_daily_cap_is_refused(self):
        now = timezone.now()
        # Earlier today, so only the daily window counts it.
        earlier = (now - timedelta(hours=1)) if now.hour else now
        SpendLedger.objects.create(date=now.date(), hour=earlier.hour,
                                   calls=1, cost_micros=9_500_000)
        with self.assertRaises(budget.CapExceeded):
            budget.assert_can_spend(1_000_000)

    def test_the_kill_switch_refuses_everything(self):
        budget.pause("testing")
        with self.assertRaises(budget.PlatformPaused) as ctx:
            budget.assert_can_spend(0)
        self.assertIn("testing", str(ctx.exception))

        budget.resume()
        budget.assert_can_spend(0)
        panel = ControlPanel.get()
        self.assertFalse(panel.paused)
        self.assertEqual(panel.pause_reason, "")

    def test_a_zero_budget_is_a_hard_stop(self):
        ControlPanel.objects.update_or_create(singleton=1, defaults={"daily_budget": Decimal("0")})
        with self.assertRaises(budget.CapExceeded):
            budget.assert_can_spend(0)

    def test_a_negative_budget_means_no_limit(self):
        ControlPanel.objects.update_or_create(
            singleton=1, defaults={"daily_budget": Decimal("-1"),
                                   "hourly_budget": Decimal("-1")})
        budget.record_spend(50_000_000)
        budget.assert_can_spend(50_000_000)

    def test_the_admin_budget_overrides_settings(self):
        ControlPanel.objects.update_or_create(singleton=1, defaults={"hourly_budget": Decimal("1.50")})
        self.assertEqual(ControlPanel.get().hourly_cap_micros(), 1_500_000)
        budget.record_spend(1_000_000)
        with self.assertRaises(budget.CapExceeded):
            budget.assert_can_spend(600_000)

    def test_blank_budgets_fall_back_to_settings(self):
        panel = ControlPanel.get()
        self.assertEqual(panel.daily_cap_micros(), 10_000_000)
        self.assertEqual(panel.hourly_cap_micros(), 4_000_000)

    def test_capacity_reports_what_is_left(self):
        budget.record_spend(1_000_000)
        info = budget.capacity()
        self.assertEqual(info["day_spent_micros"], 1_000_000)
        self.assertEqual(info["day_cap_micros"], 10_000_000)
        self.assertFalse(info["paused"])

    def test_old_buckets_are_purged(self):
        old = timezone.now().date() - timedelta(days=100)
        SpendLedger.objects.create(date=old, hour=1, calls=1, cost_micros=1)
        budget.record_spend(1)
        self.assertEqual(budget.purge_old_ledger(90), 1)
        self.assertEqual(SpendLedger.objects.count(), 1)


class ControlPanelTests(TestCase):
    def test_there_is_only_ever_one_row(self):
        ControlPanel.get()
        ControlPanel(paused=True).save()
        self.assertEqual(ControlPanel.objects.count(), 1)
        self.assertTrue(ControlPanel.get().paused)
