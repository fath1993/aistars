"""
Admin pages stay fast as the tables grow: no page runs a query per row, no
page scans a whole table for date links, and list selects load once.
"""
import itertools

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .helpers import FakeTelegram, register

_numbers = itertools.count(1)


def add_rows(count):
    """A few more rows in every growing table, linked the way the app links them."""
    from apps.ai.models import AIModel
    from apps.billing import ledger, services
    from apps.billing.models import CreditRequest, EntryKind, Payment
    from apps.core.models import ActivityLog, SpendLedger
    from apps.safety.models import Check, Screening
    from apps.studio.models import Conversation, Job, Message

    model = AIModel.objects.get(model_id="gpt-6-luna")
    for _ in range(count):
        number = next(_numbers)
        account = register(10_000 + number)
        ledger.post(account, EntryKind.ADMIN_ADJUST, 100, note="x")
        Payment.objects.create(account=account, stars=1, credits=1,
                               telegram_charge_id=f"charge-{number}")
        CreditRequest.objects.create(account=account, amount=5)
        screening = Screening.objects.create(account=account, model=model.model_id,
                                             tool="chat", prompt="x", result="blocked")
        Check.objects.create(screening=screening, layer="blocklist", verdict="block")
        conversation = Conversation.objects.create(account=account, model=model, title="x")
        job = Job.objects.create(account=account, kind="chat", model=model, hold=2, chat_id=1,
                                 conversation=conversation, screening=screening)
        services.hold(job)
        Message.objects.create(conversation=conversation, role="user", text="x", job=job)
        ActivityLog.objects.create(category="account", event="x", account=account)
        SpendLedger.objects.create(date=f"2026-01-{number % 28 + 1:02d}", hour=number % 24)


class AdminSpeedTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(user)
        patcher = FakeTelegram().patch()
        patcher.start()
        self.addCleanup(patcher.stop)

    def changelists(self):
        return [reverse(f"admin:{model._meta.app_label}_{model._meta.model_name}_changelist")
                for model in admin.site._registry]

    def queries(self):
        """Queries per changelist, after one warm-up pass (caches filled)."""
        urls = self.changelists()
        for url in urls:
            self.client.get(url)
        counts = {}
        for url in urls:
            with CaptureQueriesContext(connection) as ctx:
                self.assertEqual(self.client.get(url).status_code in (200, 302), True, url)
            counts[url] = len(ctx.captured_queries)
        return counts

    def test_no_page_runs_a_query_per_row(self):
        add_rows(2)
        before = self.queries()
        add_rows(6)
        after = self.queries()
        grew = {url: (before[url], after[url]) for url in before if after[url] != before[url]}
        self.assertEqual(grew, {}, "these pages run more queries as rows are added")

    def test_no_page_scans_a_table_for_date_links(self):
        # date_hierarchy reads every row of the table on every page view.
        with_links = [type(model_admin).__name__ for model_admin in admin.site._registry.values()
                      if model_admin.date_hierarchy]
        self.assertEqual(with_links, [])

    def test_the_country_list_loads_its_language_choices_once(self):
        url = reverse("admin:core_country_changelist")
        self.client.get(url)
        with CaptureQueriesContext(connection) as ctx:
            response = self.client.get(url)
        self.assertLess(len(ctx.captured_queries), 15)
        html = response.content.decode()
        rows = html.count('name="_selected_action"')
        self.assertEqual(rows, 100)
        # The used languages and the chosen defaults (en, ro, ru), not all 186.
        self.assertLess(html.count("<option"), rows * 6)
        self.assertNotIn("change-related", html)

    def test_a_default_language_that_is_not_used_stays_selectable(self):
        from apps.core.models import Country, Language

        Country.objects.filter(code="DE").update(default_language="de")
        response = self.client.get(reverse("admin:core_country_changelist") + "?q=Germany")
        self.assertContains(response, '<option value="de" selected>Deutsch</option>', html=False)
        self.assertFalse(Language.objects.get(code="de").used)

    def test_the_language_list_loads_its_model_choices_once(self):
        url = reverse("admin:core_language_changelist")
        self.client.get(url)
        with CaptureQueriesContext(connection) as ctx:
            self.client.get(url)
        self.assertLess(len(ctx.captured_queries), 15)
