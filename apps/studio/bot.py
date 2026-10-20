"""
The AI tools in the bot, called from apps.core.views inside the user's
language. Anything typed that is not an answer to a question the bot asked
is a chat message; photos and voice notes go to the chat too, unless a tool
asked for one.

Buttons: chat:, asst:, img:, voice: on the panel; out: on messages that are
not the panel (an answer, an image, a notice).
"""

from apps.ai.models import AIModel, Use
from apps.billing import ledger
from apps.billing import texts as billing_texts
from apps.core import menus
from apps.core.i18n import gettext as _
from apps.core.services import telegram, ui

from . import chat, images, jobs, state, texts, voice
from .models import Assistant, Conversation, Job, Message, Role

IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")


# --------------------------------------------------------------------------
# The home panel
# --------------------------------------------------------------------------

def home_lines(account) -> list:
    if not jobs.may_use(account):
        return []
    return texts.home_lines(chat.model_of(account, chat.current(account)),
                            ledger.balance(account))


def home_rows(account) -> list:
    return texts.home_rows() if jobs.may_use(account) else []


def stop_waiting(account):
    state.stop(account)


# --------------------------------------------------------------------------
# Commands, typed text, files
# --------------------------------------------------------------------------

def handle_command(account, chat_id, command) -> bool:
    if not jobs.may_use(account) or command not in ("new", "chat", "images", "voice"):
        return False
    if command == "new":
        chat.new_conversation(account)
        _chat_screen(account, chat_id, fresh=True)
    elif command == "chat":
        _chat_screen(account, chat_id, fresh=True)
    elif command == "images":
        images.show(account, chat_id, fresh=True)
    else:
        ui.clear_screen(account, chat_id)
        voice.show(account, chat_id)
    return True


def handle_text(account, chat_id, text) -> bool:
    """False when the tools are not open to this account."""
    if not jobs.may_use(account):
        return False
    draft = state.waiting(account) or {}
    step = draft.get("step")
    if step in ("image_prompt", "edit_prompt", "image_summary"):
        # A new description, or the first one.
        images.got_prompt(account, chat_id, text, draft)
    elif step in ("speech_text", "speech_summary"):
        voice.summary(account, chat_id, text, fresh=True)
    else:
        # Waiting for a photo or a voice note, but a text came: it is a chat.
        state.stop(account)
        chat.send(account, chat_id, text)
    return True


def handle_media(account, chat_id, message) -> bool:
    if not jobs.may_use(account):
        return False
    draft = state.waiting(account) or {}
    step = draft.get("step")
    photo = _photo_of(message)
    audio = voice.audio_of(message)
    caption = (message.get("caption") or "").strip()
    if photo:
        if step in ("edit_photo", "edit_prompt", "image_summary"):
            images.got_photo(account, chat_id, photo,
                             {**draft, "prompt": caption} if caption else draft)
        else:
            state.stop(account)
            chat.send(account, chat_id, caption or _("What is in this photo?"), photos=[photo])
    elif audio:
        voice.start_transcription(account, chat_id, audio, then_chat=step != "transcribe")
    else:
        telegram.send_message(chat_id, telegram.esc(texts.files_text()),
                              reply_markup=texts.notice_keyboard())
    return True


def _photo_of(message):
    if message.get("photo"):
        return message["photo"][-1]["file_id"]
    document = message.get("document") or {}
    if document.get("mime_type") in IMAGE_TYPES:
        return document["file_id"]
    return None


# --------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------

def handle_callback(account, chat_id, verb, argument):
    """The toast to show ('' for none), or None for a verb that is not ours."""
    handler = {"chat": _chat, "asst": _assistants, "img": _images, "voice": _voice,
               "out": _out}.get(verb)
    if handler is None:
        return None
    if not jobs.may_use(account):
        ui.screen(account, chat_id, menus.home_text(account), menus.home_keyboard(account))
        return ""
    action, _, value = argument.partition(":")
    if verb != "out" and not (verb == "img" and action in ("size", "quality", "go", "prompt")) \
            and not (verb == "voice" and action in ("go", "voices", "set")):
        # Leaving a question unanswered: typed text is a chat message again.
        state.stop(account)
    return handler(account, chat_id, action, value) or ""


def _chat_screen(account, chat_id, fresh=False, conversation=None):
    conversation = conversation or chat.current(account)
    model = chat.model_of(account, conversation)
    count = conversation.messages.count() if conversation is not None else 0
    has_chats = Conversation.objects.filter(account=account, messages__isnull=False).exists()
    (ui.fresh_screen if fresh else ui.screen)(
        account, chat_id, texts.chat_text(conversation, model, count),
        texts.chat_keyboard(has_chats))


def _own_conversation(account, value):
    return Conversation.objects.filter(pk=int(value) if value.isdigit() else 0,
                                       account=account).select_related("assistant").first()


def _chat(account, chat_id, action, value):
    if action == "new":
        chat.new_conversation(account)
        _chat_screen(account, chat_id)
    elif action == "models":
        _models_screen(account, chat_id)
    elif action == "model":
        models = {str(model.pk): model
                  for model in AIModel.offered_for(Use.CHAT, jobs.demo())}
        if value in models:
            chat.choose_model(account, chat.current(account), models[value])
            _chat_screen(account, chat_id)
            return f"🤖 {models[value]}"
        _models_screen(account, chat_id)
    elif action == "list":
        page = int(value) if value.isdigit() else 0
        rows, has_more = chat.listed(account, page)
        ui.screen(account, chat_id, texts.chats_text(rows, page),
                  texts.chats_keyboard(rows, page, has_more))
    elif action == "open":
        conversation = _own_conversation(account, value)
        if conversation is None:
            return _chat(account, chat_id, "list", "0")
        jobs.touch_conversation(conversation)
        last = conversation.messages.order_by("-id").first()
        ui.screen(account, chat_id,
                  texts.opened_text(conversation, chat.model_of(account, conversation),
                                    conversation.messages.count(), last),
                  texts.opened_keyboard(conversation))
    elif action == "del":
        conversation = _own_conversation(account, value)
        if conversation is None:
            return _chat(account, chat_id, "list", "0")
        ui.screen(account, chat_id, texts.delete_text(conversation),
                  texts.delete_keyboard(conversation))
    elif action == "delok":
        conversation = _own_conversation(account, value)
        if conversation is not None:
            conversation.delete()
        _chat(account, chat_id, "list", "0")
        return "🗑 " + _("Deleted")
    else:
        _chat_screen(account, chat_id)
    return ""


def _models_screen(account, chat_id, fresh=False):
    models = AIModel.offered_for(Use.CHAT, jobs.demo())
    current = chat.model_of(account, chat.current(account))
    typical = {model.pk: chat.typical_price(model) for model in models}
    (ui.fresh_screen if fresh else ui.screen)(
        account, chat_id, texts.models_text(models, typical),
        texts.models_keyboard(models, current))


def _assistants(account, chat_id, action, value):
    if action == "start":
        assistant = Assistant.objects.filter(slug=value, active=True).first()
        if assistant is not None:
            conversation = chat.new_conversation(account, assistant)
            _chat_screen(account, chat_id, conversation=conversation)
            return ""
    assistants = list(Assistant.objects.filter(active=True))
    ui.screen(account, chat_id, texts.assistants_text(assistants),
              texts.assistants_keyboard(assistants))
    return ""


def _images(account, chat_id, action, value):
    if action == "create":
        images.ask_description(account, chat_id)
    elif action == "edit":
        images.ask_photo(account, chat_id)
    elif action == "models":
        images.show_models(account, chat_id)
    elif action == "model":
        return images.choose_model(account, chat_id, value)
    elif action in ("size", "quality"):
        return images.set_option(account, chat_id, action, value)
    elif action == "go":
        return images.go(account, chat_id)
    elif action == "prompt":
        images.change_prompt(account, chat_id)
    else:
        images.show(account, chat_id)
    return ""


def _voice(account, chat_id, action, value):
    if action == "stt":
        voice.ask_audio(account, chat_id)
    elif action == "tts":
        voice.ask_text(account, chat_id)
    elif action == "voices":
        voice.show_voices(account, chat_id)
    elif action == "set":
        voice.choose_voice(account, chat_id, value)
        return f"🗣 {texts.voice_name(value)}" if value in texts.VOICES else ""
    elif action == "go":
        return voice.go(account, chat_id)
    else:
        voice.show(account, chat_id)
    return ""


def _own_job(account, value):
    return Job.objects.filter(pk=int(value) if value.isdigit() else 0,
                              account=account).select_related("model").first()


def _out(account, chat_id, action, value):
    """Buttons on an answer, an image or a notice: that message is left as it is."""
    if action == "say":
        message = Message.objects.filter(pk=int(value) if value.isdigit() else 0,
                                         role=Role.ASSISTANT,
                                         conversation__account=account).first()
        if message is not None:
            voice.listen(account, chat_id, message)
            return "🔊 …"
    elif action == "new":
        state.stop(account)
        chat.new_conversation(account)
        _chat_screen(account, chat_id, fresh=True)
    elif action == "retry":
        job = _own_job(account, value)
        if job is not None:
            chat.retry(account, chat_id, job)
    elif action == "again":
        job = _own_job(account, value)
        if job is not None:
            images.again(account, chat_id, job)
    elif action == "edit":
        job = _own_job(account, value)
        if job is not None:
            images.edit_result(account, chat_id, job)
    elif action == "file":
        job = _own_job(account, value)
        if job is not None:
            images.send_original(chat_id, job)
    elif action == "buy":
        from apps.billing.models import Pack

        packs = list(Pack.objects.filter(active=True))
        ui.fresh_screen(account, chat_id, billing_texts.packs_text(packs),
                        billing_texts.packs_keyboard(packs))
    elif action == "models":
        _models_screen(account, chat_id, fresh=True)
    return ""
