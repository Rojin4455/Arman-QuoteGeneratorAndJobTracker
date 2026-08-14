from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('quote_app', '0033_customersubmission_is_persisted_snapshot_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='customersubmission',
            name='quote_origin',
            field=models.CharField(
                choices=[('technician', 'Technician'), ('public', 'Public / Customer')],
                db_index=True,
                default='technician',
                help_text='Whether this quote was started by a technician or via the public customer form.',
                max_length=20,
            ),
        ),
    ]
