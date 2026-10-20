"""
Voice:

    a voice note in a chat   turned into text, which the AI then answers
    Voice to text            a voice note or audio file, back as text
    Text to speech           a text read aloud, back as a voice note; also
                             the 🔊 Listen button under every answer

Both are priced by size before they start (minutes of audio, characters
read), so the price shown is what is charged.
"""

from apps.ai import clients
from apps.ai.models import AIModel, Use
from apps.billing import ledger
from apps.core.services import telegram, ui

from . import archive, demo, formatting, jobs, state, texts
from .models import Conversation, Job, Kind

MAX_SECONDS = 20 * 60
TTS_LIMIT = 4000


def voice_of(account) -> str:
    from .chat import profile

    chosen = profile(account).voice
    return chosen if chosen in texts.VOICES else texts.VOICES[0]


def stt_model():
    return AIModel.default_for(Use.TRANSCRIPTION, jobs.demo())


def tts_model():
    return AIModel.default_for(Use.SPEECH, jobs.demo())


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

def show(account, chat_id):
    stt, tts = stt_model() is not None, tts_model() is not None
    ui.screen(account, chat_id, texts.voice_text(voice_of(account), stt, tts),
              texts.voice_keyboard(stt, tts))


def show_voices(account, chat_id):
    ui.screen(account, chat_id, texts.voices_text(), texts.voices_keyboard(voice_of(account)))


def choose_voice(account, chat_id, value):
    from .chat import profile
    from .models import Profile

    if value in texts.VOICES:
        Profile.objects.filter(pk=profile(account).pk).update(voice=value)
    draft = state.waiting(account)
    if draft and draft.get("step") == "speech_summary":
        return summary(account, chat_id, draft["text"])
    show(account, chat_id)
    return None


def ask_audio(account, chat_id):
    state.wait(account, "transcribe")
    ui.screen(account, chat_id, texts.ask_voice_text(), texts.cancel_keyboard("voice:"))


def ask_text(account, chat_id):
    state.wait(account, "speech_text")
    ui.screen(account, chat_id, texts.ask_speech_text(), texts.cancel_keyboard("voice:"))


# --------------------------------------------------------------------------
# Voice to text
# --------------------------------------------------------------------------

def start_transcription(account, chat_id, audio, then_chat=False, attachments=()):
    """
    Voice notes or audio files in (several when sent as one album): their
    text, and with `then_chat` the AI's answer to it, with `attachments`.
    """
    model = stt_model()
    if model is None:
        return _notice(chat_id, texts.no_model_text())
    if not all(item["seconds"] for item in audio):
        return _notice(chat_id, texts.no_duration_text())
    if sum(item["seconds"] for item in audio) > MAX_SECONDS:
        return _notice(chat_id, texts.too_long_audio_text())
    conversation = None
    if then_chat:
        from . import chat

        conversation = chat.current(account) or chat.new_conversation(account)
    try:
        job = jobs.start(account, chat_id, Kind.TRANSCRIBE, model,
                         sum(model.audio_cost_micros(item["seconds"]) for item in audio),
                         photos=[item["file_id"] for item in audio], conversation=conversation,
                         options={"audio": list(audio), "then_chat": then_chat,
                                  "attachments": list(attachments)})
    except jobs.NotEnough as exc:
        return telegram.send_message(chat_id, texts.not_enough_text(exc.hold, exc.balance),
                                     reply_markup=texts.not_enough_keyboard(chat=then_chat))
    except jobs.LimitReached as exc:
        from .chat import limit_notice

        return limit_notice(chat_id, exc)
    except jobs.TooMany:
        return _notice(chat_id, texts.too_many_text())
    state.stop(account)
    for item in audio:
        archive.copy_input(job, item["kind"], item["file_id"])
    sent = telegram.send_message(chat_id, texts.listening_text())
    if sent:
        jobs.note_message(job, sent["message_id"])
    jobs.launch(job)
    return job


def transcribe(job):
    heard, cost = [], 0
    for item in job.options.get("audio") or []:
        if job.demo:
            text, price = demo.transcript(item["seconds"]), job.model.audio_cost_micros(
                item["seconds"])
        else:
            try:
                audio = telegram.download_file(item["file_id"])
            except ValueError as exc:
                raise clients.AIError(f"the audio could not be had: {exc}") from None
            text, price = clients.transcribe(job.model, audio, item.get("name") or "voice.ogg",
                                             item["seconds"])
        if text:
            heard.append(text)
        cost += price
    text = "\n\n".join(heard)
    balance = jobs.done(job, cost)
    then_chat = job.options.get("then_chat")
    shown = texts.transcript_text(text, then_chat) if text else telegram.esc(
        texts.nothing_heard_text())
    low = texts.low_balance(balance)
    if then_chat:
        _write(job, shown, texts.footer(job.charged, balance), texts.result_keyboard(job, low))
        if text:
            from . import chat

            conversation = Conversation.objects.filter(pk=job.conversation_id).first()
            chat.send(job.account, job.chat_id, text, voice=True, conversation=conversation,
                      attachments=job.options.get("attachments") or [])
        return
    if not text:
        _write(job, shown, texts.footer(job.charged, balance), texts.result_keyboard(job, low))
        return
    parts = formatting.split_plain(text)
    last = len(parts) - 1
    for number, part in enumerate(parts):
        body = ("📝 " if number == 0 else "") + telegram.esc(part)
        if number == last:
            _write(job, body, texts.footer(job.charged, balance),
                   texts.result_keyboard(job, low), first=number == 0)
        else:
            _write(job, body, "", None, first=number == 0)


def _write(job, html_text, footer, markup=None, first=True):
    """Into the "🎙 …" message for the first part, then as new messages."""
    body = f"{html_text}\n\n{footer}" if footer else html_text
    if first and job.message_id and telegram.edit_message(job.chat_id, job.message_id, body,
                                                          markup):
        return
    telegram.send_message(job.chat_id, body, reply_markup=markup)


# --------------------------------------------------------------------------
# Text to speech
# --------------------------------------------------------------------------

def price(model, text) -> int:
    return jobs.hold_for(model.speech_cost_micros(len(text)), model)


def summary(account, chat_id, text, fresh=False):
    """What will be read, in which voice, and its price."""
    model = tts_model()
    show_screen = ui.fresh_screen if fresh else ui.screen
    if model is None:
        state.stop(account)
        return show_screen(account, chat_id, telegram.esc(texts.no_model_text()),
                           texts.cancel_keyboard("voice:"))
    text = text[:TTS_LIMIT]
    cost, balance = price(model, text), ledger.balance(account)
    state.wait(account, "speech_summary", text=text)
    return show_screen(account, chat_id,
                       texts.speech_summary_text(text, voice_of(account), cost, balance),
                       texts.speech_summary_keyboard(cost, cost <= balance))


def go(account, chat_id):
    draft = state.waiting(account)
    if not draft or draft.get("step") != "speech_summary":
        show(account, chat_id)
        return ""
    job = start_speech(account, chat_id, draft["text"])
    if job is not None:
        state.stop(account)
        ui.screen(account, chat_id, texts.reading_text(job))
        ui.forget_screen(account)
    return ""


def start_speech(account, chat_id, text, screen_check=True):
    """Checked like a chat message (it is the user's text), then read in the background."""
    from apps.safety import checks
    from apps.safety.models import Result
    from apps.safety.texts import category_text

    model = tts_model()
    if model is None:
        return _notice(chat_id, texts.no_model_text())
    text = text[:TTS_LIMIT]
    screening = None
    if screen_check:
        screening = checks.screen(account, "speech", model.model_id, text, demo=jobs.demo())
        if screening.result != Result.PASSED:
            telegram.send_message(
                chat_id, texts.blocked_text(category_text(screening.category))
                if screening.result == Result.BLOCKED else texts.unchecked_text(),
                reply_markup=texts.notice_keyboard())
            return None
    try:
        job = jobs.start(account, chat_id, Kind.SPEECH, model, model.speech_cost_micros(len(text)),
                         prompt=text, screening=screening, options={"voice": voice_of(account)})
    except jobs.NotEnough as exc:
        telegram.send_message(chat_id, texts.not_enough_text(exc.hold, exc.balance),
                              reply_markup=texts.not_enough_keyboard(chat=False))
        return None
    except jobs.LimitReached as exc:
        from .chat import limit_notice

        return limit_notice(chat_id, exc)
    except jobs.TooMany:
        return _notice(chat_id, texts.too_many_text())
    jobs.launch(job)
    return job


def speak(job):
    voice = job.options.get("voice") or texts.VOICES[0]
    if job.demo:
        data, cost = demo.voice(), job.estimate_micros
    else:
        data, cost = clients.speak(job.model, job.prompt, voice)
    job.charged, balance = jobs.charge_preview(job, cost)
    caption = texts.speech_caption(job, balance)
    markup = texts.result_keyboard(job, texts.low_balance(balance))
    stored = archive.store_output(job, "voice", data, f"speech-{job.pk}.ogg", "audio/ogg")
    sent = None
    if stored:
        sent = telegram.send_media(job.chat_id, "voice", telegram.file_id_of(stored, "voice"),
                                   caption, markup)
    if sent is None:
        sent = telegram.send_media(job.chat_id, "voice", data, caption, markup,
                                   filename=f"speech-{job.pk}.ogg", content_type="audio/ogg")
    if sent is None:
        raise clients.AIError("Telegram took the voice note nowhere")
    Job.objects.filter(pk=job.pk).update(result_file_id=telegram.file_id_of(sent, "voice"),
                                         archive_message_id=(stored or {}).get("message_id"))
    jobs.done(job, cost)


def listen(account, chat_id, message):
    """🔊 Listen under an answer: the answer read aloud (it was the AI's, not checked)."""
    return start_speech(account, chat_id, message.text, screen_check=False)


def _notice(chat_id, text):
    telegram.send_message(chat_id, telegram.esc(text), reply_markup=texts.notice_keyboard())
    return None
