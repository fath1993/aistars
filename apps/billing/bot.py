"""
The credits part of the bot: the Credits screen, buying with Stars, the
statement, credit requests, and the commands Telegram requires of bots that
take Stars (/terms, /paysupport, /support).

Called from apps.core.views inside the user's language.
"""

from django.core.cache import cache

from apps.core import menus, runtime
from apps.core.services import telegram, ui

from . import ledger, services, texts
from .models import Pack

INPUT_TTL = 15 * 60   # an unanswered "how many credits?" expires


# --------------------------------------------------------------------------
# Typed answers (the credit request conversation)
# --------------------------------------------------------------------------

def _input_key(account):
    return f"billing:input:{account.pk}"


def cancel_input(account):
    cache.delete(_input_key(account))


def handle_text(account, chat_id, text) -> bool:
    """A typed answer to a question the bot asked; False if none was asked."""
    state = cache.get(_input_key(account))
    if not state:
        return False
    if state["step"] == "amount":
        amount = int(text) if text.isdigit() else 0
        if not 1 <= amount <= services.MAX_REQUEST:
            telegram.send_message(chat_id, texts.bad_amount_text())
            return True
        cache.set(_input_key(account), {"step": "note", "amount": amount}, INPUT_TTL)
        ui.fresh_screen(account, chat_id, texts.ask_note_text(), texts.request_keyboard())
        return True

    cancel_input(account)
    if services.has_pending_request(account):
        ui.fresh_screen(account, chat_id, texts.request_pending_text(),
                        texts.credits_keyboard())
        return True
    request = services.create_request(account, state["amount"], text)
    ui.fresh_screen(account, chat_id, texts.request_sent_text(request),
                    texts.credits_keyboard())
    _tell_admin(request)
    return True


def _tell_admin(request):
    """The owner's notice, in English: the admin speaks English."""
    chat_id = runtime.get("ADMIN_CHAT_ID")
    if not chat_id:
        return
    account = request.account
    base = str(runtime.get("PUBLIC_BASE_URL") or "").rstrip("/")
    lines = [
        "<b>New credit request</b>",
        f"From: {telegram.esc(account.display_name or account.username or account.telegram_user_id)}"
        f" ({telegram.esc(account.phone or 'no phone')}, {telegram.esc(account.country or '—')})",
        f"Amount: 🪙 {request.amount}",
        f"Note: {telegram.esc(request.note or '—')}",
    ]
    if base:
        lines.append(f"{base}/admin/billing/creditrequest/{request.pk}/change/")
    telegram.send_message(chat_id, "\n".join(lines))


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def handle_command(account, chat_id, command) -> bool:
    if command == "credits":
        show_credits(account, chat_id, fresh=True)
    elif command == "terms":
        _notice(chat_id, texts.terms_text())
    elif command == "paysupport":
        _notice(chat_id, texts.paysupport_text(runtime.get("SUPPORT_CONTACT")))
    elif command == "support":
        _notice(chat_id, texts.support_text(runtime.get("SUPPORT_CONTACT")))
    else:
        return False
    return True


def _notice(chat_id, text):
    telegram.send_message(chat_id, text, reply_markup=menus.notice_keyboard())


# --------------------------------------------------------------------------
# Buttons: credits:<action>[:<argument>]
# --------------------------------------------------------------------------

def handle_callback(account, chat_id, argument):
    action, _, value = argument.partition(":")
    if action != "request":
        cancel_input(account)
    if action == "buy":
        packs = list(Pack.objects.filter(active=True))
        ui.screen(account, chat_id, texts.packs_text(packs), texts.packs_keyboard(packs))
    elif action == "pack":
        _send_invoice(account, chat_id, value)
    elif action == "statement":
        page = int(value) if value.isdigit() else 0
        entries, has_older = ledger.history(account, page)
        ui.screen(account, chat_id, texts.statement_text(entries, page),
                  texts.statement_keyboard(page, has_older))
    elif action == "request":
        _start_request(account, chat_id)
    else:
        show_credits(account, chat_id)


def show_credits(account, chat_id, fresh=False):
    show = ui.fresh_screen if fresh else ui.screen
    show(account, chat_id, texts.credits_text(ledger.balance(account)),
         texts.credits_keyboard())


def _send_invoice(account, chat_id, pack_id):
    pack = Pack.objects.filter(pk=int(pack_id) if pack_id.isdigit() else 0,
                               active=True).first()
    if pack is None:
        packs = list(Pack.objects.filter(active=True))
        ui.screen(account, chat_id, texts.packs_text(packs), texts.packs_keyboard(packs))
        return
    telegram.send_invoice(chat_id, texts.invoice_title(), texts.invoice_description(pack),
                          services.invoice_payload(pack), pack.stars,
                          texts.pack_label(pack))


def _start_request(account, chat_id):
    if services.has_pending_request(account):
        ui.screen(account, chat_id, texts.request_pending_text(), texts.credits_keyboard())
        return
    cache.set(_input_key(account), {"step": "amount"}, INPUT_TTL)
    ui.screen(account, chat_id, texts.ask_amount_text(), texts.request_keyboard())


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------

def handle_pre_checkout(account, query):
    """Telegram waits at most 10 seconds for this answer, then cancels."""
    from apps.core import activity
    from apps.core.models import LogCategory

    problem = services.pre_checkout_problem(account, query.get("invoice_payload"),
                                            query.get("currency"), query.get("total_amount"))
    if problem:
        activity.warn(LogCategory.BILLING, "pre_checkout_refused",
                      f"{account}: {problem}", account=account,
                      detail={"payload": query.get("invoice_payload")})
        message = texts.pack_changed_text() if problem == "pack" else texts.cannot_buy_text()
        telegram.answer_pre_checkout_query(query["id"], ok=False, error_message=message)
        return
    telegram.answer_pre_checkout_query(query["id"], ok=True)


def handle_successful_payment(account, message):
    """Credit it (once), then the receipt and the Credits screen."""
    payment, created = services.record_payment(account, message["successful_payment"])
    if not created:
        return
    chat_id = message["chat"]["id"]
    _notice(chat_id, texts.receipt_text(payment, ledger.balance(account)))
    show_credits(account, chat_id, fresh=True)
