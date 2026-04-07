# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('service_app', '0028_user_payroll_can_view_team_data'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='can_access_service_management_tool',
            field=models.BooleanField(
                default=True,
                help_text='Grants access to the service management tool for this user.',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='can_access_location_management_tool',
            field=models.BooleanField(
                default=True,
                help_text='Grants access to the location management tool for this user.',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='can_access_house_size_management_tool',
            field=models.BooleanField(
                default=True,
                help_text='Grants access to the house size management tool for this user.',
            ),
        ),
    ]
