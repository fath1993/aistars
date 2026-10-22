"""The billing admin: refunds, credit requests, adjustments, the ledger."""
from django.contrib.auth import get_user_model
from django.test import TestCase

from apps.billing import ledger, services
from apps.billing.models import (
    CreditRequest,
    EntryKind,
    LedgerEntry,
    Pack,
    PaymentStatus,
    RequestStatus,
)
from apps.core.tests.helpers import FakeTelegram, configured, keyboard_callbacks, register


def pay(account, pack, charge="ch-1"):
    payment, _ = services.record_payment(account, {
        "telegram_payment_charge_id": charge, "total_amount": pack.stars,
        "invoice_payload": services.invoice_payload(pack)})
    return payment


@configured
class BillingAdminTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(user)
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.account = register(42)
        self.pack = Pack.objects.get(stars=100)

    def test_every_page_loads(self):
        payment = pay(self.account, self.pack)
        CreditRequest.objects.create(account=self.account, amount=5)
        for url in ("/admin/billing/pack/", "/admin/billing/payment/",
                    f"/admin/billing/payment/{payment.pk}/change/",
                    "/admin/billing/creditrequest/", "/admin/billing/ledgerentry/",
                    "/admin/core/account/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_the_account_list_shows_credits(self):
        pay(self.account, self.pack)
        self.assertContains(self.client.get("/admin/core/account/"), "💎 100")

    def test_the_ledger_cannot_be_changed_or_deleted(self):
        pay(self.account, self.pack)
        entry = LedgerEntry.objects.get()
        self.assertEqual(self.client.post(f"/admin/billing/ledgerentry/{entry.pk}/delete/",
                                          {"post": "yes"}).status_code, 403)
        self.assertEqual(self.client.post(f"/admin/billing/ledgerentry/{entry.pk}/change/",
                                          {"amount": 1000}).status_code, 403)
        self.assertEqual(LedgerEntry.objects.get().amount, 100)

    def test_a_refund_returns_the_stars_and_removes_the_credits(self):
        payment = pay(self.account, self.pack)
        self.client.post("/admin/billing/payment/", {
            "action": "refund", "_selected_action": [payment.pk]})
        payment.refresh_from_db()
        self.assertEqual((payment.status, payment.refunded_by), (PaymentStatus.REFUNDED, "owner"))
        self.assertEqual(self.tg.last("refundStarPayment"),
                         {"user_id": 42, "telegram_payment_charge_id": "ch-1"})
        self.assertEqual(ledger.balance(self.account), 0)
        self.assertEqual(LedgerEntry.objects.order_by("id").last().kind, EntryKind.REFUND)
        self.assertIn("Payment refunded", self.tg.last("sendMessage")["text"])
        self.assertEqual(keyboard_callbacks(self.tg.last("sendMessage")), ["menu:new"])

    def test_spent_credits_cannot_be_refunded(self):
        payment = pay(self.account, self.pack)
        ledger.post(self.account, EntryKind.ADMIN_ADJUST, -1, note="spent")
        response = self.client.post("/admin/billing/payment/", {
            "action": "refund", "_selected_action": [payment.pk]}, follow=True)
        self.assertContains(response, "cannot be refunded")
        self.assertIsNone(self.tg.last("refundStarPayment"))
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PAID)

    def test_when_telegram_refuses_nothing_changes(self):
        payment = pay(self.account, self.pack)
        self.tg.fail.add("refundStarPayment")
        self.client.post("/admin/billing/payment/", {
            "action": "refund", "_selected_action": [payment.pk]})
        payment.refresh_from_db()
        self.assertEqual(payment.status, PaymentStatus.PAID)
        self.assertEqual(ledger.balance(self.account), 100)

    def test_approve_with_the_amount_asked_or_another(self):
        asked = CreditRequest.objects.create(account=self.account, amount=50)
        self.client.post("/admin/billing/creditrequest/", {
            "action": "approve", "_selected_action": [asked.pk], "amount": ""})
        asked.refresh_from_db()
        self.assertEqual((asked.status, asked.granted, asked.decided_by),
                         (RequestStatus.APPROVED, 50, "owner"))
        self.assertIn("Credit request approved", self.tg.last("sendMessage")["text"])
        self.assertEqual(keyboard_callbacks(self.tg.last("sendMessage")), ["menu:new"])

        other = CreditRequest.objects.create(account=register(43), amount=500)
        self.client.post("/admin/billing/creditrequest/", {
            "action": "approve", "_selected_action": [other.pk], "amount": "20"})
        other.refresh_from_db()
        self.assertEqual(other.granted, 20)
        self.assertEqual(ledger.balance(other.account), 20)
        self.assertEqual(ledger.balance(self.account), 50)

    def test_reject_tells_the_user_and_grants_nothing(self):
        request = CreditRequest.objects.create(account=self.account, amount=50)
        self.client.post("/admin/billing/creditrequest/", {
            "action": "reject", "_selected_action": [request.pk]})
        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatus.REJECTED)
        self.assertEqual(ledger.balance(self.account), 0)
        self.assertIn("declined", self.tg.last("sendMessage")["text"])
        self.assertEqual(keyboard_callbacks(self.tg.last("sendMessage")), ["menu:new"])

    def test_a_decided_request_is_not_decided_again(self):
        request = CreditRequest.objects.create(account=self.account, amount=50)
        services.approve(request, 50, "owner")
        self.client.post("/admin/billing/creditrequest/", {
            "action": "approve", "_selected_action": [request.pk], "amount": ""})
        self.assertEqual(ledger.balance(self.account), 50)

    def test_adjust_credit_needs_an_amount_and_a_reason(self):
        response = self.client.post("/admin/core/account/", {
            "action": "adjust_credit", "_selected_action": [self.account.pk],
            "amount": "25", "reason": ""}, follow=True)
        self.assertContains(response, "and a reason")
        self.assertEqual(ledger.balance(self.account), 0)

        self.client.post("/admin/core/account/", {
            "action": "adjust_credit", "_selected_action": [self.account.pk],
            "amount": "25", "reason": "goodwill"})
        entry = LedgerEntry.objects.get()
        self.assertEqual((entry.kind, entry.amount, entry.note, entry.created_by),
                         (EntryKind.ADMIN_ADJUST, 25, "goodwill", "owner"))

    def test_adjust_credit_never_goes_below_zero(self):
        response = self.client.post("/admin/core/account/", {
            "action": "adjust_credit", "_selected_action": [self.account.pk],
            "amount": "-5", "reason": "mistake"}, follow=True)
        self.assertContains(response, "needed")
        self.assertFalse(LedgerEntry.objects.exists())

    def test_the_ledger_exports_as_csv(self):
        pay(self.account, self.pack)
        response = self.client.post("/admin/billing/ledgerentry/", {
            "action": "export_csv",
            "_selected_action": list(LedgerEntry.objects.values_list("pk", flat=True))})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        rows = response.content.decode().splitlines()
        self.assertTrue(rows[0].startswith("id,created_at,telegram_user_id"))
        self.assertIn(",purchase,100,100,", rows[1])

    def test_packs_cannot_exceed_telegrams_limit(self):
        from django.core.exceptions import ValidationError

        pack = Pack(stars=10_001, credits=10_001)
        with self.assertRaises(ValidationError):
            pack.full_clean()
