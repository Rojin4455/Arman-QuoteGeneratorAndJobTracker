# Generated for flexible time off (half day, custom hours)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_app', '0007_rename_employee_tim_employe_f43e07_idx_employee_ti_employe_bfff47_idx'),
    ]

    operations = [
        migrations.AddField(
            model_name='employeetimeoff',
            name='coverage',
            field=models.CharField(
                choices=[
                    ('full_day', 'Full day'),
                    ('half_day_am', 'Half day (morning)'),
                    ('half_day_pm', 'Half day (afternoon)'),
                    ('custom', 'Custom hours'),
                ],
                default='full_day',
                help_text='Used when start_date equals end_date.',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='employeetimeoff',
            name='start_day_coverage',
            field=models.CharField(
                choices=[
                    ('full_day', 'Full day'),
                    ('half_day_am', 'Half day (morning)'),
                    ('half_day_pm', 'Half day (afternoon)'),
                    ('custom', 'Custom hours'),
                ],
                default='full_day',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='employeetimeoff',
            name='end_day_coverage',
            field=models.CharField(
                choices=[
                    ('full_day', 'Full day'),
                    ('half_day_am', 'Half day (morning)'),
                    ('half_day_pm', 'Half day (afternoon)'),
                    ('custom', 'Custom hours'),
                ],
                default='full_day',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='employeetimeoff',
            name='start_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='employeetimeoff',
            name='end_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='employeetimeoff',
            name='end_start_time',
            field=models.TimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='employeetimeoff',
            name='end_end_time',
            field=models.TimeField(blank=True, null=True),
        ),
    ]
