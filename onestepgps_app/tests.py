from django.test import TestCase

from accounts.models import GHLAuthCredentials
from onestepgps_app.alert_store import persist_webhook_alert
from onestepgps_app.models import OneStepGPSAlert
from onestepgps_app.services import normalize_device, normalize_devices_payload


class OneStepGPSNormalizeTests(TestCase):
    def test_normalize_device_from_result_list_shape(self):
        payload = {
            'result_list': [
                {
                    'factory_id': 'abc123',
                    'display_name': 'Truck 1',
                    'online': True,
                    'drive_status': 'Driving',
                    'tags': ['Crew A'],
                    'latest_device_point': {
                        'lat': 29.7604,
                        'lng': -95.3698,
                        'dt_tracker': '2026-08-27T12:00:00Z',
                        'speed': 25,
                        'heading': 90,
                        'address': '123 Main St',
                    },
                }
            ]
        }
        devices = normalize_devices_payload(payload)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]['display_name'], 'Truck 1')
        self.assertEqual(devices[0]['lat'], 29.7604)
        self.assertEqual(devices[0]['lng'], -95.3698)
        self.assertEqual(devices[0]['heading'], 90)
        self.assertEqual(devices[0]['address'], '123 Main St')
        self.assertEqual(devices[0]['tags'], ['Crew A'])
        self.assertEqual(devices[0]['drive_status'], 'Driving')

    def test_normalize_device_skips_missing_coordinates(self):
        self.assertIsNone(normalize_device({'display_name': 'No GPS'}))


class OneStepGPSAlertStoreTests(TestCase):
    def setUp(self):
        self.account = GHLAuthCredentials.objects.create(
            user_id='u-gps-1',
            access_token='t',
            refresh_token='r',
            expires_in=3600,
            location_id='b8qvo7VooP3JD3dIZU42',
            company_name='Test',
        )

    def test_persist_webhook_alert_and_dedupe(self):
        payload = {
            'Alert ID': 'alert-1',
            'Alert Name': 'Marc Engine off',
            'Alert Time': '08/27/2026 12:24:44 PM',
            'Device ID': 'dev-1',
            'Device Name': 'Jeremy',
            'Ignition On': False,
            'Location (Lat,Lng)': '29.76,-95.37',
            'Speed (MPH)': 0,
        }
        alert, created = persist_webhook_alert(self.account, payload)
        self.assertTrue(created)
        self.assertEqual(alert.alert_name, 'Marc Engine off')
        self.assertEqual(alert.device_name, 'Jeremy')
        self.assertAlmostEqual(alert.latitude, 29.76)
        self.assertEqual(OneStepGPSAlert.objects.count(), 1)

        again, created2 = persist_webhook_alert(self.account, payload)
        self.assertFalse(created2)
        self.assertEqual(again.id, alert.id)
        self.assertEqual(OneStepGPSAlert.objects.count(), 1)

    def test_persist_snake_case_test_webhook_payload(self):
        payload = {
            'device_id': 'sample-device-id',
            'alert_id': '6lcZ0t_mPGyKY-81f07-1k',
            'alert_time_utc': '2026-08-27T17:55:22Z',
            'device_name': 'sample-display-name',
            'alert_name': 'sample-alert-description',
            'lat': 33.81207780779176,
            'lng': -117.91897692667183,
            'ignition_on': True,
            'speed_mph': 20,
            'drive_status': 'driving',
            'drive_status_duration_s': 300,
            'device_point': {
                'lat': 33.81207780779176,
                'lng': -117.91897692667183,
                'device_point_detail': {'external_volt': 12, 'acc': True},
                'device_state': {
                    'drive_status': 'driving',
                    'drive_status_duration': {'value': 5, 'unit': 'm', 'display': '5m'},
                },
            },
        }
        alert, created = persist_webhook_alert(self.account, payload)
        self.assertTrue(created)
        self.assertEqual(alert.external_alert_id, '6lcZ0t_mPGyKY-81f07-1k')
        self.assertEqual(alert.alert_name, 'sample-alert-description')
        self.assertEqual(alert.device_name, 'sample-display-name')
        self.assertEqual(alert.speed_mph, 20)
        self.assertEqual(alert.drive_status, 'driving')
        self.assertEqual(alert.drive_status_duration_seconds, 300)
        self.assertTrue(alert.ignition_on)
        self.assertAlmostEqual(alert.latitude, 33.81207780779176)
        self.assertAlmostEqual(alert.longitude, -117.91897692667183)
        self.assertEqual(alert.external_voltage, 12)

        again, created2 = persist_webhook_alert(self.account, payload)
        self.assertFalse(created2)
        self.assertEqual(OneStepGPSAlert.objects.filter(external_alert_id='6lcZ0t_mPGyKY-81f07-1k').count(), 1)
