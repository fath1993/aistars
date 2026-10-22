"""
Every word of the AI tools, in English inside `_()`; translated by AI like
the rest of the bot (apps/core/i18n.py). Database texts users see (model
taglines, assistants) go through catalog.tr. No number agreement: amounts
appear as "💎 3" or after a label.

Callback data: panel screens use chat:, ag: (agents), img:, voice: and use:
(usage). Buttons on messages that are not the panel (an answer, an image, a
notice) use out:, so tapping them never turns that message into the panel.
Where a screen sits is shown as a trail: 🧩 Agents › ⚖️ Law.
"""

import json
import math

from django.utils import formats, timezone

from apps.billing.texts import coin, coins
from apps.core import runtime
from apps.core.i18n import gettext as _
from apps.core.menus import menu_button, nav_row
from apps.core.services.telegram import esc

from . import catalog
from .models import Kind, Status

VOICES = ("marin", "cedar", "coral", "nova", "onyx", "sage", "alloy", "echo")
SIZES = ("1024x1024", "1024x1536", "1536x1024")
QUALITIES = ("low", "medium", "high")


def _bold(text):
    return f"<b>{esc(text)}</b>"


def _inline(rows):
    return json.dumps({"inline_keyboard": rows})


def _tick(on, label):
    return ("✓ " if on else "") + label


# --------------------------------------------------------------------------
# Names users read
# --------------------------------------------------------------------------

def job_name(job) -> str:
    """What a request is called on the statement."""
    if job is None:
        return _("Request")
    return {
        Kind.CHAT: _("Chat answer"),
        Kind.IMAGE: _("Image"),
        Kind.EDIT: _("Photo edit"),
        Kind.TRANSCRIBE: _("Voice to text"),
        Kind.SPEECH: _("Text to speech"),
    }.get(job.kind, _("Request"))


def model_line(model) -> str:
    tagline = catalog.tr(model.tagline)
    return f"<b>{esc(model)}</b>" + (f" — {esc(tagline)}" if tagline else "")


def size_name(size) -> str:
    return {"1024x1024": "⬜ " + _("Square"), "1024x1536": "▯ " + _("Portrait"),
            "1536x1024": "▭ " + _("Landscape"), "auto": "🪄 " + _("As the photo")}.get(size, size)


def quality_name(quality) -> str:
    return {"low": _("Quick draft"), "medium": _("Good"), "high": _("Best")}.get(quality, quality)


def voice_name(voice) -> str:
    return voice.capitalize()


def low_balance(balance) -> bool:
    return balance < int(runtime.get("LOW_BALANCE_CREDITS"))


def footer(charged, balance, model=None) -> str:
    """Under every answer and result: what it cost, and what is left."""
    parts = [coins(charged)]
    if model is not None:
        parts.append(esc(model))
    parts.append(f"{esc(_('Balance'))} {coins(balance)}")
    text = "<i>" + " · ".join(parts) + "</i>"
    if low_balance(balance):
        text += "\n⚠️ " + esc(_("Your balance is low."))
    return text


def number(value) -> str:
    return formats.number_format(value, force_grouping=True)


def price_line(hold, balance) -> str:
    return "\n".join([
        f"{esc(_('Price'))}: <b>{esc(_('at most'))} {coins(hold)}</b>",
        f"<i>{esc(_('You pay what it really costs, never more.'))}</i>",
        f"{esc(_('Balance'))}: {coins(balance)}",
    ])


# --------------------------------------------------------------------------
# Home
# --------------------------------------------------------------------------

def home_lines(conversation, model, balance) -> list:
    lines = ["💬 " + esc(_("Just type to chat, or pick an agent made for your task."))]
    agent = conversation.assistant if conversation is not None and conversation.assistant_id \
        else None
    if agent is not None:
        lines.append(f"{esc(_('Agent'))}: {esc(agent_title(agent))}")
    if model is not None:
        lines.append(f"{esc(_('Model'))}: {esc(model)}")
    lines.append(f"{esc(_('Balance'))}: <b>{coins(balance)}</b>")
    return lines


def home_rows(can_continue) -> list:
    first = [{"text": "➕ " + _("New chat"), "callback_data": "chat:new"}]
    if can_continue:
        first.append({"text": "▶️ " + _("Continue"), "callback_data": "chat:"})
    return [
        first,
        [{"text": "🧩 " + _("Agents"), "callback_data": "ag:"},
         {"text": "🎨 " + _("Images"), "callback_data": "img:"},
         {"text": "🎙 " + _("Voice"), "callback_data": "voice:"}],
    ]


def usage_button():
    return {"text": "📊 " + _("Usage"), "callback_data": "use:"}


def notice_keyboard():
    return _inline([[menu_button(new=True)]])


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------

def _agent_of(conversation):
    return conversation.assistant if conversation is not None and conversation.assistant_id \
        else None


def chat_text(conversation, model, count, typical=None, files=(), folded=0, last=None,
              parent=None) -> str:
    """
    `files`: [(item, credits it adds to a message, unrounded)] that go again;
    `folded`: messages in the memory; `last`: the newest message (a chat
    reopened); `parent`: the chat a fresh one carries the memory of.
    """
    agent = _agent_of(conversation)
    head = f"💬 {_bold(_('Chat'))}" + (f" · {esc(agent_title(agent))}" if agent else "")
    lines = [head, ""]
    if agent is not None:
        lines.append(f"<i>{esc(catalog.tr(agent.description))}</i>")
    else:
        lines.append("🤖 " + esc(_("General assistant: helps with anything.")))
    if model is not None:
        price = f" · {esc(_('An everyday answer'))}: ~{coins(typical)}" if typical else ""
        lines.append(f"{esc(_('Model'))}: <b>{esc(model)}</b>{price}")
    if count:
        title = conversation.title or "…"
        lines.append(f"{esc(_('This chat'))}: “{esc(title)}” · {esc(_('Messages'))}: {count}")
    if folded:
        lines.append("🧠 " + esc(_("Memory: %(count)s earlier messages, summarized by AI; the "
                                  "newest go word for word.") % {"count": folded}))
    if parent is not None and not count:
        lines.append("🧹 " + esc(_("A fresh chat: the AI remembers what matters from "
                                  "“%(title)s”.") % {"title": parent.title or "…"}))
    if files:
        lines += ["", "📎 " + esc(_("Read again with each message (cheaper while you keep "
                                    "chatting):"))]
        lines += [f"• {esc(file_label(item))} · {esc(price_hint(exact))}"
                  for item, exact in files]
    lines.append("")
    if agent is not None and not count:
        lines.append(esc(catalog.tr(agent.first_message) or _("Type your message.")))
        if agent.starters:
            lines.append(esc(_("Or tap a question below to begin.")))
    else:
        lines.append(esc(_("Type a message, or send photos, files or a voice note, with a "
                           "question as their caption.")))
    if last is not None:
        excerpt = last.text if len(last.text) <= 300 else last.text[:300].rsplit(" ", 1)[0] + " …"
        who = _("AI") if last.role == "assistant" else _("You")
        lines += ["", f"<i>{esc(who)}: {esc(excerpt)}</i>"]
    return "\n".join(lines)


def file_label(item) -> str:
    if item["kind"] == "photo":
        return "🖼 " + _("photo")
    return "📄 " + (item.get("name") or _("file"))


def price_hint(exact) -> str:
    """A part of a price: "up to 💎 12", or "under 💎 1"."""
    if exact < 1:
        return _("under %(price)s") % {"price": coins(1)}
    return _("up to %(price)s") % {"price": coins(math.ceil(exact))}


def starter_buttons(agent, verb) -> list:
    """The agent's first questions, one a row; `verb` gets the question's number."""
    return [[{"text": "💬 " + _cut(catalog.tr(starter), 64), "callback_data": f"{verb}:{index}"}]
            for index, starter in enumerate((agent.starters or [])[:3])]


def _cut(text, limit) -> str:
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def chat_keyboard(conversation, count, has_chats, files=(), opened=False):
    """`files`: [(message pk, index, item)] that go again, each with a Close button."""
    agent = _agent_of(conversation)
    rows = starter_buttons(agent, "ag:ask") if agent is not None and not count else []
    for message_pk, index, item in files[:4]:
        rows.append([{"text": "✖️ " + _("Close %(file)s") % {"file": _cut(file_label(item), 40)},
                      "callback_data": f"chat:close:{message_pk}:{index}"}])
    if len(files) > 1:
        rows.append([{"text": "✖️ " + _("Close all files"), "callback_data": "chat:closeall"}])
    rows.append([{"text": "➕ " + _("New chat") if count else "🧩 " + _("Change agent"),
                  "callback_data": "chat:new"},
                 {"text": "🤖 " + _("Model"), "callback_data": "chat:models"}])
    if count:
        rows.append([{"text": "🧹 " + _("Start fresh"), "callback_data": "chat:fresh"},
                     {"text": "📤 " + _("Export"), "callback_data": "chat:export"}])
    row = []
    if opened:
        row.append({"text": "🗑 " + _("Delete"), "callback_data": f"chat:del:{conversation.pk}"})
    elif has_chats:
        row.append({"text": "🗂 " + _("My chats"), "callback_data": "chat:list:0"})
    if agent is not None:
        row.append({"text": "ℹ️ " + _("About the agent"), "callback_data": f"ag:about:{agent.slug}"})
    if row:
        rows.append(row)
    rows.append(nav_row("chat:list:0" if opened else "menu:home"))
    return _inline(rows)


def new_chat_text() -> str:
    return "\n".join([
        f"➕ {_bold(_('New chat'))}", "",
        esc(_("Who do you want to talk to? An agent knows one job well and asks the right "
              "questions; the General assistant helps with anything.")),
        "", esc(_("The price of an answer depends on the model, not the agent.")),
    ])


def new_chat_keyboard(featured, same, has_saved):
    rows = []
    if same is not None:
        rows.append([{"text": "↻ " + _("Again with %(agent)s") % {"agent": agent_title(same)},
                      "callback_data": f"ag:start:{same.slug}"}])
    rows.append([{"text": "🤖 " + _("General assistant"), "callback_data": "chat:general"}])
    rows += agent_buttons(featured[:6], "ag:start")
    rows.append([{"text": "🧩 " + _("All agents"), "callback_data": "ag:"},
                 {"text": "🔍 " + _("Search"), "callback_data": "ag:search"}])
    row = [{"text": "✨ " + _("Find for me"), "callback_data": "ag:find"}]
    if has_saved:
        row.append({"text": "⭐ " + _("My agents"), "callback_data": "ag:saved"})
    rows.append(row)
    rows.append(nav_row())
    return _inline(rows)


def models_text(models, typical) -> str:
    """`typical`: {model pk: credits for an everyday answer}."""
    lines = [f"🤖 {_bold(_('Choose the AI model'))}", "",
             esc(_("For this chat and your next ones. An answer costs more on bigger models, "
                   "and more when it is long."))]
    for model in models:
        lines.append("")
        lines.append(model_line(model))
        lines.append(f"<i>{esc(_('An everyday answer'))}: ~{coins(typical[model.pk])}</i>"
                     + ("" if model.sees_images else f" · <i>{esc(_('no photos'))}</i>"))
    if not models:
        lines += ["", esc(_("No model is on offer right now."))]
    return "\n".join(lines)


def models_keyboard(models, current, back="chat:", verb="chat:model"):
    rows = [[{"text": _tick(model.pk == getattr(current, "pk", None), str(model)),
              "callback_data": f"{verb}:{model.pk}"}] for model in models]
    rows.append(nav_row(back))
    return _inline(rows)


def chats_text(conversations, page, words=None) -> str:
    head = f"🗂 {_bold(_('My chats'))}"
    if words is not None:
        head += f" · 🔍 “{esc(words[:100])}”"
        if not conversations:
            return head + "\n\n" + esc(_("No chat has these words."))
    if not conversations:
        return head + "\n\n" + esc(_("No chats yet. Just type to start one."))
    return head + "\n\n" + esc(_("Tap a chat to continue it."))


def chats_keyboard(conversations, page, has_more, found=False):
    rows = []
    for conversation in conversations:
        day = formats.date_format(timezone.localtime(conversation.updated_at),
                                  "SHORT_DATE_FORMAT")
        title = (conversation.title or _("New chat"))[:40]
        rows.append([{"text": f"📝 {title} · {day}",
                      "callback_data": f"chat:open:{conversation.pk}"}])
    nav = []
    if page > 0:
        nav.append({"text": "‹ " + _("Newer"), "callback_data": f"chat:list:{page - 1}"})
    if has_more:
        nav.append({"text": _("Older") + " ›", "callback_data": f"chat:list:{page + 1}"})
    if nav:
        rows.append(nav)
    if found:
        rows.append([{"text": "🔍 " + _("Search again"), "callback_data": "chat:search"}])
    elif conversations or page:
        rows.append([{"text": "🔍 " + _("Search my chats"), "callback_data": "chat:search"},
                     {"text": "🗑 " + _("Delete all"), "callback_data": "chat:delall"}])
    rows.append(nav_row("chat:list:0" if found else "chat:"))
    return _inline(rows)


def search_chats_text() -> str:
    return f"🔍 {_bold(_('Search my chats'))}\n\n" + esc(
        _("Type words from a chat's title or messages."))


def delete_all_text(count) -> str:
    return (f"🗑 {_bold(_('Delete all your chats?'))}\n\n"
            + esc(_("%(count)s chats go from your chats, with their messages. What you were "
                    "charged stays on your statement.") % {"count": count}))


def delete_all_keyboard():
    return _inline([[{"text": "🗑 " + _("Delete all"), "callback_data": "chat:delallok"}],
                    nav_row("chat:list:0")])


def export_caption(conversation) -> str:
    return f"📤 “{esc(conversation.title or '…')}”"


def export_text(conversation, messages) -> str:
    """A chat as a text file (plain text: no HTML)."""
    def when(moment):
        return formats.date_format(timezone.localtime(moment), "SHORT_DATETIME_FORMAT")

    agent = _agent_of(conversation)
    lines = [f"AIstars · {conversation.title or _('Chat')}",
             f"{_('Agent')}: {agent_title(agent)}" if agent else _("General assistant"),
             f"{_('Exported')}: {when(timezone.now())}", ""]
    for message in messages:
        who = _("AI") if message.role == "assistant" else _("You")
        lines.append(f"— {who} · {when(message.created_at)}")
        marks = [file_label({"kind": "photo"}) for _photo in message.photos or []]
        marks += [file_label({**item, "kind": "document"}) for item in message.files or []]
        if marks:
            lines.append("[" + ", ".join(marks) + "]")
        lines += [message.text, ""]
    return "\n".join(lines)


def delete_text(conversation) -> str:
    return (f"🗑 {_bold(_('Delete this chat?'))}\n\n“{esc(conversation.title or '…')}”\n\n"
            + esc(_("It goes from your chats, with its messages. What you were charged stays "
                    "on your statement.")))


def delete_keyboard(conversation):
    return _inline([
        [{"text": "🗑 " + _("Delete"), "callback_data": f"chat:delok:{conversation.pk}"}],
        nav_row(f"chat:open:{conversation.pk}"),
    ])


def thinking_text() -> str:
    return "💭 …"


def details_button(job):
    return {"text": "🧾 " + _("Details"), "callback_data": f"out:cost:{job.pk}"}


def buy_row():
    return [{"text": "⭐ " + _("Buy credits"), "callback_data": "out:buy"}]


def answer_keyboard(message_pk, listen_price, job, low=False, files_open=0):
    """Under an answer, which is not the panel: out: buttons only."""
    first = []
    if listen_price is not None:
        first.append({"text": f"🔊 {_('Listen')} · {coins(listen_price)}",
                      "callback_data": f"out:say:{message_pk}"})
    first.append(details_button(job))
    rows = [first]
    if files_open:
        rows.append([{"text": f"📎 {_('Close the files')} ({files_open})",
                      "callback_data": f"out:closefiles:{job.conversation_id}"}])
    if low:
        rows.append(buy_row())
    rows.append([{"text": "➕ " + _("New chat"), "callback_data": "out:new"},
                 menu_button(new=True)])
    return _inline(rows)


def result_keyboard(job, low=False):
    """Under a voice note read aloud or a text heard: what it cost, and the menu."""
    rows = [[details_button(job), menu_button(new=True)]]
    if low:
        rows.insert(0, buy_row())
    return _inline(rows)


def details_text(job) -> str:
    """The 🧾 Details box (plain text, at most 200 characters)."""
    icon = {Kind.CHAT: "💬", Kind.IMAGE: "🎨", Kind.EDIT: "🖌", Kind.TRANSCRIBE: "📝",
            Kind.SPEECH: "🔊"}.get(job.kind, "🧾")
    lines = [f"{icon} {job_name(job)} #{job.pk}"]
    what = [str(job.model)]
    if job.assistant_id:
        what.append(agent_title(job.assistant))
    options = job.options or {}
    if job.kind in (Kind.IMAGE, Kind.EDIT):
        what.append(f"{size_name(options.get('size', ''))}, "
                    f"{quality_name(options.get('quality', ''))}")
    elif job.kind == Kind.TRANSCRIBE:
        seconds = int(sum(item.get("seconds") or 0 for item in options.get("audio") or []))
        what.append(f"{seconds // 60}:{seconds % 60:02d}")
    elif job.kind == Kind.SPEECH:
        what.append(f"{number(options.get('characters', len(job.prompt)))} "
                    f"{_('characters')}")
    lines.append(" · ".join(what))
    if job.status == Status.DONE:
        lines.append(_("Held %(hold)s, charged %(charged)s, returned %(back)s") % {
            "hold": coins(job.hold), "charged": coins(job.charged),
            "back": coins(job.hold - job.charged)})
    elif job.status == Status.FAILED:
        lines.append(_("Not done: nothing was charged."))
    else:
        lines.append(_("Being made: %(hold)s held.") % {"hold": coins(job.hold)})
    if job.cached_tokens:
        lines.append(_("Tokens: %(read)s read (%(cached)s of them cheaper, from the cache), "
                       "%(wrote)s written") % {
            "read": number(job.input_tokens), "cached": number(job.cached_tokens),
            "wrote": number(job.output_tokens)})
    elif job.input_tokens or job.output_tokens:
        lines.append(_("Tokens: %(read)s read, %(wrote)s written") % {
            "read": number(job.input_tokens), "wrote": number(job.output_tokens)})
    return "\n".join(lines)[:200]


def cut_off_text() -> str:
    return _("The answer reached its length limit. Ask “go on” for the rest.")


def empty_answer_text() -> str:
    return _("The AI gave no answer this time (it spent its whole length thinking). Try "
             "again, or ask in fewer words.")


def answer_failed_text(job) -> str:
    return "⚠️ " + esc(_("The answer could not be made, so nothing was charged. Please try "
                        "again.")) + f"\n<i>#{job.pk}</i>"


def retry_keyboard(job):
    return _inline([[{"text": "🔁 " + _("Try again"), "callback_data": f"out:retry:{job.pk}"}],
                    [menu_button(new=True)]])


def busy_text() -> str:
    return "⏳ " + _("Still answering your last message. Send this one when the answer is here.")


def chat_deleted_text() -> str:
    return _("You deleted that chat. Ask again in a chat you have.")


def too_many_text() -> str:
    return "⏳ " + _("You already have requests being made. Please wait for one to finish.")


def too_long_text(limit) -> str:
    return _("That message is too long. The most is %(limit)s characters.") % {"limit": limit}


def no_model_text() -> str:
    return _("No AI model is on offer for this right now. Please try again later.")


def no_photos_text(model) -> str:
    return _("%(model)s cannot see photos. Choose a model that can, under 🤖 Model.") % {
        "model": model}


def not_enough_text(hold, balance) -> str:
    return "\n".join([
        f"{coin()} " + _bold(_("Not enough credits")),
        "",
        esc(_("This can cost up to %(price)s, and your balance is %(balance)s.") % {
            "price": coins(hold), "balance": coins(balance)}),
        esc(_("Buy credits, or choose a cheaper model.")),
    ])


def not_enough_keyboard(chat=True):
    rows = [[{"text": "⭐ " + _("Buy credits"), "callback_data": "out:buy"}]]
    if chat:
        rows.append([{"text": "🤖 " + _("Cheaper model"), "callback_data": "out:models"}])
    rows.append([menu_button(new=True)])
    return _inline(rows)


def confirm_text(hold, balance, long_chat) -> str:
    lines = ["🧾 " + _bold(_("This message can cost up to %(price)s") % {"price": coins(hold)}),
             "", esc(_("You pay only what it really costs, often much less."))]
    if long_chat:
        lines.append(esc(_("Most of it is the chat so far: every answer reads it all again. A "
                           "new chat costs less.")))
    lines += ["", f"{esc(_('Balance'))}: {coins(balance)}"]
    return "\n".join(lines)


def confirm_keyboard(hold, long_chat):
    rows = [[{"text": f"✅ {_('Send')} · {_('up to')} {coins(hold)}",
              "callback_data": "out:send"}]]
    if long_chat:
        rows.append([{"text": "➕ " + _("Send it in a new chat"), "callback_data": "out:sendnew"}])
    rows.append([{"text": "🤖 " + _("Cheaper model"), "callback_data": "out:models"},
                 {"text": "✖️ " + _("Cancel"), "callback_data": "out:cancel"}])
    return _inline(rows)


def cancelled_text() -> str:
    return "✖️ " + _("Not sent.")


def limit_text(limit, spent, hold) -> str:
    return "\n".join([
        "📊 " + _bold(_("Daily limit reached")), "",
        esc(_("You set a limit of %(limit)s a day. Today %(spent)s is spent or held, and this "
              "needs up to %(price)s.") % {"limit": coins(limit), "spent": coins(spent),
                                           "price": coins(hold)}),
        esc(_("It starts again tomorrow, or you can change it under 📊 Usage.")),
    ])


def limit_keyboard():
    return _inline([[{"text": "📊 " + _("Usage"), "callback_data": "out:usage"},
                     menu_button(new=True)]])


def blocked_text(category_text) -> str:
    return "🚫 " + esc(_("This is not allowed: %(category)s.") % {"category": category_text})


def unchecked_text() -> str:
    return "⚠️ " + esc(_("This could not be checked right now, so nothing was sent. Please "
                        "try again in a minute."))


def refused_text() -> str:
    return "🚫 " + esc(_("The AI provider's own safety check refused this, so nothing was "
                        "charged."))


def file_problem_text(what, name, model) -> str:
    """Why a photo or file cannot go to the AI (files.problem)."""
    if what == "no_photos":
        return no_photos_text(model)
    if what == "too_big":
        return _("%(name)s is over 20 MB, the most a bot can download from Telegram.") % {
            "name": name}
    if what == "cannot_read":
        return _("%(model)s cannot read %(name)s. Choose another model under 🤖 Model.") % {
            "model": model, "name": name}
    if name in ("video", "animation", "video_note", "sticker"):
        return _("Videos cannot be read yet. The AI reads photos, PDFs, Word, Excel, "
                 "PowerPoint, text and code files.")
    return _("%(name)s cannot be read. The AI reads photos, PDFs, Word, Excel, PowerPoint, "
             "text and code files.") % {"name": name}


def too_many_files_text(limit) -> str:
    return _("That is more than %(limit)s files for one message.") % {"limit": limit}


def files_too_big_text() -> str:
    return _("These files are over 50 MB together, the most for one message.")


# --------------------------------------------------------------------------
# Agents
# --------------------------------------------------------------------------

def agent_title(agent) -> str:
    return f"{agent.emoji} {catalog.tr(agent.name)}".strip()


def category_title(category) -> str:
    return f"{category.emoji} {catalog.tr(category.name)}".strip()


def trail(*parts) -> str:
    """Where a screen sits: 🧩 Agents › ⚖️ Law."""
    last = len(parts) - 1
    return "🧩 " + " › ".join(f"<b>{esc(part)}</b>" if index == last else esc(part)
                             for index, part in enumerate(parts))


def agent_buttons(agents, verb="ag:card", per_row=2) -> list:
    rows, row = [], []
    for agent in agents:
        row.append({"text": agent_title(agent)[:40], "callback_data": f"{verb}:{agent.slug}"})
        if len(row) == per_row:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return rows


def _agent_lines(agents) -> list:
    return [f"{esc(agent.emoji)} <b>{esc(catalog.tr(agent.name))}</b> — "
            f"<i>{esc(catalog.tr(agent.description))}</i>" for agent in agents]


def agents_text(categories, count) -> str:
    return "\n".join([
        trail(_("Agents")), "",
        esc(_("Agents are AI assistants made for one job: each knows its field, asks the right "
              "questions and answers the way that job needs.")),
        esc(_("Pick a category, search by words, or tell the AI what you need.")), "",
        "<i>" + esc(_("%(agents)s agents in %(categories)s categories") % {
            "agents": count, "categories": len(categories)}) + "</i>",
    ])


def agents_keyboard(categories, has_saved, has_recent):
    rows, row = [], []
    for category in categories:
        row.append({"text": category_title(category), "callback_data": f"ag:cat:{category.slug}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "🔍 " + _("Search"), "callback_data": "ag:search"},
                 {"text": "✨ " + _("Find for me"), "callback_data": "ag:find"}])
    row = []
    if has_saved:
        row.append({"text": "⭐ " + _("My agents"), "callback_data": "ag:saved"})
    if has_recent:
        row.append({"text": "🕘 " + _("Recent"), "callback_data": "ag:recent"})
    row.append({"text": "🔥 " + _("Popular"), "callback_data": "ag:top"})
    rows.append(row)
    rows.append(nav_row())
    return _inline(rows)


def category_text(category, agents) -> str:
    return "\n".join([trail(_("Agents"), category_title(category)), "",
                      *_agent_lines(agents), "", esc(_("Tap an agent to see what it does."))])


def category_keyboard(category, agents, page, has_more):
    rows = agent_buttons(agents)
    nav = []
    if page > 0:
        nav.append({"text": "‹ " + _("Previous"),
                    "callback_data": f"ag:cat:{category.slug}:{page - 1}"})
    if has_more:
        nav.append({"text": _("More") + " ›", "callback_data": f"ag:cat:{category.slug}:{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append(nav_row("ag:"))
    return _inline(rows)


def card_text(agent, model, typical, saved) -> str:
    where = [_("Agents")] + ([category_title(agent.category)] if agent.category_id else [])
    lines = [trail(*where), "",
             f"{esc(agent.emoji)} {_bold(catalog.tr(agent.name))}" + (" ⭐" if saved else ""),
             f"<i>{esc(catalog.tr(agent.description))}</i>"]
    if agent.starters:
        lines += ["", esc(_("Try asking:"))]
        lines += [f"• {esc(catalog.tr(starter))}" for starter in agent.starters[:3]]
    if model is not None:
        lines += ["", f"{esc(_('Model'))}: <b>{esc(model)}</b> · "
                      f"{esc(_('An everyday answer'))}: ~{coins(typical)}"]
    lines.append(f"<i>{esc(_('Every answer shows what it cost. You pay only what it really '
                             'costs.'))}</i>")
    return "\n".join(lines)


def card_keyboard(agent, saved, share_url, back):
    rows = [[{"text": "▶️ " + _("Start a chat"), "callback_data": f"ag:start:{agent.slug}"}]]
    rows += starter_buttons(agent, f"ag:try:{agent.slug}")
    row = [{"text": "✓ " + _("Saved") if saved else "⭐ " + _("Save"),
            "callback_data": f"ag:save:{agent.slug}"}]
    if share_url:
        row.append({"text": "📤 " + _("Share"), "url": share_url})
    rows.append(row)
    rows.append(nav_row(back))
    return _inline(rows)


def saved_toast(saved) -> str:
    return "⭐ " + _("Saved to My agents") if saved else _("Removed from My agents")


LISTS = ("saved", "recent", "top", "search", "find")


def list_text(kind, agents, words="") -> str:
    title = {"saved": "⭐ " + _("My agents"), "recent": "🕘 " + _("Recent"),
             "top": "🔥 " + _("Popular"), "search": "🔍 " + _("Search"),
             "find": "✨ " + _("Found for you")}[kind]
    lines = [trail(_("Agents"), title), ""]
    if words:
        lines += [f"“{esc(words[:200])}”", ""]
    if agents:
        lines += _agent_lines(agents)
        if kind == "top":
            lines += ["", f"<i>{esc(_('The agents people used most this month.'))}</i>"]
        return "\n".join(lines)
    lines.append(esc({
        "saved": _("No saved agents yet. Open an agent and tap ⭐ Save to keep it here."),
        "recent": _("No chats with an agent yet."),
        "top": _("No agent has been used this month yet."),
        "search": _("No agent matches these words. Try other words, or let the AI find one "
                    "for you."),
        "find": _("The AI found no agent for this. Try other words, or browse the "
                  "categories."),
    }[kind]))
    return "\n".join(lines)


def list_keyboard(kind, agents):
    rows = agent_buttons(agents)
    if kind == "search":
        rows.append([{"text": "🔍 " + _("Search again"), "callback_data": "ag:search"},
                     {"text": "✨ " + _("Find for me"), "callback_data": "ag:find"}])
    elif kind == "find":
        rows.append([{"text": "✨ " + _("Describe it again"), "callback_data": "ag:find"},
                     {"text": "🔍 " + _("Search"), "callback_data": "ag:search"}])
    rows.append(nav_row("ag:"))
    return _inline(rows)


def search_text() -> str:
    return "\n".join([trail(_("Agents"), "🔍 " + _("Search")), "",
                      esc(_("Type what you need, like “contract”, “English” or “recipe”."))])


def find_text(left) -> str:
    lines = [trail(_("Agents"), "✨ " + _("Find for me")), ""]
    if left:
        lines += [esc(_("Describe your task in your own words, in any language, and the AI "
                        "picks the agents that fit it.")), "",
                  f"<i>{esc(_('Free. Left today: %(left)s') % {'left': left})}</i>"]
    else:
        lines.append(esc(_("You have used today's free finds. Search by words instead, or "
                           "browse the categories.")))
    return "\n".join(lines)


def find_keyboard(left):
    rows = [] if left else [[{"text": "🔍 " + _("Search"), "callback_data": "ag:search"}]]
    rows.append(nav_row("ag:"))
    return _inline(rows)


def finding_text() -> str:
    return "\n".join([trail(_("Agents"), "✨ " + _("Find for me")), "",
                      "🔎 " + esc(_("Looking for the right agents…"))])


def asked_text(question) -> str:
    """A starter question tapped: shown as what the user asked."""
    return f"🗨 <i>{esc(question)}</i>"


# --------------------------------------------------------------------------
# About me
# --------------------------------------------------------------------------

ABOUT_LIMIT = 600


def about_text(about) -> str:
    lines = [f"📝 {_bold(_('About me'))}", "",
             esc(_("A few lines about you that every chat gets, so the AI and the agents need "
                   "not ask again: your country, your work, your level in a language, how you "
                   "like answers.")), ""]
    lines.append(f"<i>{esc(about)}</i>" if about else esc(_("Nothing yet.")))
    return "\n".join(lines)


def about_keyboard(has_about):
    row = [{"text": "✏️ " + (_("Change it") if has_about else _("Write it")),
            "callback_data": "me:edit"}]
    if has_about:
        row.append({"text": "🗑 " + _("Clear"), "callback_data": "me:clear"})
    return _inline([row, nav_row("menu:profile")])


def ask_about_text() -> str:
    return f"📝 {_bold(_('About me'))}\n\n" + esc(
        _("Type a few lines about yourself, up to %(limit)s characters. For example: “I live "
          "in Romania, work as an accountant, and like short answers.”") % {
            "limit": ABOUT_LIMIT})


def about_too_long_text() -> str:
    return _("That is too long. About me takes up to %(limit)s characters.") % {
        "limit": ABOUT_LIMIT}


def about_button():
    return {"text": "📝 " + _("About me"), "callback_data": "me:"}


# --------------------------------------------------------------------------
# Privacy
# --------------------------------------------------------------------------

def privacy_text(archive) -> str:
    keep = [_("Your chats: their messages, and the memory the AI writes of a long chat. A chat "
              "you delete is gone from your chats; the owner keeps a record of it."),
            _("What you write in About me, until you clear it."),
            _("Your requests and what each cost, for your statement."),
            _("The safety checks of your messages.")]
    if archive:
        keep.append(_("Copies of the photos, files and voice notes you send, and of the images "
                      "and voice notes made, in the owner's private archive channel."))
    see = [_("The AI provider (OpenAI or Anthropic) gets each request to answer it. OpenAI is "
             "asked not to store it; the providers may keep requests for a short time to check "
             "for abuse, under their own policies."),
           _("The owner of the bot can read chats, for support and safety.")]
    choose = [_("Delete a chat, or all of them, from your chats: /chats."),
              _("Clear About me: 👤 Profile → 📝 About me."),
              _("For anything else about your data, write to /support.")]

    def block(title, items):
        return [_bold(title)] + [f"• {esc(item)}" for item in items]

    return "\n".join([f"🔒 {_bold(_('Privacy'))}", "",
                      *block(_("What is kept"), keep), "",
                      *block(_("Who sees it"), see), "",
                      *block(_("Your choices"), choose)])


# --------------------------------------------------------------------------
# Usage
# --------------------------------------------------------------------------

def tool_name(kind) -> str:
    return {"chat": "💬 " + _("Chat"), "images": "🎨 " + _("Images"),
            "voice": "🎙 " + _("Voice")}.get(kind, kind)


def usage_text(periods, balance, limit, spent, tools, by_agent, by_model) -> str:
    """`periods`: [(label, credits, requests)]; the rest: [(name, credits)]."""
    lines = [f"📊 {_bold(_('Usage'))}", ""]
    for label, credits, requests in periods:
        lines.append(f"{esc(label)}: <b>{coins(credits)}</b> · {esc(_('Requests'))}: {requests}")
    lines.append(f"{esc(_('Balance'))}: {coins(balance)}")
    if limit:
        lines.append(f"{esc(_('Daily limit'))}: {coins(limit)} · {esc(_('used today'))}: "
                     f"{coins(spent)}")
    else:
        lines.append(f"{esc(_('Daily limit'))}: {esc(_('none'))}")
    for title, rows in ((_("Last 30 days by tool"), tools), (_("By agent"), by_agent),
                        (_("By model"), by_model)):
        if rows:
            lines += ["", _bold(title)]
            lines += [f"{esc(name)}: {coins(credits)}" for name, credits in rows]
    if not periods[-1][2]:
        lines += ["", esc(_("No requests yet. Every answer, image and voice note shows here "
                            "with what it cost."))]
    lines += ["", "<i>" + esc(_("1,000 tokens are about 750 English words. Tap 🧾 Details "
                                "under an answer for its tokens.")) + "</i>"]
    return "\n".join(lines)


def usage_keyboard():
    from apps.billing.texts import credits_button

    return _inline([
        [{"text": "🧾 " + _("Last requests"), "callback_data": "use:last:0"},
         {"text": "🎯 " + _("Daily limit"), "callback_data": "use:limit"}],
        [credits_button()],
        nav_row(),
    ])


def _request_line(job) -> str:
    when = formats.date_format(timezone.localtime(job.created_at), "SHORT_DATETIME_FORMAT")
    icon = {Kind.CHAT: "💬", Kind.IMAGE: "🎨", Kind.EDIT: "🖌", Kind.TRANSCRIBE: "📝",
            Kind.SPEECH: "🔊"}.get(job.kind, "🧾")
    name = agent_title(job.assistant) if job.assistant_id else job_name(job)
    if job.status == Status.DONE:
        price = coins(job.charged)
    elif job.status == Status.FAILED:
        price = _("nothing charged")
    else:
        price = _("being made")
    return f"<code>#{job.pk}</code> {esc(when)}\n{icon} {esc(name)} · {esc(job.model)} · " \
           f"<b>{esc(price)}</b>"


def last_text(rows, page) -> str:
    lines = [f"🧾 {_bold(_('Last requests'))}", ""]
    if not rows:
        lines.append(esc(_("No requests yet.")))
    lines += [_request_line(job) for job in rows]
    return "\n".join(lines)


def last_keyboard(page, has_more):
    rows, nav = [], []
    if page > 0:
        nav.append({"text": "‹ " + _("Newer"), "callback_data": f"use:last:{page - 1}"})
    if has_more:
        nav.append({"text": _("Older") + " ›", "callback_data": f"use:last:{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append(nav_row("use:"))
    return _inline(rows)


LIMITS = (0, 20, 50, 100, 200, 500, 1000)


def limit_choice_text(limit, spent) -> str:
    return "\n".join([
        f"🎯 {_bold(_('Daily limit'))}", "",
        esc(_("The most your requests may cost in a day. A request that would go past it is "
              "not sent, and nothing is charged.")), "",
        f"{esc(_('Now'))}: " + (coins(limit) if limit else esc(_("no limit"))),
        f"{esc(_('Today'))}: {coins(spent)}",
    ])


def limit_choice_keyboard(current):
    buttons = [{"text": _tick((current or 0) == amount,
                              coins(amount) if amount else _("No limit")),
                "callback_data": f"use:setlimit:{amount}"} for amount in LIMITS]
    rows = [buttons[:1], buttons[1:4], buttons[4:]]
    rows.append(nav_row("use:"))
    return _inline(rows)


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------

def images_text(model) -> str:
    lines = [f"🎨 {_bold(_('Images'))}", "",
             esc(_("Create an image from a description, or change a photo you send."))]
    if model is not None:
        lines += ["", f"{esc(_('Model'))}: {model_line(model)}"]
    else:
        lines += ["", esc(no_model_text())]
    return "\n".join(lines)


def images_keyboard(model, can_edit, several):
    rows = []
    if model is not None:
        row = [{"text": "✨ " + _("Create an image"), "callback_data": "img:create"}]
        if can_edit:
            row.append({"text": "🖌 " + _("Edit a photo"), "callback_data": "img:edit"})
        rows.append(row)
    if several:
        rows.append([{"text": "🤖 " + _("Model"), "callback_data": "img:models"}])
    rows.append(nav_row())
    return _inline(rows)


def image_models_text(models) -> str:
    lines = [f"🤖 {_bold(_('Choose the image model'))}"]
    for model in models:
        lines += ["", model_line(model)]
    return "\n".join(lines)


def ask_description_text() -> str:
    return (f"✨ {_bold(_('Create an image'))}\n\n"
            + esc(_("Describe the image you want, in any language.")))


def ask_photo_text() -> str:
    return f"🖌 {_bold(_('Edit a photo'))}\n\n" + esc(
        _("Send the photo you want to change, or several photos as one album."))


def ask_change_text() -> str:
    return (f"🖌 {_bold(_('Edit a photo'))}\n\n"
            + esc(_("What should change? Describe it, in any language.")))


def cancel_keyboard(back):
    return _inline([[{"text": "✖️ " + _("Cancel"), "callback_data": back}], [menu_button()]])


def image_summary_text(draft, model, hold, balance) -> str:
    photos = len(draft.get("photos") or [])
    title = _("Your photo edit") if photos else _("Your image")
    if photos > 1:
        title += f" · 🖼 {photos}"
    lines = [f"🎨 {_bold(title)}", "", f"<i>{esc(draft['prompt'])}</i>", "",
             f"{esc(_('Shape'))}: {esc(size_name(draft['size']))} · "
             f"{esc(_('Quality'))}: {esc(quality_name(draft['quality']))}",
             f"{esc(_('Model'))}: {esc(model)}", "", price_line(hold, balance)]
    return "\n".join(lines)


def image_summary_keyboard(draft, hold, enough):
    sizes = (("auto",) if draft.get("photos") else ()) + SIZES
    shapes = [{"text": _tick(size == draft["size"], size_name(size)),
               "callback_data": f"img:size:{size}"} for size in sizes]
    # Three shapes fit a row; with "As the photo", two rows of two.
    rows = [shapes] if len(shapes) == 3 else [shapes[:2], shapes[2:]]
    rows.append([{"text": _tick(quality == draft["quality"], quality_name(quality)),
                  "callback_data": f"img:quality:{quality}"} for quality in QUALITIES])
    if enough:
        label = _("Edit it") if draft.get("photos") else _("Create it")
        rows.append([{"text": f"✅ {label} · {coins(hold)}", "callback_data": "img:go"}])
    else:
        rows.append([{"text": "⭐ " + _("Buy credits"), "callback_data": "credits:buy"}])
    rows.append([{"text": "✏️ " + _("Change the description"), "callback_data": "img:prompt"},
                 {"text": "✖️ " + _("Cancel"), "callback_data": "img:"}])
    return _inline(rows)


def making_image_text(job) -> str:
    return "⏳ " + esc(_("Making your image (#%(number)s). It arrives here in about a "
                        "minute.") % {"number": job.pk})


def image_caption(job, balance) -> str:
    return f"🎨 #{job.pk}\n" + footer(job.charged, balance)


def image_keyboard(job, original, low=False):
    rows = [[{"text": "🔁 " + _("Again"), "callback_data": f"out:again:{job.pk}"},
             {"text": "🖌 " + _("Edit this"), "callback_data": f"out:edit:{job.pk}"}]]
    row = [{"text": "📎 " + _("Original file"), "callback_data": f"out:file:{job.pk}"}] \
        if original else []
    rows.append(row + [details_button(job)])
    if low:
        rows.append(buy_row())
    rows.append([menu_button(new=True)])
    return _inline(rows)


def image_failed_text(job) -> str:
    return "⚠️ " + esc(_("Your image could not be made, so nothing was charged. Please try "
                        "again.")) + f"\n<i>#{job.pk}</i>"


def again_keyboard(job):
    return _inline([[{"text": "🔁 " + _("Try again"), "callback_data": f"out:again:{job.pk}"}],
                    [menu_button(new=True)]])


# --------------------------------------------------------------------------
# Voice
# --------------------------------------------------------------------------

def voice_text(voice, stt, tts) -> str:
    lines = [f"🎙 {_bold(_('Voice'))}", "",
             esc(_("Send a voice note in a chat and the AI answers it. Or:"))]
    if stt:
        lines.append("📝 " + esc(_("Voice to text: send a voice note or an audio file, get "
                                   "the text.")))
    if tts:
        lines.append("🔊 " + esc(_("Text to speech: send a text, get it read aloud.")))
        lines += ["", f"{esc(_('Voice'))}: {esc(voice_name(voice))}"]
    return "\n".join(lines)


def voice_keyboard(stt, tts):
    row = []
    if stt:
        row.append({"text": "📝 " + _("Voice to text"), "callback_data": "voice:stt"})
    if tts:
        row.append({"text": "🔊 " + _("Text to speech"), "callback_data": "voice:tts"})
    rows = [row] if row else []
    if tts:
        rows.append([{"text": "🗣 " + _("Choose the voice"), "callback_data": "voice:voices"}])
    rows.append(nav_row())
    return _inline(rows)


def voices_text() -> str:
    return f"🗣 {_bold(_('Choose the voice'))}\n\n" + esc(
        _("For texts read aloud, and the Listen button under answers."))


def voices_keyboard(current):
    rows, row = [], []
    for voice in VOICES:
        row.append({"text": _tick(voice == current, voice_name(voice)),
                    "callback_data": f"voice:set:{voice}"})
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(nav_row("voice:"))
    return _inline(rows)


def ask_voice_text() -> str:
    return f"📝 {_bold(_('Voice to text'))}\n\n" + esc(
        _("Send a voice note or an audio file, up to 20 minutes."))


def ask_speech_text() -> str:
    return f"🔊 {_bold(_('Text to speech'))}\n\n" + esc(
        _("Send the text to read aloud, up to 4000 characters."))


def speech_summary_text(text, voice, price, balance) -> str:
    excerpt = text if len(text) <= 300 else text[:300].rsplit(" ", 1)[0] + " …"
    return "\n".join([
        f"🔊 {_bold(_('Text to speech'))}", "", f"<i>{esc(excerpt)}</i>", "",
        f"{esc(_('Characters'))}: {len(text)} · {esc(_('Voice'))}: {esc(voice_name(voice))}",
        "", f"{esc(_('Price'))}: <b>{coins(price)}</b>", f"{esc(_('Balance'))}: {coins(balance)}",
    ])


def speech_summary_keyboard(price, enough):
    rows = []
    if enough:
        rows.append([{"text": f"✅ {_('Read it')} · {coins(price)}", "callback_data": "voice:go"}])
    else:
        rows.append([{"text": "⭐ " + _("Buy credits"), "callback_data": "credits:buy"}])
    rows.append([{"text": "🗣 " + _("Choose the voice"), "callback_data": "voice:voices"},
                 {"text": "✖️ " + _("Cancel"), "callback_data": "voice:"}])
    return _inline(rows)


def too_long_audio_text() -> str:
    return _("That is too long. Voice to text takes up to 20 minutes.")


def no_duration_text() -> str:
    return _("Send it as a voice note or an audio file, so its length is known.")


def listening_text() -> str:
    return "🎙 …"


def transcript_text(text, voice_chat) -> str:
    """What was heard, above the answer to it (a long one is cut: the AI gets it all)."""
    shown = text if len(text) <= 3500 else text[:3500].rsplit(" ", 1)[0] + " …"
    if voice_chat:
        return f"🎙 <i>{esc(shown)}</i>"
    return f"📝 {esc(shown)}"


def nothing_heard_text() -> str:
    return _("No words could be heard in it.")


def voice_failed_text(job) -> str:
    return "⚠️ " + esc(_("This could not be done, so nothing was charged. Please try again.")) \
        + f"\n<i>#{job.pk}</i>"


def reading_text(job) -> str:
    return "⏳ " + esc(_("Reading it aloud (#%(number)s)…") % {"number": job.pk})


def speech_caption(job, balance) -> str:
    return f"🔊 #{job.pk}\n" + footer(job.charged, balance)
