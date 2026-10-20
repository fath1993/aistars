"""
Every word of the AI tools, in English inside `_()`; translated by AI like
the rest of the bot (apps/core/i18n.py). Database texts users see (model
taglines, assistants) go through catalog.tr. No number agreement: amounts
appear as "🪙 3" or after a label.

Callback data: panel screens use chat:, asst:, img: and voice:. Buttons on
messages that are not the panel (an answer, an image, a notice) use out:, so
tapping them never turns that message into the panel.
"""

import json

from django.utils import formats, timezone

from apps.billing.texts import coins
from apps.core.i18n import gettext as _
from apps.core.menus import menu_button, nav_row
from apps.core.services.telegram import esc

from . import catalog
from .models import Kind

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


def footer(charged, balance, model=None) -> str:
    """Under every answer and result: what it cost, and what is left."""
    parts = [coins(charged)]
    if model is not None:
        parts.append(esc(model))
    parts.append(f"{esc(_('Balance'))} {coins(balance)}")
    return "<i>" + " · ".join(parts) + "</i>"


def price_line(hold, balance) -> str:
    return "\n".join([
        f"{esc(_('Price'))}: <b>{esc(_('at most'))} {coins(hold)}</b>",
        f"<i>{esc(_('You pay what it really costs, never more.'))}</i>",
        f"{esc(_('Balance'))}: {coins(balance)}",
    ])


# --------------------------------------------------------------------------
# Home
# --------------------------------------------------------------------------

def home_lines(model, balance) -> list:
    lines = ["💬 " + esc(_("Just type a message to chat with the AI."))]
    if model is not None:
        lines.append(f"{esc(_('Model'))}: {esc(model)}")
    lines.append(f"{esc(_('Balance'))}: <b>{coins(balance)}</b>")
    return lines


def home_rows() -> list:
    return [
        [{"text": "💬 " + _("Chat"), "callback_data": "chat:"},
         {"text": "🎨 " + _("Images"), "callback_data": "img:"}],
        [{"text": "🎙 " + _("Voice"), "callback_data": "voice:"},
         {"text": "🧩 " + _("Assistants"), "callback_data": "asst:"}],
    ]


def notice_keyboard():
    return _inline([[menu_button(new=True)]])


# --------------------------------------------------------------------------
# Chat
# --------------------------------------------------------------------------

def chat_text(conversation, model, count) -> str:
    lines = [f"💬 {_bold(_('Chat'))}", ""]
    if model is not None:
        lines.append(f"{esc(_('Model'))}: {model_line(model)}")
    if conversation is not None and conversation.assistant_id:
        lines.append(f"{esc(_('Assistant'))}: {esc(conversation.assistant.emoji)} "
                     f"{esc(catalog.tr(conversation.assistant.name))}")
    if count:
        title = conversation.title or "…"
        lines.append(f"{esc(_('This chat'))}: “{esc(title)}” · {esc(_('Messages'))}: {count}")
    lines.append("")
    if conversation is not None and conversation.assistant_id and not count:
        lines.append(esc(catalog.tr(conversation.assistant.first_message)
                         or _("Type your message.")))
    else:
        lines.append(esc(_("Type a message, send a photo with a question, or a voice note.")))
    return "\n".join(lines)


def chat_keyboard(has_chats):
    rows = [[{"text": "🤖 " + _("Model"), "callback_data": "chat:models"},
             {"text": "➕ " + _("New chat"), "callback_data": "chat:new"}]]
    if has_chats:
        rows.append([{"text": "🗂 " + _("My chats"), "callback_data": "chat:list:0"}])
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


def chats_text(conversations, page) -> str:
    if not conversations:
        return f"🗂 {_bold(_('My chats'))}\n\n" + esc(_("No chats yet. Just type to start one."))
    return f"🗂 {_bold(_('My chats'))}\n\n" + esc(_("Tap a chat to continue it."))


def chats_keyboard(conversations, page, has_more):
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
    rows.append(nav_row("chat:"))
    return _inline(rows)


def opened_text(conversation, model, count, last) -> str:
    text = chat_text(conversation, model, count)
    if last is not None:
        excerpt = last.text if len(last.text) <= 300 else last.text[:300].rsplit(" ", 1)[0] + " …"
        who = _("AI") if last.role == "assistant" else _("You")
        text += f"\n\n<i>{esc(who)}: {esc(excerpt)}</i>"
    return text


def opened_keyboard(conversation):
    return _inline([
        [{"text": "🤖 " + _("Model"), "callback_data": "chat:models"},
         {"text": "🗑 " + _("Delete this chat"), "callback_data": f"chat:del:{conversation.pk}"}],
        [{"text": "➕ " + _("New chat"), "callback_data": "chat:new"}],
        nav_row("chat:list:0"),
    ])


def delete_text(conversation) -> str:
    return (f"🗑 {_bold(_('Delete this chat?'))}\n\n“{esc(conversation.title or '…')}”\n\n"
            + esc(_("Its messages are removed for good. What you were charged stays on your "
                    "statement.")))


def delete_keyboard(conversation):
    return _inline([
        [{"text": "🗑 " + _("Delete"), "callback_data": f"chat:delok:{conversation.pk}"}],
        nav_row(f"chat:open:{conversation.pk}"),
    ])


def thinking_text() -> str:
    return "💭 …"


def answer_keyboard(message_pk, listen_price):
    """Under an answer, which is not the panel: out: buttons only."""
    rows = []
    if listen_price is not None:
        rows.append([{"text": f"🔊 {_('Listen')} · {coins(listen_price)}",
                      "callback_data": f"out:say:{message_pk}"},
                     {"text": "➕ " + _("New chat"), "callback_data": "out:new"}])
    else:
        rows.append([{"text": "➕ " + _("New chat"), "callback_data": "out:new"}])
    rows.append([menu_button(new=True)])
    return _inline(rows)


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
        "🪙 " + _bold(_("Not enough credits")),
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


def blocked_text(category_text) -> str:
    return "🚫 " + esc(_("This is not allowed: %(category)s.") % {"category": category_text})


def unchecked_text() -> str:
    return "⚠️ " + esc(_("This could not be checked right now, so nothing was sent. Please "
                        "try again in a minute."))


def refused_text() -> str:
    return "🚫 " + esc(_("The AI provider's own safety check refused this, so nothing was "
                        "charged."))


def files_text() -> str:
    return _("Photos, voice notes and audio files work here. Other files are coming soon.")


# --------------------------------------------------------------------------
# Assistants
# --------------------------------------------------------------------------

def assistants_text(assistants) -> str:
    lines = [f"🧩 {_bold(_('Assistants'))}", "",
             esc(_("Ready-made helpers. Each starts a new chat that knows its job."))]
    for assistant in assistants:
        lines.append(f"\n{esc(assistant.emoji)} <b>{esc(catalog.tr(assistant.name))}</b>\n"
                     f"<i>{esc(catalog.tr(assistant.description))}</i>")
    return "\n".join(lines)


def assistants_keyboard(assistants):
    rows, row = [], []
    for assistant in assistants:
        row.append({"text": f"{assistant.emoji} {catalog.tr(assistant.name)}".strip(),
                    "callback_data": f"asst:start:{assistant.slug}"})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(nav_row())
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
    return f"🖌 {_bold(_('Edit a photo'))}\n\n" + esc(_("Send the photo you want to change."))


def ask_change_text() -> str:
    return (f"🖌 {_bold(_('Edit a photo'))}\n\n"
            + esc(_("What should change? Describe it, in any language.")))


def cancel_keyboard(back):
    return _inline([[{"text": "✖️ " + _("Cancel"), "callback_data": back}], [menu_button()]])


def image_summary_text(draft, model, hold, balance) -> str:
    title = _("Your photo edit") if draft.get("photo") else _("Your image")
    lines = [f"🎨 {_bold(title)}", "", f"<i>{esc(draft['prompt'])}</i>", "",
             f"{esc(_('Shape'))}: {esc(size_name(draft['size']))} · "
             f"{esc(_('Quality'))}: {esc(quality_name(draft['quality']))}",
             f"{esc(_('Model'))}: {esc(model)}", "", price_line(hold, balance)]
    return "\n".join(lines)


def image_summary_keyboard(draft, hold, enough):
    sizes = (("auto",) if draft.get("photo") else ()) + SIZES
    shapes = [{"text": _tick(size == draft["size"], size_name(size)),
               "callback_data": f"img:size:{size}"} for size in sizes]
    # Three shapes fit a row; with "As the photo", two rows of two.
    rows = [shapes] if len(shapes) == 3 else [shapes[:2], shapes[2:]]
    rows.append([{"text": _tick(quality == draft["quality"], quality_name(quality)),
                  "callback_data": f"img:quality:{quality}"} for quality in QUALITIES])
    if enough:
        label = _("Edit it") if draft.get("photo") else _("Create it")
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


def image_keyboard(job, original):
    rows = [[{"text": "🔁 " + _("Again"), "callback_data": f"out:again:{job.pk}"},
             {"text": "🖌 " + _("Edit this"), "callback_data": f"out:edit:{job.pk}"}]]
    if original:
        rows.append([{"text": "📎 " + _("Original file"), "callback_data": f"out:file:{job.pk}"}])
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
