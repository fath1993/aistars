"""
The credits part of the bot, through the webhook: the Credits screen, buying
with Stars, the statement, credit requests and the required commands.
"""
import itertools
import json

from django.test import TestCase

from apps.billing import ledger, services
from apps.billing.models import CreditRequest, EntryKind, LedgerEntry, Pack, Payment
from apps.core import runtime
from apps.core.models import Account, AccountStatus, Country
from apps.core.services import telegram
from apps.core.tests.helpers import (
    FakeTelegram,
    callback_update,
    configured,
    keyboard_callbacks,
    message_update,
    open_panel,
    post_update,
    register,
)

_ids = itertools.count(5000)


def pre_checkout_update(pack, user_id=42, stars=None, currency="XTR", payload=None):
    return {"update_id": next(_ids), "pre_checkout_query": {
        "id": f"pcq{next(_ids)}", "from": {"id": user_id, "first_name": "Amir"},
        "currency": currency, "total_amount": pack.stars if stars is None else stars,
        "invoice_payload": services.invoice_payload(pack) if payload is None else payload}}


def paid_update(pack=None, charge="charge-1", user_id=42, payload=None, stars=None):
    return {"update_id": next(_ids), "message": {
        "message_id": next(_ids), "from": {"id": user_id, "first_name": "Amir"},
        "chat": {"id": user_id, "type": "private"},
        "successful_payment": {
            "currency": "XTR", "total_amount": stars if stars is not None else pack.stars,
            "invoice_payload": payload if payload is not None else services.invoice_payload(pack),
            "telegram_payment_charge_id": charge, "provider_payment_charge_id": ""}}}


class BotTestCase(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.account = register(42)
        self.pack = Pack.objects.get(stars=100)
        post_update(message_update("/start"))
        self.panel = open_panel()

    def sent(self, method):
        return [payload for name, payload in self.tg.calls if name == method]


@configured
class CreditsScreenTests(BotTestCase):
    def test_home_has_a_credits_button(self):
        self.assertEqual(keyboard_callbacks(self.tg.last("sendMessage"))[0], "credits:")

    def test_the_credits_screen_shows_the_balance(self):
        ledger.post(self.account, EntryKind.PURCHASE, 120)
        post_update(callback_update("credits:", self.panel))
        edit = self.tg.last("editMessageText")
        self.assertIn("Balance: <b>🪙 120</b>", edit["text"])
        self.assertEqual(keyboard_callbacks(edit), [
            "credits:buy", "credits:statement:0", "credits:request", "menu:home", "menu:home"])

    def test_the_credits_command(self):
        post_update(message_update("/credits"))
        self.assertIn("Balance: <b>🪙 0</b>", self.tg.last("sendMessage")["text"])

    def test_the_packs_on_sale(self):
        Pack.objects.filter(stars=1000).update(active=False)
        post_update(callback_update("credits:buy", self.panel))
        edit = self.tg.last("editMessageText")
        labels = [row[0]["text"] for row in json.loads(edit["reply_markup"])["inline_keyboard"]]
        self.assertEqual(labels, ["🪙 100 = ⭐ 100", "🪙 300 = ⭐ 300", "‹ Back"])

    def test_tapping_a_pack_sends_a_stars_invoice(self):
        post_update(callback_update(f"credits:pack:{self.pack.pk}", self.panel))
        invoice = self.tg.last("sendInvoice")
        self.assertEqual(invoice["currency"], "XTR")
        self.assertEqual(json.loads(invoice["prices"]), [{"label": "🪙 100 = ⭐ 100",
                                                          "amount": 100}])
        self.assertEqual(invoice["payload"], f"pack:{self.pack.pk}:100:100")
        self.assertNotIn("provider_token", invoice)
        self.assertLessEqual(len(invoice["title"]), 32)

    def test_an_inactive_pack_sends_no_invoice(self):
        Pack.objects.filter(pk=self.pack.pk).update(active=False)
        post_update(callback_update(f"credits:pack:{self.pack.pk}", self.panel))
        self.assertIsNone(self.tg.last("sendInvoice"))

    def test_the_webhook_asks_telegram_for_payment_checks(self):
        telegram.set_webhook("https://msc.example.com/bot/webhook/")
        self.assertIn("pre_checkout_query",
                      json.loads(self.tg.last("setWebhook")["allowed_updates"]))


@configured
class PaymentTests(BotTestCase):
    def answer(self):
        return self.tg.last("answerPreCheckoutQuery")

    def test_a_valid_pre_checkout_is_accepted(self):
        post_update(pre_checkout_update(self.pack))
        self.assertEqual(self.answer()["ok"], "true")

    def test_a_changed_pack_is_refused_with_a_reason(self):
        update = pre_checkout_update(self.pack)
        Pack.objects.filter(pk=self.pack.pk).update(stars=150)
        post_update(update)
        self.assertEqual(self.answer()["ok"], "false")
        self.assertIn("pack has changed", self.answer()["error_message"])

    def test_an_inactive_pack_is_refused(self):
        Pack.objects.filter(pk=self.pack.pk).update(active=False)
        post_update(pre_checkout_update(self.pack))
        self.assertEqual(self.answer()["ok"], "false")

    def test_a_wrong_amount_or_currency_is_refused(self):
        post_update(pre_checkout_update(self.pack, stars=1))
        self.assertEqual(self.answer()["ok"], "false")
        post_update(pre_checkout_update(self.pack, currency="USD"))
        self.assertEqual(self.answer()["ok"], "false")

    def test_a_blocked_or_region_refused_account_cannot_pay(self):
        Account.objects.filter(pk=self.account.pk).update(status=AccountStatus.BLOCKED)
        post_update(pre_checkout_update(self.pack))
        self.assertEqual(self.answer()["ok"], "false")
        Account.objects.filter(pk=self.account.pk).update(status=AccountStatus.ACTIVE)
        Country.objects.filter(code="GB").update(allowed=False)
        post_update(pre_checkout_update(self.pack))
        self.assertEqual(self.answer()["ok"], "false")

    def test_a_payment_is_credited_with_a_receipt(self):
        post_update(paid_update(self.pack))
        payment = Payment.objects.get()
        self.assertEqual((payment.stars, payment.credits, payment.pack), (100, 100, self.pack))
        self.assertEqual(ledger.balance(self.account), 100)
        receipt = self.sent("sendMessage")[-2]
        self.assertIn("Payment received", receipt["text"])
        self.assertIn("<code>charge-1</code>", receipt["text"])
        self.assertEqual(keyboard_callbacks(receipt), ["menu:new"])
        self.assertIn("Balance: <b>🪙 100</b>", self.tg.last("sendMessage")["text"])

    def test_the_same_payment_twice_counts_once(self):
        post_update(paid_update(self.pack))
        sends = len(self.sent("sendMessage"))
        post_update(paid_update(self.pack))
        self.assertEqual(LedgerEntry.objects.count(), 1)
        self.assertEqual(ledger.balance(self.account), 100)
        self.assertEqual(len(self.sent("sendMessage")), sends)

    def test_the_invoice_decides_the_credits_not_todays_pack(self):
        update = paid_update(self.pack)
        Pack.objects.filter(pk=self.pack.pk).update(credits=150)
        post_update(update)
        self.assertEqual(ledger.balance(self.account), 100)

    def test_paid_is_paid_even_if_the_account_was_blocked_since(self):
        Account.objects.filter(pk=self.account.pk).update(status=AccountStatus.BLOCKED)
        post_update(paid_update(self.pack))
        self.assertEqual(ledger.balance(self.account), 100)

    def test_an_unreadable_payload_still_credits_what_was_paid(self):
        post_update(paid_update(payload="garbage", stars=70))
        self.assertEqual(ledger.balance(self.account), 70)


@configured
class StatementTests(BotTestCase):
    def test_an_empty_statement(self):
        post_update(callback_update("credits:statement:0", self.panel))
        self.assertIn("No credit movements yet", self.tg.last("editMessageText")["text"])

    def test_movements_newest_first_with_pages(self):
        for amount in (100, 300):
            ledger.post(self.account, EntryKind.PURCHASE, amount)
        for _ in range(10):
            ledger.post(self.account, EntryKind.ADMIN_ADJUST, -1, note="x")
        post_update(callback_update("credits:statement:0", self.panel))
        edit = self.tg.last("editMessageText")
        self.assertIn("Adjusted by support", edit["text"])
        self.assertIn("<b>−1</b> → 🪙 390", edit["text"])
        self.assertEqual(keyboard_callbacks(edit),
                         ["credits:statement:1", "credits:", "menu:home"])
        post_update(callback_update("credits:statement:1", self.panel))
        edit = self.tg.last("editMessageText")
        self.assertIn("<b>+300</b> → 🪙 400", edit["text"])
        self.assertIn("Bought credits", edit["text"])
        self.assertEqual(keyboard_callbacks(edit),
                         ["credits:statement:0", "credits:", "menu:home"])


@configured
class CreditRequestTests(BotTestCase):
    def test_the_whole_request(self):
        post_update(callback_update("credits:request", self.panel))
        self.assertIn("How many credits", self.tg.last("editMessageText")["text"])
        post_update(message_update("lots"))
        self.assertIn("whole number", self.tg.last("sendMessage")["text"])
        post_update(message_update("500"))
        self.assertIn("short note", self.tg.last("sendMessage")["text"])
        post_update(message_update("For a school project, I pay by bank transfer"))
        request = CreditRequest.objects.get()
        self.assertEqual((request.amount, request.status), (500, "pending"))
        self.assertIn("bank transfer", request.note)
        self.assertIn("Request sent", self.tg.last("sendMessage")["text"])

    @runtime.override(ADMIN_CHAT_ID=999, PUBLIC_BASE_URL="https://msc.example.com")
    def test_the_owner_is_told(self):
        post_update(callback_update("credits:request", self.panel))
        post_update(message_update("50"))
        post_update(message_update("please"))
        notice = [p for p in self.sent("sendMessage") if p["chat_id"] == 999][0]
        self.assertIn("New credit request", notice["text"])
        self.assertIn("🪙 50", notice["text"])
        self.assertIn("/admin/billing/creditrequest/", notice["text"])

    def test_one_request_at_a_time(self):
        CreditRequest.objects.create(account=self.account, amount=10)
        post_update(callback_update("credits:request", self.panel))
        self.assertIn("already have a request", self.tg.last("editMessageText")["text"])

    def test_a_command_cancels_the_question(self):
        post_update(callback_update("credits:request", self.panel))
        post_update(message_update("/menu"))
        post_update(message_update("500"))
        self.assertFalse(CreditRequest.objects.exists())


@configured
class RequiredCommandTests(BotTestCase):
    def test_terms(self):
        post_update(message_update("/terms"))
        self.assertIn("Terms", self.tg.last("sendMessage")["text"])

    @runtime.override(SUPPORT_CONTACT="@AIstarsSupport")
    def test_paysupport_and_support_name_the_contact(self):
        post_update(message_update("/paysupport"))
        self.assertIn("@AIstarsSupport", self.tg.last("sendMessage")["text"])
        self.assertIn("charge ID", self.tg.last("sendMessage")["text"])
        post_update(message_update("/support"))
        self.assertIn("@AIstarsSupport", self.tg.last("sendMessage")["text"])

    def test_without_a_contact_they_still_answer(self):
        post_update(message_update("/paysupport"))
        self.assertIn("not set up", self.tg.last("sendMessage")["text"])
