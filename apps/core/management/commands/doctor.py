"""
Console rendering of apps.core.diagnostics.

The checks live in that module so the admin page and this command cannot
drift apart. dp.sh runs this with --strict after every deploy.
"""

import sys

from django.core.management.base import BaseCommand

from apps.core import diagnostics, runtime

_COLOUR = sys.stdout.isatty()
BADGE = {
    diagnostics.OK: "\033[32m  ok  \033[0m" if _COLOUR else "  ok  ",
    diagnostics.WARN: "\033[33m warn \033[0m" if _COLOUR else " warn ",
    diagnostics.FAIL: "\033[31m FAIL \033[0m" if _COLOUR else " FAIL ",
}


class Command(BaseCommand):
    help = "Diagnose why the Telegram bot is not responding."

    def add_arguments(self, parser):
        parser.add_argument(
            "--set-webhook", nargs="?", const="", metavar="URL",
            help="Register the webhook. With no URL it is built from "
                 "PUBLIC_BASE_URL. The same button exists in the admin.")
        parser.add_argument("--strict", action="store_true",
                            help="Exit non-zero when something is blocking.")

    def handle(self, *args, **options):
        if options.get("set_webhook") is not None:
            ok, note = diagnostics.register_webhook(options["set_webhook"])
            self.stdout.write((self.style.SUCCESS if ok else self.style.ERROR)(note))

        report = diagnostics.run()

        self.stdout.write("")
        for section, checks in report.sections().items():
            self.stdout.write(self.style.MIGRATE_HEADING(
                f"-- {section} " + "-" * max(2, 52 - len(section))))
            for check in checks:
                self.stdout.write(
                    f"[{BADGE[check.status]}] {check.label:<26} {check.detail}")
                if check.fix:
                    self.stdout.write(f"          -> {check.fix}")

        self.stdout.write("")
        if report.failures:
            self.stdout.write(self.style.ERROR(
                f"{len(report.failures)} blocking problem(s):"))
            for check in report.failures:
                self.stdout.write(f"  - {check.label}: {check.fix or check.detail}")
            self.stdout.write("")
            self.stdout.write("Fix these in the admin: "
                              f"{runtime.get('PUBLIC_BASE_URL') or ''}"
                              "/admin/core/controlpanel/diagnostics/")
        elif report.warnings:
            self.stdout.write(self.style.WARNING("Nothing blocking, but worth a look:"))
            for check in report.warnings:
                self.stdout.write(f"  - {check.label}: {check.detail}")
        else:
            self.stdout.write(self.style.SUCCESS("Everything checks out."))
        self.stdout.write("")

        if options.get("strict") and report.failures:
            raise SystemExit(1)
