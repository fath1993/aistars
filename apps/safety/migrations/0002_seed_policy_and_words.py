"""The image policy and a few words (apps/safety/defaults.py), as in MyShortClip."""

from django.db import migrations


def seed(apps, schema_editor):
    from apps.safety.defaults import BLOCKED_TERMS, POLICY

    Policy = apps.get_model("safety", "Policy")
    BlockedTerm = apps.get_model("safety", "BlockedTerm")
    Policy.objects.get_or_create(singleton=1, defaults={"text": POLICY})
    for term, category in BLOCKED_TERMS:
        BlockedTerm.objects.get_or_create(term=term, defaults={"category": category})


class Migration(migrations.Migration):
    dependencies = [("safety", "0001_initial")]

    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
