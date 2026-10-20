"""
Chat: a typed message (or a photo with a question, or a voice note turned
into text) goes to the AI with the chat so far, and the answer is written
into one message as it comes, then formatted.

The price: the most an answer can cost (the chat so far, the photos, and the
model's longest answer) is held before it is asked; what the provider says
it really cost is charged; the rest goes back. Each answer ends with what it
cost and the balance.
"""

import time

from django.db.models import Count
from django.utils import timezone

from apps.ai import clients
from apps.ai.models import AIModel, Use
from apps.core import runtime
from apps.core.i18n import gettext as _
from apps.core.services import telegram
from apps.safety import checks
from apps.safety.models import Result
from apps.safety.texts import category_text

from . import archive, demo, formatting, jobs, texts
from .models import ACTIVE, Conversation, Job, Kind, Message, Profile, Role

SYSTEM = """\
You are the AI assistant of AIstars, chatting with a user in Telegram. Today is {today}.
Answer in the language the user writes in, unless they ask for another.
Telegram shows **bold**, *italic*, `code`, ``` code blocks ``` and [links](https://example.com),
but not tables or HTML: use short lists instead of tables.
Be helpful and to the point; write long answers only when they are asked for or needed.
You cannot browse the web, open files other than photos, or see other chats."""

ROLE = "\n\nYour role in this chat:\n{instructions}"

# A rough count, on the safe side: tokens are fewer than characters ÷ 2.5 in
# most languages, and a photo is read as at most about 1,600 tokens.
CHARS_PER_TOKEN = 2.5
PHOTO_TOKENS = 1600
# An everyday answer, for the price shown next to each model.
TYPICAL = (1500, 700)
# How often an answer being written is shown (Telegram allows about one edit
# a second per chat).
EDIT_EVERY = 1.5


# --------------------------------------------------------------------------
# The chat a user is in, and its model
# --------------------------------------------------------------------------

def profile(account) -> Profile:
    found, _ = Profile.objects.get_or_create(account=account)
    return found


def current(account):
    return (Conversation.objects.filter(account=account)
            .select_related("model", "model__provider", "assistant").first())


def model_of(account, conversation=None):
    """The chat's model if users can have it, else the user's choice, else the default."""
    is_demo = jobs.demo()
    for model in (getattr(conversation, "model", None), profile(account).chat_model):
        if model is not None and model.use == Use.CHAT and model.ready_for(is_demo):
            return model
    return AIModel.default_for(Use.CHAT, is_demo)


def new_conversation(account, assistant=None) -> Conversation:
    """A new chat; an empty current one is reused rather than piling up."""
    model = (assistant.model if assistant is not None and assistant.model is not None
             and assistant.model.ready_for(jobs.demo()) else model_of(account))
    conversation = current(account)
    if conversation is not None and not conversation.messages.exists():
        conversation.assistant, conversation.model, conversation.title = assistant, model, ""
        conversation.save()
        return conversation
    return Conversation.objects.create(account=account, model=model, assistant=assistant)


def choose_model(account, conversation, model):
    """For this chat and the next ones."""
    Profile.objects.filter(pk=profile(account).pk).update(chat_model=model)
    if conversation is not None:
        Conversation.objects.filter(pk=conversation.pk).update(model=model,
                                                               updated_at=timezone.now())


def listed(account, page=0, per_page=8):
    """(chats with messages, newest first; has_more)."""
    rows = list(Conversation.objects.filter(account=account)
                .annotate(count=Count("messages")).filter(count__gt=0).order_by("-updated_at")
                [page * per_page:(page + 1) * per_page + 1])
    return rows[:per_page], len(rows) > per_page


def typical_price(model) -> int:
    return jobs.hold_for(model.cost_micros(*TYPICAL), model)


def listen_price(text):
    """The price on an answer's 🔊 Listen button, or None when nobody can read it."""
    model = AIModel.default_for(Use.SPEECH, jobs.demo())
    if model is None or not text:
        return None
    from .voice import TTS_LIMIT

    return jobs.hold_for(model.speech_cost_micros(min(len(text), TTS_LIMIT)), model)


# --------------------------------------------------------------------------
# A message in
# --------------------------------------------------------------------------

def system_prompt(conversation) -> str:
    prompt = SYSTEM.format(today=timezone.localdate().isoformat())
    if conversation.assistant_id and conversation.assistant.instructions:
        prompt += ROLE.format(instructions=conversation.assistant.instructions)
    return prompt


def history(conversation, upto=None) -> list:
    """The last messages of the chat, oldest first, up to `upto` (a Message id)."""
    rows = conversation.messages.order_by("-id")
    if upto is not None:
        rows = rows.filter(id__lte=upto)
    limit = int(runtime.get("CHAT_CONTEXT_MESSAGES")) + 1
    return list(rows[:limit])[::-1]


def estimate(model, conversation, text, photos) -> int:
    """The most an answer can cost, in micro-USD."""
    chars = len(system_prompt(conversation)) + len(text) + sum(
        len(message.text) for message in history(conversation))
    tokens_in = int(chars / CHARS_PER_TOKEN) + PHOTO_TOKENS * len(photos)
    return model.cost_micros(tokens_in, model.max_output_tokens)


def send(account, chat_id, text, photos=(), voice=False, conversation=None):
    """A message from the user: checked, priced and held, then answered in the background."""
    photos = list(photos)
    limit = int(runtime.get("CHAT_MAX_MESSAGE_CHARS"))
    if len(text) > limit:
        return _notice(chat_id, texts.too_long_text(limit))
    conversation = conversation or current(account) or new_conversation(account)
    model = model_of(account, conversation)
    if model is None:
        return _notice(chat_id, texts.no_model_text())
    if conversation.model_id != model.pk:
        Conversation.objects.filter(pk=conversation.pk).update(model=model)
        conversation.model = model
    if photos and not model.sees_images:
        return _notice(chat_id, texts.no_photos_text(model))
    if Job.objects.filter(conversation=conversation, kind=Kind.CHAT,
                          status__in=ACTIVE).exists():
        return _notice(chat_id, texts.busy_text())

    screening = checks.screen(account, "chat", model.model_id, text,
                              photos=[{"file_id": file_id, "kind": "photo"} for file_id in photos],
                              demo=jobs.demo())
    if not _passed(account, chat_id, screening):
        return None
    try:
        job = jobs.start(account, chat_id, Kind.CHAT, model,
                         estimate(model, conversation, text, photos),
                         conversation=conversation, prompt=text, photos=photos,
                         screening=screening)
    except jobs.NotEnough as exc:
        return telegram.send_message(chat_id, texts.not_enough_text(exc.hold, exc.balance),
                                     reply_markup=texts.not_enough_keyboard())
    except jobs.TooMany:
        return _notice(chat_id, texts.too_many_text())
    Message.objects.create(conversation=conversation, role=Role.USER, text=text, photos=photos,
                           voice=voice, job=job)
    jobs.touch_conversation(conversation)
    for file_id in photos:
        archive.copy_input(job, "photo", file_id)
    _begin(job)
    return job


def retry(account, chat_id, failed_job):
    """The same message again, after its answer failed."""
    message = Message.objects.filter(job=failed_job, role=Role.USER).select_related(
        "conversation").first()
    if message is None or failed_job.account_id != account.pk:
        return _notice(chat_id, texts.no_model_text())
    conversation = message.conversation
    model = model_of(account, conversation)
    if model is None:
        return _notice(chat_id, texts.no_model_text())
    if Job.objects.filter(conversation=conversation, kind=Kind.CHAT,
                          status__in=ACTIVE).exists():
        return _notice(chat_id, texts.busy_text())
    try:
        job = jobs.start(account, chat_id, Kind.CHAT, model,
                         estimate(model, conversation, "", message.photos),
                         conversation=conversation, prompt=message.text, photos=message.photos,
                         screening=failed_job.screening)
    except jobs.NotEnough as exc:
        return telegram.send_message(chat_id, texts.not_enough_text(exc.hold, exc.balance),
                                     reply_markup=texts.not_enough_keyboard())
    except jobs.TooMany:
        return _notice(chat_id, texts.too_many_text())
    Message.objects.filter(pk=message.pk).update(job=job)
    jobs.touch_conversation(conversation)
    _begin(job)
    return job


def _begin(job):
    """The message the answer will be written into, then the background work."""
    sent = telegram.send_message(job.chat_id, texts.thinking_text())
    if sent:
        jobs.note_message(job, sent["message_id"])
    telegram.send_chat_action(job.chat_id)
    jobs.launch(job)


def _passed(account, chat_id, screening) -> bool:
    """Tell the user when the check stopped it."""
    if screening.result == Result.BLOCKED:
        telegram.send_message(chat_id, texts.blocked_text(category_text(screening.category)),
                              reply_markup=texts.notice_keyboard())
        if account.is_blocked:
            from apps.core import menus

            telegram.send_message(chat_id, menus.blocked_text())
        return False
    if screening.result == Result.UNAVAILABLE:
        telegram.send_message(chat_id, texts.unchecked_text(),
                              reply_markup=texts.notice_keyboard())
        return False
    return True


def _notice(chat_id, text):
    """A one-line plain-text answer, with the way back to the menu."""
    telegram.send_message(chat_id, telegram.esc(text), reply_markup=texts.notice_keyboard())
    return None


# --------------------------------------------------------------------------
# The answer (in the background)
# --------------------------------------------------------------------------

class Writer:
    """Shows the answer as it grows, at most every EDIT_EVERY seconds."""

    def __init__(self, job):
        self.job, self.last = job, time.monotonic()

    def __call__(self, text):
        now = time.monotonic()
        if not self.job.message_id or now - self.last < EDIT_EVERY:
            return
        self.last = now
        telegram.edit_message(self.job.chat_id, self.job.message_id,
                              formatting.preview(text) + " ▍", parse_mode=None)


def answer(job):
    conversation = job.conversation
    question = Message.objects.filter(job=job, role=Role.USER).first()
    messages = _context(conversation, question)
    if job.demo:
        reply = demo.chat_reply(job, Writer(job))
    else:
        reply = clients.chat(job.model, system_prompt(conversation), messages, purpose="chat",
                             on_text=Writer(job), estimate_micros=job.estimate_micros)
    Message.objects.create(conversation=conversation, role=Role.ASSISTANT, text=reply.text,
                           job=job)
    if not conversation.title:
        first = conversation.messages.filter(role=Role.USER).order_by("id").first()
        title = " ".join((first.text if first else "").split())[:60] or _("Photo")
        Conversation.objects.filter(pk=conversation.pk).update(title=title)
    balance = jobs.done(job, reply.cost_micros)
    _write(job, reply, balance)


def _context(conversation, question) -> list:
    """The chat for the API: roles alternate, and only the newest photos are sent."""
    rows = history(conversation, upto=question.pk if question else None)
    messages = []
    for message in rows:
        text = message.text
        images = []
        if message.photos:
            if question is not None and message.pk == question.pk:
                images = [(_photo(file_id), "image/jpeg") for file_id in message.photos]
            else:
                text = f"[{_('photo')}] {text}".strip()
        if messages and messages[-1]["role"] == message.role:
            messages[-1]["text"] += "\n\n" + text
            messages[-1]["images"] += images
        else:
            messages.append({"role": message.role, "text": text or "…", "images": images})
    return messages


def _photo(file_id) -> bytes:
    from apps.safety import images

    try:
        return images.prepare(file_id)
    except ValueError as exc:
        raise clients.AIError(f"the photo could not be read: {exc}") from None


def _write(job, reply, balance):
    """The finished answer, formatted, with what it cost and the buttons."""
    text = reply.text.strip()
    parts = formatting.messages(text) if text else [(telegram.esc(texts.empty_answer_text()),
                                                     texts.empty_answer_text())]
    extra_html = extra_plain = ""
    if reply.cut_off and text:
        extra_html = f"\n\n<i>{telegram.esc(texts.cut_off_text())}</i>"
        extra_plain = f"\n\n{texts.cut_off_text()}"
    footer = texts.footer(job.charged, balance, job.model)
    footer_plain = footer.replace("<i>", "").replace("</i>", "")
    answer_row = Message.objects.filter(job=job, role=Role.ASSISTANT).first()
    markup = texts.answer_keyboard(answer_row.pk, listen_price(text))

    last = len(parts) - 1
    for number, (html_text, plain_text) in enumerate(parts):
        if number == last:
            html_text += f"{extra_html}\n\n{footer}"
            plain_text += f"{extra_plain}\n\n{footer_plain}"
        keyboard = markup if number == last else None
        if number == 0 and job.message_id and telegram.edit_formatted(
                job.chat_id, job.message_id, html_text, plain_text, keyboard):
            continue
        telegram.send_formatted(job.chat_id, html_text, plain_text, keyboard)

