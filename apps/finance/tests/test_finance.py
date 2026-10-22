"""
My finances: every AI call recorded with its provider and purpose; the
overview (money in, AI costs split by provider and purpose, bills in USD and
EUR, profit, what is left at each provider, credits users hold); the
spreadsheet; invoice files for superusers only; Telegram Stars; the low
balance message and the monthly bills.
"""
import shutil
import tempfile
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.ai.models import AICall
from apps.billing import ledger
from apps.billing.models import EntryKind, Payment, PaymentStatus
from apps.core import runtime
from apps.core.tests.helpers import register
from apps.finance import money, tasks
from apps.finance.models import Bill

OVERVIEW = "/admin/finance/overview/"


def call(provider, purpose, usd, days_ago=0):
    row = AICall.objects.create(provider=provider, model="m", purpose=purpose,
                                cost_micros=int(usd * 1_000_000))
    if days_ago:
        AICall.objects.filter(pk=row.pk).update(at=timezone.now() - timedelta(days=days_ago))
    return row


def payment(account, stars, charge, refunded=False):
    return Payment.objects.create(
        account=account, stars=stars, credits=stars, telegram_charge_id=charge,
        status=PaymentStatus.REFUNDED if refunded else PaymentStatus.PAID,
        refunded_at=timezone.now() if refunded else None)


class MoneyTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.owner = User.objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(self.owner)
        self.account = register(42)


class OverviewTests(MoneyTestCase):
    def fill(self):
        payment(self.account, 1000, "c1")
        payment(self.account, 200, "c2", refunded=True)
        call("openai", "chat", 0.50)
        call("openai", "safety_judge", 0.10)
        call("anthropic", "chat", 0.40)
        Bill.objects.create(payee="server", description="Hetzner", amount=Decimal("5.00"),
                            currency="EUR")
        Bill.objects.create(payee="openai", description="Top-up", amount=Decimal("20.00"))
        ledger.post(self.account, EntryKind.PURCHASE, 300)

    def test_the_numbers(self):
        self.fill()
        start, end = money.month_range(timezone.localdate().year, timezone.localdate().month)
        data = money.overview(start, end)
        # 1,200 Stars paid, 200 refunded, at $0.013.
        self.assertEqual(data["income"]["net"], Decimal("13.000"))
        self.assertEqual(data["ai"]["total"], Decimal("1"))
        self.assertEqual(data["ai"]["users_total"], Decimal("0.9"))
        self.assertEqual(data["ai"]["own_total"], Decimal("0.1"))
        # €5 at 1.15; the OpenAI top-up is not a cost (its use is, call by call).
        self.assertEqual(data["other"], Decimal("5.75"))
        self.assertEqual(data["profit"], Decimal("6.250"))
        balance = money.provider_balances()
        self.assertEqual(len(balance), 1)
        self.assertEqual((balance[0]["name"], balance[0]["left"]), ("OpenAI", Decimal("19.4")))
        held = money.held_credits()
        self.assertEqual(held["credits"], 300)
        self.assertEqual(held["paid"], Decimal("3.900"))

    def test_the_page(self):
        self.fill()
        page = self.client.get(OVERVIEW)
        self.assertEqual(page.status_code, 200)
        text = page.content.decode()
        for expected in ("$13.00", "$6.75", "$6.25", "🪙 300", "$19.40", "Chat answers",
                         "Safety judge", "Hetzner", "5.00 EUR", "€11.30", "€5.43"):
            self.assertIn(expected, text, expected)

    def test_an_older_month_and_all_time(self):
        call("openai", "chat", 2.0, days_ago=40)
        last = timezone.localdate().replace(day=1) - timedelta(days=1)
        page = self.client.get(f"{OVERVIEW}?month={last.year}-{last.month:02d}")
        self.assertContains(page, last.strftime("%B %Y"))
        self.assertContains(self.client.get(f"{OVERVIEW}?month=all"), "All time")

    def test_the_spreadsheet(self):
        self.fill()
        response = self.client.get(f"{OVERVIEW}?format=csv")
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        text = response.content.decode()
        self.assertIn("income,Stars payment from", text)
        self.assertIn(",refund,", text)
        self.assertIn("AI cost,OpenAI: 2 calls,-0.60", text)
        self.assertIn("cost,Server Hetzner,-5.75,5.00 EUR", text)
        self.assertIn("paid to AI provider,OpenAI Top-up,-20.00,20.00 USD", text)

    def test_openai_s_own_figure_with_an_admin_key(self):
        from apps.ai.models import Provider

        Provider.objects.filter(kind="openai").update(admin_key="sk-admin")
        answer = mock.Mock(status_code=200)
        answer.json.return_value = {"data": [
            {"results": [{"amount": {"value": 0.25, "currency": "usd"}}]},
            {"results": [{"amount": {"value": 0.5, "currency": "usd"}}]}], "next_page": None}
        with mock.patch("apps.ai.clients.requests.get", return_value=answer) as get:
            page = self.client.get(OVERVIEW)
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"], "Bearer sk-admin")
        self.assertContains(page, "OpenAI's own figure for this period")
        self.assertContains(page, "$0.7500")

    def test_only_superusers(self):
        clerk = User.objects.create_user("clerk", password="pw", is_staff=True)
        self.client.force_login(clerk)
        self.assertEqual(self.client.get(OVERVIEW).status_code, 403)
        self.assertNotContains(self.client.get("/admin/"), "My finances")


class AICallTests(TestCase):
    def test_every_provider_call_is_recorded(self):
        from apps.ai import clients
        from apps.ai.models import AIModel, Provider

        Provider.objects.update(api_key="sk-test")
        luna = AIModel.objects.get(model_id="gpt-6-luna")
        answer = mock.Mock(status_code=200)
        answer.json.return_value = {"status": "completed", "output": [
            {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 1000, "output_tokens": 200}}
        with mock.patch("apps.ai.clients.requests.post", return_value=answer):
            clients.ask(luna, "s", "p", purpose="translation")
            clients.ask(luna, "s", "p", purpose="something_new")
        first, second = AICall.objects.order_by("id")
        self.assertEqual((first.provider, first.model, first.purpose, first.cost_micros,
                          first.input_tokens, first.output_tokens),
                         ("openai", "gpt-6-luna", "translation", 200, 1000, 200))
        self.assertEqual(second.purpose, "other")


class InvoiceTests(MoneyTestCase):
    def test_upload_and_open_it(self):
        response = self.client.post("/admin/finance/bill/add/", {
            "date": timezone.localdate().isoformat(), "payee": "server",
            "description": "Hetzner October", "amount": "5.83", "currency": "EUR",
            "invoice_number": "R-1", "paid": "on",
            "invoice": SimpleUploadedFile("invoice.pdf", b"%PDF-1.7 invoice",
                                          content_type="application/pdf")})
        self.assertEqual(response.status_code, 302)
        bill = Bill.objects.get()
        self.assertTrue(bill.invoice.name.startswith("invoices/"))
        opened = self.client.get(f"/admin/finance/bill/{bill.pk}/invoice/")
        self.assertEqual(b"".join(opened.streaming_content), b"%PDF-1.7 invoice")
        self.assertContains(self.client.get("/admin/finance/bill/"), "open")

    def test_not_for_other_staff(self):
        bill = Bill.objects.create(payee="server", amount=Decimal("5"),
                                   invoice=SimpleUploadedFile("a.pdf", b"x"))
        clerk = User.objects.create_user("clerk", password="pw", is_staff=True)
        self.client.force_login(clerk)
        self.assertEqual(self.client.get(f"/admin/finance/bill/{bill.pk}/invoice/").status_code,
                         403)

    def test_never_public(self):
        self.client.logout()
        response = self.client.get("/media/invoices/a.pdf")
        self.assertEqual(response.status_code, 404)


class StarsTests(MoneyTestCase):
    def test_the_balance_and_the_transactions(self):
        def fake(method, **payload):
            if method == "getMyStarBalance":
                return {"amount": 1500, "nanostar_amount": 0}
            if method == "getStarTransactions":
                return {"transactions": [
                    {"id": "t1", "amount": 250, "date": 1792000000,
                     "source": {"type": "user", "user": {"id": 42, "username": "amir"}}},
                    {"id": "t2", "amount": 1000, "date": 1792100000,
                     "receiver": {"type": "fragment",
                                  "withdrawal_state": {"type": "succeeded"}}}]}
            return None

        with mock.patch("apps.core.services.telegram._call", side_effect=fake):
            page = self.client.get("/admin/finance/starsaccount/")
        text = page.content.decode()
        self.assertIn("⭐ 1,500", text)
        self.assertIn("$19.50", text)
        self.assertIn("Payment from @amir", text)
        self.assertIn("Withdrawal (Fragment): succeeded", text)
        self.assertIn("-1,000", text)

    def test_telegram_silent(self):
        with mock.patch("apps.core.services.telegram._call", return_value=None):
            self.assertContains(self.client.get("/admin/finance/starsaccount/"),
                                "Telegram did not answer.")


@runtime.override(ADMIN_CHAT_ID=555000111)
class TaskTests(MoneyTestCase):
    def test_a_low_balance_is_told_once_a_day(self):
        Bill.objects.create(payee="openai", amount=Decimal("12"))
        call("openai", "chat", 5)
        with mock.patch("apps.core.services.telegram._call") as send:
            self.assertEqual(tasks.warn_low_balances(), 1)
            self.assertEqual(tasks.warn_low_balances(), 0)
        method, payload = send.call_args.args[0], send.call_args.kwargs
        self.assertEqual((method, payload["chat_id"]), ("sendMessage", 555000111))
        self.assertIn("about $7.00 left of what you paid", payload["text"])

    def test_enough_left_says_nothing(self):
        Bill.objects.create(payee="openai", amount=Decimal("50"))
        with mock.patch("apps.core.services.telegram._call") as send:
            self.assertEqual(tasks.warn_low_balances(), 0)
        send.assert_not_called()

    def test_monthly_bills_get_this_month_s_copy_once(self):
        today = timezone.localdate()
        last_month = today.replace(day=1) - timedelta(days=1)
        server = Bill.objects.create(payee="server", amount=Decimal("5.83"), currency="EUR",
                                     description="Hetzner", repeats_monthly=True,
                                     date=last_month.replace(day=1))
        self.assertEqual(tasks.repeat_monthly_bills(), 1)
        self.assertEqual(tasks.repeat_monthly_bills(), 0)
        copy = Bill.objects.get(repeat_of=server)
        self.assertEqual((copy.date, copy.paid, copy.amount, copy.description),
                         (today.replace(day=1), False, Decimal("5.83"), "Hetzner"))
