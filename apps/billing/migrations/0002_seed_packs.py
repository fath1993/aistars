"""The packs to start with: one credit per Star. Edit them in Billing → Packs."""

from django.db import migrations

PACKS = ((100, 100, 1), (300, 300, 2), (1000, 1000, 3))


def seed(apps, schema_editor):
    Pack = apps.get_model("billing", "Pack")
    for stars, credits, order in PACKS:
        Pack.objects.get_or_create(stars=stars, credits=credits,
                                   defaults={"sort_order": order})


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
