# Generated manually for multi-account support

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_remove_ghlmediastorage_accounts_ghlmediastorage_credentials_ghl_id_uniq_and_more'),
        ('dashboard_app', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='account',
            field=models.ForeignKey(
                blank=True,
                help_text='GHL account this invoice belongs to (for multi-account onboarding)',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='invoices',
                to='accounts.ghlauthcredentials',
            ),
        ),
        migrations.AlterField(
            model_name='invoice',
            name='invoice_id',
            field=models.CharField(db_index=True, max_length=100),
        ),
        migrations.AlterUniqueTogether(
            name='invoice',
            unique_together={('account', 'invoice_id')},
        ),
        migrations.AddIndex(
            model_name='invoice',
            index=models.Index(fields=['account', 'location_id'], name='invoices_account_9a1b2c_idx'),
        ),
    ]
