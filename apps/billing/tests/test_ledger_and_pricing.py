"""The ledger (the only way credits move) and the price formula."""
from django.test import TestCase

from apps.billing import ledger, pricing
from apps.billing.models import EntryKind, LedgerEntry
from apps.core import runtime
from apps.core.tests.helpers import register


class LedgerTests(TestCase):
    def setUp(self):
        self.account = register(42)

    def test_a_new_account_has_nothing(self):
        self.assertEqual(ledger.balance(self.account), 0)

    def test_every_movement_records_the_balance_after_it(self):
        ledger.post(self.account, EntryKind.PURCHASE, 100)
        ledger.post(self.account, EntryKind.ADMIN_ADJUST, -30, note="test")
        entries = list(LedgerEntry.objects.order_by("id").values_list("amount", "balance_after"))
        self.assertEqual(entries, [(100, 100), (-30, 70)])
        self.assertEqual(ledger.balance(self.account), 70)

    def test_the_balance_never_goes_negative(self):
        ledger.post(self.account, EntryKind.PURCHASE, 10)
        with self.assertRaises(ledger.InsufficientCredits):
            ledger.post(self.account, EntryKind.ADMIN_ADJUST, -11)
        self.assertEqual(LedgerEntry.objects.count(), 1)

    def test_balances_are_per_account(self):
        other = register(43)
        ledger.post(self.account, EntryKind.PURCHASE, 10)
        ledger.post(other, EntryKind.PURCHASE, 5)
        self.assertEqual((ledger.balance(self.account), ledger.balance(other)), (10, 5))

    def test_history_pages_newest_first(self):
        for amount in range(1, 13):
            ledger.post(self.account, EntryKind.PURCHASE, amount)
        first, older = ledger.history(self.account, 0)
        self.assertEqual([entry.amount for entry in first], list(range(12, 2, -1)))
        self.assertTrue(older)
        second, older = ledger.history(self.account, 1)
        self.assertEqual([entry.amount for entry in second], [2, 1])
        self.assertFalse(older)


class PricingTests(TestCase):
    def test_the_build_plan_example(self):
        # $0.50 at 40% with one Star = $0.013: 0.50 / 0.013 * 1.4 = 53.8 -> 54
        self.assertEqual(pricing.credits_for("0.50"), 54)

    def test_rounds_up(self):
        self.assertEqual(pricing.credits_for("0.30"), 33)

    @runtime.override(PROFIT_MARGIN_PERCENT=0.0)
    def test_the_margin_comes_from_the_admin(self):
        self.assertEqual(pricing.credits_for("0.13"), 10)

    @runtime.override(STAR_VALUE_USD=0.02)
    def test_the_star_value_comes_from_the_admin(self):
        self.assertEqual(pricing.credits_for("0.20"), 14)

    def test_nothing_costs_nothing_and_something_costs_at_least_one(self):
        self.assertEqual(pricing.credits_for(0), 0)
        self.assertEqual(pricing.credits_for("0.0001"), 1)

    def test_the_settings_are_validated(self):
        self.assertEqual(runtime.validate("PROFIT_MARGIN_PERCENT", "40"), "")
        self.assertIn("Between", runtime.validate("PROFIT_MARGIN_PERCENT", "-5"))
        self.assertIn("less than 1", runtime.validate("STAR_VALUE_USD", "13"))
        self.assertIn("digits", runtime.validate("ADMIN_CHAT_ID", "@me"))
