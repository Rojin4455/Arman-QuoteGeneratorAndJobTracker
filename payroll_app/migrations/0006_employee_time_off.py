# Generated manually for EmployeeTimeOff model

import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('payroll_app', '0005_alter_payout_payout_type'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EmployeeTimeOff',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('start_date', models.DateField()),
                ('end_date', models.DateField(help_text='Last day off (inclusive).')),
                ('kind', models.CharField(choices=[('day_off', 'Day off'), ('vacation', 'Vacation'), ('sick', 'Sick'), ('personal', 'Personal'), ('other', 'Other')], default='day_off', max_length=20)),
                ('notes', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('employee', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='time_off_entries', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'employee_time_off',
                'ordering': ['-start_date', '-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='employeetimeoff',
            index=models.Index(fields=['employee', 'start_date', 'end_date'], name='employee_tim_employe_f43e07_idx'),
        ),
    ]
