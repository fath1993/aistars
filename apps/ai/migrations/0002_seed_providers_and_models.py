"""
The providers, without keys, and the models to start with. API keys are
added in the admin (AI -> Providers). OpenAI is the default: its models are
offered; Anthropic's are offered too but only show once its key is set.

Prices in USD, from OpenAI's pricing page read on 2026-10-20 (Anthropic's as
MyShortClip seeded them on 2026-10-15). Edit them in AI -> Models when a
provider changes them; Sync models (AI -> Providers) adds models found later,
switched off.

    chat           input / output per 1M tokens
    images         input (prompt) / output (image) per 1M tokens, and photos
                   sent in per 1M tokens
    voice to text  per minute of audio
    text to speech per 1M characters (gpt-4o-mini-tts is about $0.015 a
                   minute of speech, about 900 characters: $20 per 1M
                   characters is on the safe side)
"""

from decimal import Decimal

from django.db import migrations

# provider, id, label, use, tagline, sees images, input, output, image input,
# unit, longest answer, order, default for users
MODELS = (
    ("openai", "gpt-6-luna", "GPT-6 Luna", "chat",
     "Fast and cheap, for everyday questions", True,
     "0.10", "0.50", "0", "0", 4000, 1, True),
    ("openai", "gpt-6.1-sol", "GPT-6.1 Sol", "chat",
     "Smart: writing, analysis and harder questions", True,
     "2.00", "10.00", "0", "0", 8000, 2, False),
    ("openai", "gpt-6-astra", "GPT-6 Astra", "chat",
     "OpenAI's most capable model, for the hardest tasks", True,
     "10.00", "50.00", "0", "0", 8000, 3, False),
    ("anthropic", "claude-haiku-5-5", "Claude Haiku 5.5", "chat",
     "Anthropic's fast model, for everyday questions", True,
     "0.10", "0.50", "0", "0", 4000, 4, False),
    ("anthropic", "claude-sonnet-5-5", "Claude Sonnet 5.5", "chat",
     "Anthropic's smart model, careful with writing and code", True,
     "2.00", "10.00", "0", "0", 8000, 5, False),
    ("openai", "gpt-image-2.5-flare", "GPT Image 2.5 Flare", "image",
     "Fast, sharp images from a description", True,
     "5.00", "30.00", "8.00", "0", 0, 1, True),
    ("openai", "gpt-image-2.5-sunburst", "GPT Image 2.5 Sunburst", "image",
     "Slower, most precise edits of your photos", True,
     "5.00", "30.00", "8.00", "0", 0, 2, False),
    ("openai", "gpt-transcribe", "GPT Transcribe", "transcription", "", False,
     "0", "0", "0", "0.0045", 0, 1, True),
    ("openai", "gpt-4o-mini-transcribe", "GPT-4o mini Transcribe", "transcription", "", False,
     "0", "0", "0", "0.0030", 0, 2, False),
    ("openai", "gpt-4o-mini-tts", "GPT-4o mini TTS", "speech", "", False,
     "0", "0", "0", "20.00", 0, 1, True),
)

# Cheap and good enough for both; both need the OpenAI key.
TRANSLATOR = "gpt-6-luna"
JUDGE = "gpt-6-luna"


def seed(apps, schema_editor):
    Provider = apps.get_model("ai", "Provider")
    AIModel = apps.get_model("ai", "AIModel")
    for kind in ("anthropic", "openai"):
        Provider.objects.get_or_create(kind=kind)
    for (kind, model_id, label, use, tagline, sees_images, price_in, price_out, image_in,
         unit, longest, order, default) in MODELS:
        AIModel.objects.update_or_create(
            provider=Provider.objects.get(kind=kind), model_id=model_id,
            defaults={"label": label, "use": use, "tagline": tagline,
                      "sees_images": sees_images, "input_price": Decimal(price_in),
                      "output_price": Decimal(price_out),
                      "image_input_price": Decimal(image_in), "unit_price": Decimal(unit),
                      "max_output_tokens": longest or 4000, "sort_order": order,
                      "is_default": default, "offered": True, "enabled": True,
                      "default_for_translation": model_id == TRANSLATOR,
                      "default_for_safety": model_id == JUDGE})


class Migration(migrations.Migration):

    dependencies = [
        ("ai", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
