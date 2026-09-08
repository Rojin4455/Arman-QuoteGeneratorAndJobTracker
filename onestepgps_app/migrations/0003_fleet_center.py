from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('onestepgps_app', '0002_onestepgpsalert_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='onestepgpsalert',
            name='acknowledged',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='onestepgpsalert',
            name='acknowledged_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name='OneStepGPSTrip',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('event_key', models.CharField(db_index=True, max_length=255)),
                ('kind', models.CharField(choices=[('drive', 'Drive'), ('stop', 'Stop')], default='drive', max_length=16)),
                ('device_id', models.CharField(db_index=True, max_length=255)),
                ('device_name', models.CharField(blank=True, default='', max_length=255)),
                ('started_at', models.DateTimeField(db_index=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('duration_seconds', models.FloatField(blank=True, null=True)),
                ('distance_miles', models.FloatField(blank=True, null=True)),
                ('idle_seconds', models.FloatField(blank=True, null=True)),
                ('max_speed_mph', models.FloatField(blank=True, null=True)),
                ('start_address', models.CharField(blank=True, default='', max_length=500)),
                ('end_address', models.CharField(blank=True, default='', max_length=500)),
                ('start_latitude', models.FloatField(blank=True, null=True)),
                ('start_longitude', models.FloatField(blank=True, null=True)),
                ('end_latitude', models.FloatField(blank=True, null=True)),
                ('end_longitude', models.FloatField(blank=True, null=True)),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onestepgps_trips', to='accounts.ghlauthcredentials')),
            ],
            options={'db_table': 'onestepgps_trip', 'ordering': ['-started_at']},
        ),
        migrations.AddConstraint(
            model_name='onestepgpstrip',
            constraint=models.UniqueConstraint(fields=('account', 'event_key'), name='onestepgps_trip_account_event_uniq'),
        ),
        migrations.CreateModel(
            name='OneStepGPSMaintenance',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('device_id', models.CharField(max_length=255)),
                ('device_name', models.CharField(blank=True, default='', max_length=255)),
                ('odometer_miles', models.FloatField(blank=True, null=True)),
                ('engine_hours', models.FloatField(blank=True, null=True)),
                ('fuel_level_percent', models.FloatField(blank=True, null=True)),
                ('check_engine', models.BooleanField(default=False)),
                ('dtc_codes', models.JSONField(blank=True, default=list)),
                ('next_service_miles', models.FloatField(blank=True, null=True)),
                ('next_service_at', models.DateTimeField(blank=True, null=True)),
                ('raw_payload', models.JSONField(blank=True, default=dict)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onestepgps_maintenance', to='accounts.ghlauthcredentials')),
            ],
            options={'db_table': 'onestepgps_maintenance'},
        ),
        migrations.AddConstraint(
            model_name='onestepgpsmaintenance',
            constraint=models.UniqueConstraint(fields=('account', 'device_id'), name='onestepgps_maint_account_device_uniq'),
        ),
        migrations.CreateModel(
            name='OneStepGPSGeofence',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('latitude', models.FloatField()),
                ('longitude', models.FloatField()),
                ('radius_miles', models.FloatField(default=1.0)),
                ('color', models.CharField(default='#0877f9', max_length=16)),
                ('trigger_entry', models.BooleanField(default=True)),
                ('trigger_exit', models.BooleanField(default=True)),
                ('after_hours', models.BooleanField(default=False)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onestepgps_geofences', to='accounts.ghlauthcredentials')),
            ],
            options={'db_table': 'onestepgps_geofence', 'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='OneStepGPSVehicleBinding',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('device_id', models.CharField(max_length=255)),
                ('technician_name', models.CharField(blank=True, default='', max_length=255)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('account', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='onestepgps_vehicle_bindings', to='accounts.ghlauthcredentials')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='gps_vehicle_bindings', to=settings.AUTH_USER_MODEL)),
            ],
            options={'db_table': 'onestepgps_vehicle_binding'},
        ),
        migrations.AddConstraint(
            model_name='onestepgpsvehiclebinding',
            constraint=models.UniqueConstraint(fields=('account', 'device_id'), name='onestepgps_binding_account_device_uniq'),
        ),
    ]
