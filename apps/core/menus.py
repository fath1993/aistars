"""
Bot copy and keyboards, in one place so wording changes never touch routing.

Every word a user sees is written in English inside `_()` and translated by
AI into the database (i18n.py, translation.py). Functions, not constants: the
text is looked up when it is sent, in the language active for that user
(languages.activate_for). HTML stays out of the translated text, so a
translation can never break Telegram's parsing.

Navigation is inline buttons on the single panel message (see services/ui.py).
Every screen but home ends with ‹ Back · 🏠 Menu; other messages with
🏠 Menu. Callback data is `verb:argument`.
"""

import json

from django.utils import formats, timezone
from . import languages
from .i18n import gettext as _
from .services.telegram import esc

BRAND = "AIstars"


def _inline(rows):
    return json.dumps({"inline_keyboard": rows})


def _bold(text):
    return f"<b>{esc(text)}</b>"


# --------------------------------------------------------------------------
# Home and help
# --------------------------------------------------------------------------

def home_text(account=None) -> str:
    """With the AI tools for those who may use them, else "coming soon"."""
    from apps.studio.bot import home_lines, tools

    lines = home_lines(account) or [
        _("The AI tools are being switched on and will appear on this panel.")]
    functions = tools(account)
    everything = not functions or ("chat" in functions and "image" in functions and (
        "transcription" in functions or "speech" in functions))
    return "\n\n".join([
        _bold(BRAND),
        esc(_("Chat with AI, create images and turn voice into text, right here in "
              "Telegram.") if everything else _("AI tools, right here in Telegram.")),
        *lines,
    ])


def help_text(account=None) -> str:
    """How it works, for what the user's AI provider offers (everything before the tools open)."""
    from apps.studio.bot import help_facts
    from apps.studio.texts import things_text

    facts = help_facts(account)
    functions, reads, providers = facts or (
        ("chat", "image", "transcription", "speech"), ["photos", "pdf", "office", "text"], False)
    talks = "chat" in functions
    stt, tts = "transcription" in functions, "speech" in functions
    steps = []
    if talks:
        things = list(reads) + (["voice"] if stt else [])
        step = _("Just type to chat with the AI.")
        if things:
            step += " " + _("You can send %(things)s too; several at once as one album, with "
                            "your question as its caption.") % {"things": things_text(things)}
        steps += [step, _("Agents: AI assistants made for one job (a lawyer, a teacher, a "
                          "translator, a programmer…). Browse them by category, search, or let "
                          "the AI find one for you.")]
    if "image" in functions:
        steps.append(_("Images: describe the image you want, or send a photo to change."))
    if stt and tts:
        steps.append(_("Voice: turn a voice note into text, or a text into speech."))
    elif stt or tts:
        steps.append(_("Voice: turn a voice note into text.") if stt
                     else _("Voice: turn a text into speech."))
    steps += [
        _("Every answer shows what it cost and your balance; 🧾 Details shows the tokens. "
          "You only pay what a request really costs, and a big one asks you first."),
        _("📊 Usage shows what you spent by day, tool, agent and model. You can set a daily "
          "limit there."),
    ]
    if talks:
        steps += [
            _("Long chats keep a memory: the newest messages go to the AI word for word, older "
              "ones as a summary the AI writes, so it does not forget and the price stays even. "
              "A file goes again with your next few messages; close it on the chat screen."),
            _("👤 Profile → 📝 About me: a few lines about you that every chat gets."),
        ]
    if providers:
        steps.append(_("👤 Profile → 🧠 AI provider: the AI company that answers you. Each "
                       "offers different tools; the menu shows what yours offers."))
    commands = ([("/new", _("a new chat")), ("/agents", _("the agents")),
                 ("/chats", _("your chats"))] if talks else []) + [
        ("/usage", _("your usage")), ("/privacy", _("what is kept")),
        ("/menu", _("this menu"))]
    steps.append(_("Commands") + ": " + ", ".join(f"{command} {what}"
                                                   for command, what in commands) + ".")
    lines = [f"{number}. {esc(step)}" for number, step in enumerate(steps, 1)]
    return _bold(_("How it works")) + "\n\n" + "\n".join(lines)


def home_keyboard(account=None):
    from apps.billing.texts import credits_button
    from apps.studio.bot import account_buttons, home_rows

    return _inline([
        *home_rows(account),
        [credits_button(), *account_buttons(account)],
        [{"text": "👤 " + _("Profile"), "callback_data": "menu:profile"},
         {"text": "❓ " + _("Help"), "callback_data": "menu:help"}],
    ])


def back_button(target="menu:home"):
    # Translators: a button that returns to the previous screen.
    return {"text": "‹ " + _("Back"), "callback_data": target}


def menu_button(new=False):
    """
    To the home panel. On a screen it is edited in place (menu:home); on a
    message that is not the panel (a result, a notice) the message stays and
    the home panel is sent under it (menu:new).
    """
    # Translators: a button that opens the main menu.
    return {"text": "🏠 " + _("Menu"), "callback_data": "menu:new" if new else "menu:home"}


def nav_row(back_to="menu:home"):
    """The last row of every screen but home: the user is never stuck."""
    return [back_button(back_to), menu_button()]


def back_keyboard(back_to="menu:home"):
    return _inline([nav_row(back_to)])


def notice_keyboard():
    """For a message that is not the panel: the way back to the menu."""
    return _inline([[menu_button(new=True)]])


# --------------------------------------------------------------------------
# One-line replies
# --------------------------------------------------------------------------

def blocked_text() -> str:
    return _("This account has been blocked.")


def unknown_command_text() -> str:
    return _("I don't know that command. Use the buttons on the panel, or just type to chat.")


def expired_button_text() -> str:
    return _("That button has expired.")


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------

def register_text() -> str:
    return "\n\n".join([
        _bold(_("Welcome to AIstars")),
        esc(_("To start, share your phone number with the button below. Telegram "
              "asks you to confirm, and only your own number is accepted.")),
    ])


def register_keyboard():
    """A reply keyboard: only that kind of button can ask for the contact."""
    return json.dumps({
        "keyboard": [[{"text": "📱 " + _("Share my phone number"), "request_contact": True}]],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    })


def remove_keyboard():
    return json.dumps({"remove_keyboard": True})


def not_own_number_text() -> str:
    return _("That is not your number. Use the button below to share your own.")


def register_first_text() -> str:
    return _("Share your phone number first.")


def registered_text(gift=0) -> str:
    text = "✅ " + _("Thank you, you are registered.")
    if gift:
        from apps.billing.texts import coins

        text += "\n🎁 " + _("A welcome gift: %(credits)s to try everything.") % {
            "credits": coins(gift)}
    return text


def region_text() -> str:
    return _("Sorry, AIstars is not available in your region.")


# --------------------------------------------------------------------------
# Profile and language
# --------------------------------------------------------------------------

def profile_text(account) -> str:
    name = account.display_name or account.username or "—"
    joined = formats.date_format(timezone.localtime(account.created_at), "DATE_FORMAT")
    rows = [
        (_("Name"), esc(name)),
        (_("Telegram ID"), f"<code>{account.telegram_user_id}</code>"),
        (_("Phone"), esc(account.phone or "—")),
        (_("Country"), esc(account.country or "—")),
        (_("Language"), esc(languages.name(languages.resolve(account)))),
        (_("Member since"), esc(joined)),
    ]
    lines = [f"{esc(label)}: {value}" for label, value in rows]
    return _bold(_("Profile")) + "\n\n" + "\n".join(lines)


def profile_keyboard(account=None):
    from apps.studio.bot import profile_buttons, profile_rows

    return _inline([
        [{"text": "🌐 " + _("Language"), "callback_data": "lang:"},
         *profile_buttons(account)],
        *profile_rows(account),
        [{"text": "🔒 " + _("Privacy"), "callback_data": "menu:privacy"}],
        nav_row(),
    ])


def language_text() -> str:
    return _bold(_("Language")) + "\n\n" + esc(_("Choose the language of the bot."))


def language_keyboard(current, codes):
    """One button per used language, in its own name; the current one ticked."""
    names = languages.names(codes)
    rows = [[{"text": ("✓ " if code == current else "") + names[code],
              "callback_data": f"lang:{code}"}] for code in codes]
    rows.append(nav_row("menu:profile"))
    return _inline(rows)
