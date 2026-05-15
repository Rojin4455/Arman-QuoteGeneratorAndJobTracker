# Generated manually for company branding on invoices

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_ghlcompanyauth'),
    ]

    operations = [
        migrations.AddField(
            model_name='ghlauthcredentials',
            name='company_name',
            field=models.CharField(
                blank=True,
                help_text='Business name shown on GHL invoices for this account/location.',
                max_length=255,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name='ghlauthcredentials',
            name='company_logo_url',
            field=models.URLField(
                blank=True,
                help_text='Public URL for logo shown on GHL invoices (same bucket CDN URLs work well).',
                max_length=500,
                null=True,
            ),
        ),
    ]
