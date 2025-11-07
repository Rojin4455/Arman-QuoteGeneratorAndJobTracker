# Generated migration to remove JTService model after migration to Service

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('jobtracker_app', '0007_alter_jobserviceitem_service'),
    ]

    operations = [
        migrations.DeleteModel(
            name='JTService',
        ),
    ]

