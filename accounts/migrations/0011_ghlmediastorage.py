# Generated manually for GHLMediaStorage model

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_contact_company_name'),
    ]

    operations = [
        migrations.CreateModel(
            name='GHLMediaStorage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Display name of the media storage', max_length=255)),
                ('ghl_id', models.CharField(help_text='The GHL media storage ID used in API calls', max_length=255)),
                ('location_id', models.CharField(blank=True, help_text='GHL location ID (often same as credentials.location_id)', max_length=255, null=True)),
                ('is_active', models.BooleanField(default=True, help_text='Whether this media storage mapping is currently active')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('credentials', models.ForeignKey(help_text='The GHL credentials (location) this media storage belongs to', on_delete=models.deletion.CASCADE, related_name='media_storages', to='accounts.ghlauthcredentials')),
            ],
            options={
                'db_table': 'ghl_media_storages',
                'ordering': ['name'],
            },
        ),
        migrations.AddConstraint(
            model_name='ghlmediastorage',
            constraint=models.UniqueConstraint(fields=('credentials', 'ghl_id'), name='accounts_ghlmediastorage_credentials_ghl_id_uniq'),
        ),
        migrations.AddIndex(
            model_name='ghlmediastorage',
            index=models.Index(fields=['credentials', 'is_active'], name='ghl_media_s_credent_5a0e0d_idx'),
        ),
        migrations.AddIndex(
            model_name='ghlmediastorage',
            index=models.Index(fields=['ghl_id'], name='ghl_media_s_ghl_id_8b2c3d_idx'),
        ),
    ]
