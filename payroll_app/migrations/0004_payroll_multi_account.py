# Generated manually for multi-account support

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0012_remove_ghlmediastorage_accounts_ghlmediastorage_credentials_ghl_id_uniq_and_more'),
        ('payroll_app', '0003_employeeprofile_address_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeeprofile',
            name='account',
            field=models.ForeignKey(
                blank=True,
                help_text='GHL account this employee belongs to (for multi-account onboarding)',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='employee_profiles',
                to='accounts.ghlauthcredentials',
            ),
        ),
        migrations.AddField(
            model_name='payrollsettings',
            name='account',
            field=models.ForeignKey(
                blank=True,
                help_text='GHL account these settings belong to (for multi-account onboarding)',
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='payroll_settings',
                to='accounts.ghlauthcredentials',
            ),
        ),
    ]
