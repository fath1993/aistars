"""
Every word of the credits screens, in English inside `_()`; translated by AI
like the rest of the bot (apps/core/i18n.py). No number agreement: amounts
appear as "🪙 120" or after a label, never as "120 credits".
"""

import json

from django.utils import formats, timezone

from apps.core.i18n import gettext as _
from apps.core.menus import nav_row
from apps.core.services.telegram import esc

from .models import HOLD_KINDS, EntryKind

COIN = "🪙"
STAR = "⭐"


def _bold(text):
    return f"<b>{esc(text)}</b>"


def _inline(rows):
    return json.dumps({"inline_keyboard": rows})


def coins(amount) -> str:
    return f"{COIN} {amount}"


def signed(amount) -> str:
    return f"+{amount}" if amount > 0 else f"−{abs(amount)}"


# --------------------------------------------------------------------------
# Credits screen
# --------------------------------------------------------------------------

def credits_button():
    return {"text": f"{COIN} " + _("Credits"), "callback_data": "credits:"}


def credits_text(balance) -> str:
    return "\n\n".join([
        _bold(_("Credits")),
        f"{esc(_('Balance'))}: <b>{coins(balance)}</b>",
        f"<i>{esc(_('Every answer and result shows what it cost. You only pay what a '
                   'request really costs.'))}</i>",
    ])


def credits_keyboard():
    return _inline([
        [{"text": f"{STAR} " + _("Buy credits"), "callback_data": "credits:buy"}],
        [{"text": "🧾 " + _("Statement"), "callback_data": "credits:statement:0"},
         {"text": "✍️ " + _("Request credit"), "callback_data": "credits:request"}],
        nav_row(),
    ])


# --------------------------------------------------------------------------
# Buying
# --------------------------------------------------------------------------

def pack_label(pack) -> str:
    return f"{coins(pack.credits)} = {STAR} {pack.stars}"


def packs_text(packs) -> str:
    if not packs:
        return _bold(_("Buy credits")) + "\n\n" + esc(_("No packs are on sale right now."))
    return "\n\n".join([
        _bold(_("Buy credits")),
        esc(_("Pay with Telegram Stars. The credits arrive at once.")),
    ])


def packs_keyboard(packs):
    rows = [[{"text": pack_label(pack), "callback_data": f"credits:pack:{pack.pk}"}]
            for pack in packs]
    rows.append(nav_row("credits:"))
    return _inline(rows)


def invoice_title() -> str:
    return _("AIstars credits")


def invoice_description(pack) -> str:
    return f"{_('Credits for chat, images and voice in AIstars.')} {pack_label(pack)}"


def pack_changed_text() -> str:
    return _("This pack has changed. Please choose it again.")


def cannot_buy_text() -> str:
    return _("Buying credits is not possible for this account right now.")


def receipt_text(payment, balance) -> str:
    rows = [
        (_("Paid"), f"{STAR} {payment.stars}"),
        (_("Credits added"), coins(payment.credits)),
        (_("Balance"), coins(balance)),
        (_("Charge ID"), f"<code>{esc(payment.telegram_charge_id)}</code>"),
    ]
    lines = [f"{esc(label)}: {value}" for label, value in rows]
    return ("✅ " + _bold(_("Payment received")) + "\n\n" + "\n".join(lines) + "\n\n"
            + f"<i>{esc(_('Keep the charge ID in case you need /paysupport.'))}</i>")


def refunded_text(payment, balance) -> str:
    return (_bold(_("Payment refunded")) + "\n\n"
            + f"{esc(_('Refunded'))}: {STAR} {payment.stars}\n"
            + f"{esc(_('Credits removed'))}: {coins(payment.credits)}\n"
            + f"{esc(_('Balance'))}: {coins(balance)}")


# --------------------------------------------------------------------------
# Statement
# --------------------------------------------------------------------------

def entry_label(entry) -> str:
    if entry.kind in HOLD_KINDS:
        from apps.studio.texts import job_name

        name = f"{job_name(entry.job)} #{entry.job_id}"
        if entry.kind == EntryKind.HOLD:
            return f"{name} · {_('in progress, held')}"
        if entry.kind == EntryKind.RETURN:
            return f"{name} · {_('not done, nothing charged')}"
        return name
    return {
        EntryKind.PURCHASE: _("Bought credits"),
        EntryKind.MANUAL_GRANT: _("Credit request approved"),
        EntryKind.ADMIN_ADJUST: _("Adjusted by support"),
        EntryKind.REFUND: _("Payment refunded"),
    }.get(entry.kind, entry.kind)


def entry_amount(entry) -> str:
    """
    '+100 → 🪙 100'. A done request shows what it cost (the hold, less what
    came back); one not done shows 0: its hold came back whole.
    """
    if entry.kind == EntryKind.CHARGE:
        return f"<b>{signed(-entry.job.charged) if entry.job.charged else '0'}</b> → " \
               f"{coins(entry.balance_after)}"
    if entry.kind == EntryKind.RETURN:
        return f"<b>0</b> → {coins(entry.balance_after)}"
    return f"<b>{signed(entry.amount)}</b> → {coins(entry.balance_after)}"


def statement_text(entries, page) -> str:
    title = _bold(_("Statement"))
    if not entries:
        return title + "\n\n" + esc(_("No credit movements yet."))
    lines = []
    for entry in entries:
        day = formats.date_format(timezone.localtime(entry.created_at), "SHORT_DATE_FORMAT")
        lines.append(f"{esc(day)} · {esc(entry_label(entry))}\n{entry_amount(entry)}")
    return title + "\n\n" + "\n\n".join(lines)


def statement_keyboard(page, has_older):
    nav = []
    if page > 0:
        nav.append({"text": "‹ " + _("Newer"), "callback_data": f"credits:statement:{page - 1}"})
    if has_older:
        nav.append({"text": _("Older") + " ›", "callback_data": f"credits:statement:{page + 1}"})
    rows = [nav] if nav else []
    rows.append(nav_row("credits:"))
    return _inline(rows)


# --------------------------------------------------------------------------
# Credit requests
# --------------------------------------------------------------------------

def ask_amount_text() -> str:
    return "\n\n".join([
        _bold(_("Request credit")),
        esc(_("If you cannot pay with Telegram Stars, ask for credits and we review it.")),
        esc(_("How many credits do you need? Send a number.")),
    ])


def bad_amount_text() -> str:
    return _("Please send a whole number between 1 and 100000.")


def ask_note_text() -> str:
    return _("Add a short note: what the credits are for, and how you will pay us.")


def request_sent_text(request) -> str:
    return ("✅ " + _bold(_("Request sent")) + "\n\n"
            + f"{esc(_('Requested'))}: {coins(request.amount)}\n\n"
            + esc(_("We will tell you here when it is decided.")))


def request_pending_text() -> str:
    return _("You already have a request waiting for a decision.")


def request_approved_text(granted, balance) -> str:
    return ("✅ " + _bold(_("Credit request approved")) + "\n\n"
            + f"{esc(_('Credits added'))}: {coins(granted)}\n"
            + f"{esc(_('Balance'))}: {coins(balance)}")


def request_rejected_text() -> str:
    return _bold(_("Credit request declined")) + "\n\n" + esc(
        _("Your credit request was not approved. Write to /support if you have questions."))


def request_keyboard():
    """The amount and note questions: Back to Credits ends the request."""
    return _inline([nav_row("credits:")])


# --------------------------------------------------------------------------
# Commands Telegram requires of bots that take Stars
# --------------------------------------------------------------------------

def terms_text() -> str:
    points = [
        _("Credits are bought with Telegram Stars inside this bot."),
        _("When a request starts, the most it can cost is held. When it is done you are "
          "charged what it really cost and the rest comes back; a request that fails costs "
          "nothing. Images and texts read aloud show their price first."),
        _("Credits are not money and cannot be exchanged back. For a problem with a "
          "payment, use /paysupport."),
        _("Requests are checked for safety. Forbidden requests are refused, and repeated "
          "attempts can block the account."),
        _("Send only photos and voice notes you have the right to use. AI answers can be "
          "wrong: check anything important."),
        _("Your chats are kept so the AI remembers them. You can delete a chat at any time."),
    ]
    lines = [f"{number}. {esc(point)}" for number, point in enumerate(points, 1)]
    return _bold(_("Terms")) + "\n\n" + "\n".join(lines)


def paysupport_text(contact) -> str:
    if not contact:
        return _("Payment support is not set up yet. Please try again later.")
    return _bold(_("Payment support")) + "\n\n" + esc(
        _("For a problem with a payment, write to %(contact)s and include the charge ID "
          "from your receipt.") % {"contact": contact})


def support_text(contact) -> str:
    if not contact:
        return _("Support is not set up yet. Please try again later.")
    return _bold(_("Support")) + "\n\n" + esc(
        _("Questions or problems: write to %(contact)s.") % {"contact": contact})
