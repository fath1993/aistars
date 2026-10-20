"""
A request's credits: held when it starts, charged what it really cost (never
more than held), all returned when it fails; once each, whatever arrives
twice; and a request that never finishes is given back by the sweep.
"""
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from apps.ai.models import AIModel
from apps.billing import ledger, services
from apps.billing import texts as billing_texts
from apps.billing.models import EntryKind, LedgerEntry
from apps.core.tests.helpers import FakeTelegram, register
from apps.studio import jobs
from apps.studio.models import Job, Kind, Status


class JobTestCase(TestCase):
    def setUp(self):
        self.tg = FakeTelegram()
        patcher = self.tg.patch()
        patcher.start()
        self.addCleanup(patcher.stop)
        self.account = register(42, tester=True)
        ledger.post(self.account, EntryKind.PURCHASE, 100)
        self.model = AIModel.objects.get(model_id="gpt-6.1-sol")

    def job(self, hold=10, status=Status.RUNNING):
        job = Job.objects.create(account=self.account, kind=Kind.CHAT, model=self.model,
                                 chat_id=42, hold=hold, status=status)
        services.hold(job)
        return job


class LedgerTests(JobTestCase):
    def test_hold_then_charge_returns_the_rest(self):
        job = self.job(hold=10)
        self.assertEqual(ledger.balance(self.account), 90)
        self.assertEqual(jobs.done(job, 20_000), 97)   # $0.02 → 🪙 3 of the 🪙 10 held
        job.refresh_from_db()
        self.assertEqual((job.status, job.charged, job.cost_micros), (Status.DONE, 3, 20_000))
        self.assertEqual(LedgerEntry.objects.get(kind=EntryKind.CHARGE).amount, 7)

    def test_never_more_than_the_hold(self):
        job = self.job(hold=2)
        jobs.done(job, 1_000_000)
        job.refresh_from_db()
        self.assertEqual((job.charged, ledger.balance(self.account)), (2, 98))

    def test_a_failure_returns_it_all(self):
        job = self.job(hold=10)
        self.assertTrue(jobs.failed(job, "boom"))
        self.assertEqual(ledger.balance(self.account), 100)
        self.assertEqual(Job.objects.get(pk=job.pk).status, Status.FAILED)

    def test_settled_once_whatever_arrives_twice(self):
        job = self.job(hold=10)
        jobs.done(job, 20_000)
        jobs.done(job, 20_000)
        self.assertFalse(jobs.failed(job, "late"))
        self.assertEqual(ledger.balance(self.account), 97)
        self.assertEqual(LedgerEntry.objects.filter(job=job).count(), 2)

    def test_start_refuses_more_than_the_balance(self):
        with self.assertRaises(jobs.NotEnough) as refused:
            jobs.start(self.account, 42, Kind.CHAT, self.model, 2_000_000)
        self.assertEqual(refused.exception.balance, 100)
        self.assertGreater(refused.exception.hold, 100)
        self.assertFalse(Job.objects.exists())

    def test_a_request_is_made_once_even_if_its_task_comes_twice(self):
        job = jobs.start(self.account, 42, Kind.CHAT, self.model, 1000)
        with mock.patch("apps.studio.chat.answer") as answer:
            self.assertEqual(jobs.run(job.pk), Status.RUNNING)
        self.assertEqual(jobs.run(job.pk), "not queued")
        answer.assert_called_once()


class StatementTests(JobTestCase):
    def lines(self):
        entries, _ = ledger.history(self.account)
        return [(billing_texts.entry_label(entry), billing_texts.entry_amount(entry))
                for entry in entries]

    def test_each_request_is_one_line_with_what_it_cost(self):
        running = self.job(hold=5)
        done = self.job(hold=10)
        jobs.done(done, 20_000)
        failed = self.job(hold=4)
        jobs.failed(failed, "x")
        self.assertEqual(self.lines(), [
            (f"Chat answer #{failed.pk} · not done, nothing charged", "<b>0</b> → 🪙 92"),
            (f"Chat answer #{done.pk}", "<b>−3</b> → 🪙 92"),
            (f"Chat answer #{running.pk} · in progress, held", "<b>−5</b> → 🪙 95"),
            ("Bought credits", "<b>+100</b> → 🪙 100"),
        ])

    def test_paging_counts_the_lines_shown(self):
        for _ in range(12):
            jobs.done(self.job(hold=2), 1000)
        first, has_older = ledger.history(self.account, page=0)
        self.assertEqual((len(first), has_older), (10, True))
        second, has_older = ledger.history(self.account, page=1)
        self.assertEqual((len(second), has_older), (3, False))


class SweepTests(JobTestCase):
    def test_a_request_that_never_finished_is_given_back_and_the_user_told(self):
        old = self.job(hold=10)
        Job.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(minutes=30),
                                             message_id=555)
        fresh = self.job(hold=5)
        self.assertEqual(jobs.sweep(), 1)
        self.assertEqual(Job.objects.get(pk=old.pk).status, Status.FAILED)
        self.assertEqual(Job.objects.get(pk=fresh.pk).status, Status.RUNNING)
        self.assertEqual(ledger.balance(self.account), 95)
        edit = self.tg.last("editMessageText")
        self.assertEqual(edit["message_id"], 555)
        self.assertIn("nothing was charged", edit["text"])

    def test_the_task_runs_the_sweep(self):
        from apps.studio import tasks

        self.assertEqual(tasks.sweep_jobs(), 0)


class AdminTests(JobTestCase):
    def setUp(self):
        super().setUp()
        user = get_user_model().objects.create_superuser("owner", "o@example.com", "pw")
        self.client.force_login(user)

    def test_the_pages_load(self):
        job = self.job()
        for url in ("/admin/studio/job/", f"/admin/studio/job/{job.pk}/change/",
                    "/admin/studio/assistant/", "/admin/studio/conversation/",
                    "/admin/studio/assistant/1/change/"):
            self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_a_stuck_request_can_be_given_back(self):
        job = self.job(hold=10)
        response = self.client.post("/admin/studio/job/", {
            "action": "give_back", "_selected_action": [job.pk]}, follow=True)
        self.assertContains(response, "1 request(s) stopped")
        self.assertEqual(ledger.balance(self.account), 100)
        self.assertEqual(LedgerEntry.objects.get(kind=EntryKind.RETURN).created_by, "owner")
