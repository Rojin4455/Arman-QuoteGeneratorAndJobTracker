# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('service_app', '0027_add_percent_of_total_pricing_types'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='payroll_can_view_team_data',
            field=models.BooleanField(
                default=True,
                help_text=(
                    'When True (and user is admin: manager/supervisor), payroll reads see all account '
                    'team data for payouts list, time-entries today, and active-session. '
                    "When False, those endpoints only return the user's own data (worker scope)."
                ),
            ),
        ),
    ]
