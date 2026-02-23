# Multi-account support: add account FK to Job

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_remove_ghlmediastorage_accounts_ghlmediastorage_credentials_ghl_id_uniq_and_more'),
        ('jobtracker_app', '0019_job_discount'),
    ]

    operations = [
        migrations.AddField(
            model_name='job',
            name='account',
            field=models.ForeignKey(
                blank=True,
                help_text='GHL account this job belongs to (for multi-account onboarding)',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='jobs',
                to='accounts.ghlauthcredentials',
            ),
        ),
    ]
