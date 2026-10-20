"""
Images: Create an image from a description, or Edit a photo the user sends.

    description (and the photo) → the safety check → the summary: shape,
    quality, the most it can cost → Create → made in the background → the
    image, with what it cost

Every image request goes through all three safety layers before its price
is shown, as in MyShortClip. The hold is priced from the image tokens the
size and quality make (OUTPUT_TOKENS, with room to spare); the charge is
what OpenAI reports it really used.
"""

from apps.ai import clients
from apps.ai.models import AIModel, Use
from apps.billing import ledger
from apps.core import menus
from apps.core.services import telegram, ui
from apps.safety import checks
from apps.safety.models import Result, Screening
from apps.safety.texts import category_text

from . import archive, demo, jobs, state, texts
from .models import Job, Kind

# Image tokens made for each size and quality, as OpenAI published them for
# GPT Image; newer models are charged from their real usage, so these only
# set the hold, with HOLD_FACTOR to spare.
OUTPUT_TOKENS = {
    ("1024x1024", "low"): 272, ("1024x1024", "medium"): 1056, ("1024x1024", "high"): 4160,
    ("1024x1536", "low"): 408, ("1024x1536", "medium"): 1584, ("1024x1536", "high"): 6240,
    ("1536x1024", "low"): 400, ("1536x1024", "medium"): 1568, ("1536x1024", "high"): 6208,
}
HOLD_FACTOR = 1.5
# A photo sent in, as image input tokens, on the safe side.
PHOTO_TOKENS = 3000
DEFAULTS = {"size": "1024x1024", "quality": "medium"}


def model_for(account=None, edit=False):
    """
    The image model: the user's choice, else the default; for an edit, one
    that can change photos.
    """
    from .chat import profile

    is_demo = jobs.demo()
    offered = AIModel.offered_for(Use.IMAGE, is_demo)
    chosen = profile(account).image_model_id if account is not None else None
    default = AIModel.default_for(Use.IMAGE, is_demo)
    ordered = ([model for model in offered if model.pk == chosen]
               + ([default] if default is not None else []) + offered)
    return next((model for model in ordered if model.sees_images or not edit), None)


def estimate(model, prompt, size, quality, photos) -> int:
    """The most an image can cost, in micro-USD."""
    sizes = texts.SIZES if size == "auto" else (size,)
    tokens_out = max(OUTPUT_TOKENS[(each, quality)] for each in sizes)
    tokens_in = len(prompt) // 2 + 100
    cost = model.cost_micros(tokens_in, tokens_out, PHOTO_TOKENS * len(photos))
    return int(cost * HOLD_FACTOR)


# --------------------------------------------------------------------------
# Screens
# --------------------------------------------------------------------------

def show(account, chat_id, fresh=False):
    model = model_for(account)
    editor = model_for(account, edit=True)
    several = len(AIModel.offered_for(Use.IMAGE, jobs.demo())) > 1
    (ui.fresh_screen if fresh else ui.screen)(
        account, chat_id, texts.images_text(model),
        texts.images_keyboard(model, editor is not None, several))


def show_models(account, chat_id):
    models = AIModel.offered_for(Use.IMAGE, jobs.demo())
    ui.screen(account, chat_id, texts.image_models_text(models),
              texts.models_keyboard(models, model_for(account), back="img:",
                                    verb="img:model"))


def choose_model(account, chat_id, value):
    from .chat import profile
    from .models import Profile

    models = {str(model.pk): model for model in AIModel.offered_for(Use.IMAGE, jobs.demo())}
    if value in models:
        Profile.objects.filter(pk=profile(account).pk).update(image_model=models[value])
    show(account, chat_id)
    return str(models[value]) if value in models else ""


def ask_description(account, chat_id):
    state.wait(account, "image_prompt")
    ui.screen(account, chat_id, texts.ask_description_text(), texts.cancel_keyboard("img:"))


def ask_photo(account, chat_id):
    state.wait(account, "edit_photo")
    ui.screen(account, chat_id, texts.ask_photo_text(), texts.cancel_keyboard("img:"))


def got_photo(account, chat_id, file_id, draft=None):
    """The photo to change: then what should change, unless that is known already."""
    draft = {k: v for k, v in (draft or {}).items() if k not in ("step", "screening")}
    draft["photo"] = file_id
    if draft.get("prompt"):
        return got_prompt(account, chat_id, draft["prompt"], draft)
    state.wait(account, "edit_prompt", **draft)
    ui.fresh_screen(account, chat_id, texts.ask_change_text(), texts.cancel_keyboard("img:"))
    return None


def got_prompt(account, chat_id, text, draft):
    base = {"size": "auto" if draft.get("photo") else DEFAULTS["size"],
            "quality": DEFAULTS["quality"]}
    draft = {**base, **{k: v for k, v in draft.items() if k not in ("step", "screening")},
             "prompt": text}
    summary(account, chat_id, draft, fresh=True)


def set_option(account, chat_id, key, value):
    draft = state.waiting(account)
    if not draft or draft.get("step") != "image_summary":
        show(account, chat_id)
        return menus.expired_button_text()
    allowed = {"size": (("auto",) if draft.get("photo") else ()) + texts.SIZES,
               "quality": texts.QUALITIES}[key]
    if value in allowed:
        draft[key] = value
    summary(account, chat_id, draft)
    return ""


def change_prompt(account, chat_id):
    draft = state.waiting(account) or {}
    keep = {k: v for k, v in draft.items() if k in ("photo", "size", "quality")}
    state.wait(account, "edit_prompt" if keep.get("photo") else "image_prompt", **keep)
    ui.screen(account, chat_id, texts.ask_change_text() if keep.get("photo")
              else texts.ask_description_text(), texts.cancel_keyboard("img:"))


def summary(account, chat_id, draft, fresh=False):
    """Checked for safety once per description and photo, then priced."""
    show_screen = ui.fresh_screen if fresh else ui.screen
    model = model_for(account, edit=bool(draft.get("photo")))
    if model is None:
        state.stop(account)
        return show_screen(account, chat_id, telegram.esc(texts.no_model_text()),
                           texts.cancel_keyboard("img:"))
    photos = [draft["photo"]] if draft.get("photo") else []
    if not draft.get("screening"):
        screening = checks.screen(account, "edit" if photos else "image", model.model_id,
                                  draft["prompt"],
                                  photos=[{"file_id": file_id, "kind": "photo"}
                                          for file_id in photos], demo=jobs.demo())
        if screening.result != Result.PASSED:
            state.stop(account)
            text = (texts.blocked_text(category_text(screening.category))
                    if screening.result == Result.BLOCKED else texts.unchecked_text())
            show_screen(account, chat_id, text, texts.cancel_keyboard("img:"))
            if account.is_blocked:
                telegram.send_message(chat_id, menus.blocked_text())
            return None
        draft["screening"] = screening.pk
    hold = jobs.hold_for(estimate(model, draft["prompt"], draft["size"], draft["quality"],
                                  photos), model)
    balance = ledger.balance(account)
    state.wait(account, "image_summary", **{k: v for k, v in draft.items() if k != "step"})
    return show_screen(account, chat_id, texts.image_summary_text(draft, model, hold, balance),
                       texts.image_summary_keyboard(draft, hold, hold <= balance))


def go(account, chat_id):
    draft = state.waiting(account)
    if not draft or draft.get("step") != "image_summary":
        show(account, chat_id)
        return menus.expired_button_text()
    photos = [draft["photo"]] if draft.get("photo") else []
    model = model_for(account, edit=bool(photos))
    screening = Screening.objects.filter(pk=draft.get("screening"), account=account,
                                         result=Result.PASSED).first()
    if model is None or screening is None:
        show(account, chat_id)
        return menus.expired_button_text()
    try:
        job = jobs.start(account, chat_id, Kind.EDIT if photos else Kind.IMAGE, model,
                         estimate(model, draft["prompt"], draft["size"], draft["quality"], photos),
                         prompt=draft["prompt"], photos=photos, screening=screening,
                         options={"size": draft["size"], "quality": draft["quality"]})
    except jobs.NotEnough:
        summary(account, chat_id, draft)
        return ""
    except jobs.TooMany:
        return texts.too_many_text()
    state.stop(account)
    for file_id in photos:
        archive.copy_input(job, "photo", file_id)
    ui.screen(account, chat_id, texts.making_image_text(job))
    ui.forget_screen(account)
    ui.fresh_screen(account, chat_id, menus.home_text(account), menus.home_keyboard(account))
    jobs.launch(job)
    return ""


def again(account, chat_id, job):
    """The same request once more: its description, photo and settings."""
    draft = {"prompt": job.prompt, "size": job.options.get("size", DEFAULTS["size"]),
             "quality": job.options.get("quality", DEFAULTS["quality"])}
    if job.photos:
        draft["photo"] = job.photos[0]
    summary(account, chat_id, draft, fresh=True)


def edit_result(account, chat_id, job):
    """Change the image just made: it becomes the photo to edit."""
    if not job.result_file_id:
        return show(account, chat_id, fresh=True)
    state.wait(account, "edit_prompt", photo=job.result_file_id)
    ui.fresh_screen(account, chat_id, texts.ask_change_text(), texts.cancel_keyboard("img:"))
    return None


def send_original(chat_id, job):
    if job.original_file_id:
        telegram.send_media(chat_id, "document", job.original_file_id,
                            caption=f"🎨 #{job.pk}")


# --------------------------------------------------------------------------
# Making it (in the background)
# --------------------------------------------------------------------------

def make(job):
    size, quality = job.options.get("size", "1024x1024"), job.options.get("quality", "medium")
    if job.demo:
        data, cost = demo.image(), int(job.estimate_micros / HOLD_FACTOR)
    else:
        photos = [(_download(file_id), "image/jpeg") for file_id in job.photos]
        try:
            picture = clients.image(job.model, job.prompt, size, quality, photos=photos,
                                    purpose=job.kind, estimate_micros=job.estimate_micros)
        except clients.ProviderRefused as exc:
            jobs.failed(job, str(exc))
            checks.provider_refused(job)
            telegram.send_message(job.chat_id, texts.refused_text(),
                                  reply_markup=texts.notice_keyboard())
            return
        data, cost = picture.data, picture.cost_micros
    _deliver(job, data, cost)


def _download(file_id) -> bytes:
    try:
        return telegram.download_file(file_id)
    except ValueError as exc:
        raise clients.AIError(f"the photo could not be had: {exc}") from None


def _deliver(job, data, cost):
    """
    Sent before it is charged: if Telegram takes it nowhere, nothing is
    charged. The caption shows the charge and the balance it will leave.
    """
    job.charged, balance = jobs.charge_preview(job, cost)
    caption = texts.image_caption(job, balance)
    stored = archive.store_output(job, "photo", data, f"image-{job.pk}.png", "image/png")
    original = archive.store_output(job, "document", data, f"image-{job.pk}.png", "image/png",
                                    role="original")
    original_id = telegram.file_id_of(original, "document")
    markup = texts.image_keyboard(job, bool(original_id))
    sent = None
    if stored:
        file_id = telegram.file_id_of(stored, "photo")
        sent = telegram.send_media(job.chat_id, "photo", file_id, caption, markup)
    if sent is None:
        sent = telegram.send_media(job.chat_id, "photo", data, caption, markup,
                                   filename=f"image-{job.pk}.png", content_type="image/png")
    if sent is None:
        raise clients.AIError("Telegram took the image nowhere")
    Job.objects.filter(pk=job.pk).update(
        result_file_id=telegram.file_id_of(sent, "photo"), original_file_id=original_id,
        archive_message_id=(stored or {}).get("message_id"))
    jobs.done(job, cost)

