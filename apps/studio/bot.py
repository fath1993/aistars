"""
The AI tools in the bot, called from apps.core.views inside the user's
language. Anything typed that is not an answer to a question the bot asked
is a chat message; photos, files and voice notes go to the chat too, unless
a tool asked for them.

Buttons: chat:, ag: (agents), img:, voice: and use: (usage) on the panel;
out: on messages that are not the panel (an answer, an image, a notice).
"""

from django.core.cache import cache

from apps.ai.models import AIModel, Use
from apps.billing import ledger
from apps.billing import texts as billing_texts
from apps.core import menus
from apps.core.i18n import gettext as _
from apps.core.services import telegram, ui

from . import agents, catalog, chat, images, jobs, state, texts, usage, voice
from .models import Category, Conversation, Job, Message, Role

COMMANDS = ("new", "chat", "chats", "agents", "usage", "images", "voice")


# --------------------------------------------------------------------------
# The home panel
# --------------------------------------------------------------------------

def home_lines(account) -> list:
    if not jobs.may_use(account):
        return []
    conversation = chat.current(account)
    return texts.home_lines(conversation, chat.model_of(account, conversation),
                            ledger.balance(account))


def home_rows(account) -> list:
    if not jobs.may_use(account):
        return []
    conversation = chat.current(account)
    return texts.home_rows(conversation is not None and conversation.messages.exists())


def account_buttons(account) -> list:
    """Next to 🪙 Credits on the home panel."""
    return [texts.usage_button()] if jobs.may_use(account) else []


def stop_waiting(account):
    state.stop(account)


def _show(account, chat_id, text, markup, fresh=False):
    """On the panel; `fresh` draws it at the bottom of the chat (after typing, a command)."""
    (ui.fresh_screen if fresh else ui.screen)(account, chat_id, text, markup)


# --------------------------------------------------------------------------
# Commands, typed text, files, links
# --------------------------------------------------------------------------

def handle_command(account, chat_id, command) -> bool:
    if not jobs.may_use(account) or command not in COMMANDS:
        return False
    if command == "new":
        _new_chat(account, chat_id, fresh=True)
    elif command == "chat":
        _chat_screen(account, chat_id, fresh=True)
    elif command == "chats":
        _chats(account, chat_id, 0, fresh=True)
    elif command == "agents":
        _agents(account, chat_id, "", "", fresh=True)
    elif command == "usage":
        _usage(account, chat_id, "", "", fresh=True)
    elif command == "images":
        images.show(account, chat_id, fresh=True)
    else:
        ui.clear_screen(account, chat_id)
        voice.show(account, chat_id)
    return True


def open_start(account, chat_id, payload) -> bool:
    """/start agent_<slug>, from a shared link: the agent's card."""
    agent = agents.from_start(payload)
    if agent is None or not jobs.may_use(account):
        return False
    _remember_back(account, {"kind": "home"})
    _card(account, chat_id, agent, fresh=True)
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
    elif step == "agent_search":
        state.stop(account)
        found = agents.search(text)
        _remember_back(account, {"kind": "search", "words": text[:200],
                                 "slugs": [agent.slug for agent in found]})
        _show(account, chat_id, texts.list_text("search", found, text),
              texts.list_keyboard("search", found), fresh=True)
    elif step == "agent_find":
        state.stop(account)
        _show(account, chat_id, texts.finding_text(), menus.back_keyboard("ag:"), fresh=True)
        from . import tasks

        tasks.find_agents.delay(account.pk, chat_id, text[:1000])
    else:
        # Waiting for a photo or a voice note, but a text came: it is a chat.
        state.stop(account)
        chat.send(account, chat_id, text)
    return True


def found(account, chat_id, need):
    """The agents the AI found for `need` (in the background), on the panel."""
    result = agents.find(account, need)
    _remember_back(account, {"kind": "find", "words": need[:200],
                             "slugs": [agent.slug for agent in result]})
    _show(account, chat_id, texts.list_text("find", result, need),
          texts.list_keyboard("find", result))


def handle_media(account, chat_id, message) -> bool:
    """Photos, files and voice notes: one message, however many it carries (album.py)."""
    if not jobs.may_use(account):
        return False
    from . import album

    album.add(account, chat_id, message)
    return True


# --------------------------------------------------------------------------
# Buttons
# --------------------------------------------------------------------------

def handle_callback(account, chat_id, verb, argument, message_id=None):
    """The answer to show ('' for none), or None for a verb that is not ours."""
    handler = {"chat": _chat, "ag": _agents, "img": _images, "voice": _voice, "use": _usage,
               "out": _out}.get(verb)
    if handler is None:
        return None
    if not jobs.may_use(account):
        ui.screen(account, chat_id, menus.home_text(account), menus.home_keyboard(account))
        return ""
    action, _sep, value = argument.partition(":")
    if verb != "out" and not (verb == "img" and action in ("size", "quality", "go", "prompt")) \
            and not (verb == "voice" and action in ("go", "voices", "set")):
        # Leaving a question unanswered: typed text is a chat message again.
        state.stop(account)
    if verb == "out":
        return _out(account, chat_id, action, value, message_id) or ""
    return handler(account, chat_id, action, value) or ""


# ---- Chat -----------------------------------------------------------------

def _chat_screen(account, chat_id, fresh=False, conversation=None):
    conversation = conversation or chat.current(account)
    model = chat.model_of(account, conversation)
    count = conversation.messages.count() if conversation is not None else 0
    has_chats = Conversation.objects.filter(account=account, messages__isnull=False).exists()
    typical = chat.typical_price(model) if model is not None else None
    _show(account, chat_id, texts.chat_text(conversation, model, count, typical),
          texts.chat_keyboard(conversation, count, has_chats), fresh)


def _new_chat(account, chat_id, fresh=False):
    """Who to talk to: the General assistant, an agent, or the catalog."""
    conversation = chat.current(account)
    same = conversation.assistant if conversation is not None and conversation.assistant_id \
        and conversation.assistant.active else None
    _show(account, chat_id, texts.new_chat_text(),
          texts.new_chat_keyboard(agents.featured(), same, bool(agents.saved(account))), fresh)


def _chats(account, chat_id, page, fresh=False):
    rows, has_more = chat.listed(account, page)
    _show(account, chat_id, texts.chats_text(rows, page),
          texts.chats_keyboard(rows, page, has_more), fresh)


def _own_conversation(account, value):
    return Conversation.objects.filter(pk=int(value) if value.isdigit() else 0,
                                       account=account).select_related("assistant").first()


def _chat(account, chat_id, action, value):
    if action == "new":
        _new_chat(account, chat_id)
    elif action == "general":
        conversation = chat.new_conversation(account)
        _chat_screen(account, chat_id, conversation=conversation)
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
        _chats(account, chat_id, int(value) if value.isdigit() else 0)
    elif action == "open":
        conversation = _own_conversation(account, value)
        if conversation is None:
            return _chat(account, chat_id, "list", "0")
        jobs.touch_conversation(conversation)
        model = chat.model_of(account, conversation)
        last = conversation.messages.order_by("-id").first()
        ui.screen(account, chat_id,
                  texts.opened_text(conversation, model, conversation.messages.count(), last,
                                    chat.typical_price(model) if model else None),
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
    _show(account, chat_id, texts.models_text(models, typical),
          texts.models_keyboard(models, current), fresh)


# ---- Agents ---------------------------------------------------------------

def _back_key(account):
    return f"studio:agents:back:{account.pk}"


def _remember_back(account, where):
    """Where ‹ Back on an agent's card goes: the list it was opened from."""
    cache.set(_back_key(account), where, 60 * 60 * 6)


def _card(account, chat_id, agent, fresh=False):
    model = agent.model if agent.model is not None and agent.model.ready_for(jobs.demo()) \
        else chat.model_of(account)
    saved = agents.saved_by(account, agent)
    _show(account, chat_id,
          texts.card_text(agent, model, chat.typical_price(model) if model else 0, saved),
          texts.card_keyboard(agent, saved, agents.share_url(agent), "ag:back"), fresh)


def _list(account, chat_id, kind, rows, words="", fresh=False):
    _show(account, chat_id, texts.list_text(kind, rows, words), texts.list_keyboard(kind, rows),
          fresh)


def _back(account, chat_id):
    where = cache.get(_back_key(account)) or {}
    kind = where.get("kind")
    if kind == "cat":
        return _agents(account, chat_id, "cat", f"{where['slug']}:{where.get('page', 0)}")
    if kind == "chat":
        return _chat_screen(account, chat_id)
    if kind in ("saved", "recent", "top"):
        return _agents(account, chat_id, kind, "")
    if kind in ("search", "find"):
        by_slug = {agent.slug: agent for agent in agents.by_slugs(where.get("slugs") or [])}
        rows = [by_slug[slug] for slug in where.get("slugs") or [] if slug in by_slug]
        return _list(account, chat_id, kind, rows, where.get("words", ""))
    return _agents(account, chat_id, "", "")


def _ask(account, chat_id, conversation, question):
    """A starter tapped: shown as asked, then sent like a typed message."""
    telegram.send_message(chat_id, texts.asked_text(question))
    chat.send(account, chat_id, question, conversation=conversation)
    _chat_screen(account, chat_id, conversation=conversation)


def _agents(account, chat_id, action, value, fresh=False):
    if action == "cat":
        slug, _sep, page = value.partition(":")
        category = Category.objects.filter(slug=slug, active=True).first()
        if category is None:
            return _agents(account, chat_id, "", "", fresh)
        page = int(page) if page.isdigit() else 0
        rows, has_more = agents.in_category(category, page)
        _remember_back(account, {"kind": "cat", "slug": slug, "page": page})
        _show(account, chat_id, texts.category_text(category, rows),
              texts.category_keyboard(category, rows, page, has_more), fresh)
    elif action in ("card", "about"):
        agent = agents.get(value)
        if agent is None:
            _agents(account, chat_id, "", "", fresh)
            return _("This agent is no longer offered.")
        if action == "about":
            _remember_back(account, {"kind": "chat"})
        _card(account, chat_id, agent, fresh)
    elif action == "save":
        agent = agents.get(value)
        if agent is None:
            return _agents(account, chat_id, "", "")
        now = agents.toggle_saved(account, agent)
        _card(account, chat_id, agent)
        return texts.saved_toast(now)
    elif action == "start":
        agent = agents.get(value)
        if agent is None:
            _agents(account, chat_id, "", "")
            return _("This agent is no longer offered.")
        _chat_screen(account, chat_id, fresh, chat.new_conversation(account, agent))
    elif action == "try":
        slug, _sep, index = value.rpartition(":")
        agent = agents.get(slug)
        starters = agent.starters if agent is not None else []
        if not index.isdigit() or int(index) >= len(starters):
            return _agents(account, chat_id, "", "")
        _ask(account, chat_id, chat.new_conversation(account, agent),
             catalog.tr(starters[int(index)]))
    elif action == "ask":
        conversation = chat.current(account)
        starters = conversation.assistant.starters \
            if conversation is not None and conversation.assistant_id else []
        if not value.isdigit() or int(value) >= len(starters):
            return _chat_screen(account, chat_id)
        _ask(account, chat_id, conversation, catalog.tr(starters[int(value)]))
    elif action == "search":
        state.wait(account, "agent_search")
        _show(account, chat_id, texts.search_text(), menus.back_keyboard("ag:"), fresh)
    elif action == "find":
        left = agents.finds_left(account)
        if left:
            state.wait(account, "agent_find")
        _show(account, chat_id, texts.find_text(left), texts.find_keyboard(left), fresh)
    elif action in ("saved", "recent", "top"):
        rows = {"saved": agents.saved, "recent": agents.recent}.get(
            action, lambda _account: agents.popular())(account)
        _remember_back(account, {"kind": action})
        _list(account, chat_id, action, rows, fresh=fresh)
    elif action == "back":
        _back(account, chat_id)
    else:
        categories = agents.categories()
        _remember_back(account, {"kind": "home"})
        _show(account, chat_id,
              texts.agents_text(categories, sum(category.on for category in categories)),
              texts.agents_keyboard(categories, bool(agents.saved(account)),
                                    bool(agents.recent(account, 1))), fresh)
    return ""


# ---- Images and voice -----------------------------------------------------

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


# ---- Usage ----------------------------------------------------------------

def _usage(account, chat_id, action, value, fresh=False):
    if action == "last":
        page = int(value) if value.isdigit() else 0
        rows, has_more = usage.last(account, page)
        _show(account, chat_id, texts.last_text(rows, page), texts.last_keyboard(page, has_more),
              fresh)
    elif action == "limit":
        limit = chat.profile(account).daily_limit
        _show(account, chat_id, texts.limit_choice_text(limit, jobs.spent_today(account)),
              texts.limit_choice_keyboard(limit), fresh)
    elif action == "setlimit" and value.isdigit() and int(value) in texts.LIMITS:
        usage.set_limit(account, int(value))
        _usage(account, chat_id, "", "")
        return "🎯 " + (texts.coins(int(value)) if int(value) else _("No limit"))
    else:
        limit = chat.profile(account).daily_limit
        _show(account, chat_id,
              texts.usage_text(usage.periods(account), ledger.balance(account), limit,
                               jobs.spent_today(account),
                               [(texts.tool_name(tool), credits)
                                for tool, credits in usage.by_tool(account)],
                               usage.by_agent(account), usage.by_model(account)),
              texts.usage_keyboard(), fresh)
    return ""


# ---- Buttons on answers and notices ---------------------------------------

def _own_job(account, value):
    return Job.objects.filter(pk=int(value) if value.isdigit() else 0,
                              account=account).select_related("model", "assistant").first()


def _out(account, chat_id, action, value, message_id=None):
    """Buttons on an answer, an image or a notice: that message is left as it is."""
    if action == "say":
        message = Message.objects.filter(pk=int(value) if value.isdigit() else 0,
                                         role=Role.ASSISTANT,
                                         conversation__account=account).first()
        if message is not None:
            voice.listen(account, chat_id, message)
            return "🔊 …"
    elif action == "cost":
        job = _own_job(account, value)
        if job is not None:
            return telegram.Alert(texts.details_text(job))
    elif action in ("send", "sendnew"):
        if not chat.send_confirmed(account, chat_id, new_chat=action == "sendnew"):
            return menus.expired_button_text()
        telegram.clear_reply_markup(chat_id, message_id)
    elif action == "cancel":
        state.stop(account)
        telegram.edit_message(chat_id, message_id, telegram.esc(texts.cancelled_text()))
    elif action == "new":
        state.stop(account)
        _new_chat(account, chat_id, fresh=True)
    elif action == "usage":
        _usage(account, chat_id, "", "", fresh=True)
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
