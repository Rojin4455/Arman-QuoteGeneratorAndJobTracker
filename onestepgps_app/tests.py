from django.test import TestCase

from accounts.models import GHLAuthCredentials
from onestepgps_app.alert_store import persist_webhook_alert
from onestepgps_app.dataqueue import extract_dataqueue_bundle
from onestepgps_app.bundle_store import persist_operational_bundle
from onestepgps_app.models import OneStepGPSAlert, OneStepGPSTrip
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


class OneStepGPSDataQueueTests(TestCase):
    def setUp(self):
        self.account = GHLAuthCredentials.objects.create(
            user_id='u-gps-2',
            access_token='t',
            refresh_token='r',
            expires_in=3600,
            location_id='loc-dataqueue-1',
            company_name='Test',
        )

    def test_extract_batch_alerts_and_trips(self):
        payload = {
            'events': [
                {
                    'alert_id': 'a-1',
                    'alert_name': 'Harold Speeding',
                    'device_id': 'dev-9',
                    'device_name': 'Elias',
                    'alert_time_utc': '2026-09-01T16:22:13Z',
                    'speed_mph': 73,
                    'lat': 29.78,
                    'lng': -95.55,
                },
                {
                    'event_type': 'drive_complete',
                    'device_id': 'dev-9',
                    'display_name': 'Elias',
                    'started_at': '2026-09-01T15:00:00Z',
                    'ended_at': '2026-09-01T16:00:00Z',
                    'distance_mi': 18.7,
                    'duration_s': 3600,
                },
            ]
        }
        bundle = extract_dataqueue_bundle(payload)
        self.assertEqual(len(bundle.alerts), 1)
        self.assertEqual(len(bundle.trips), 1)
        stats = persist_operational_bundle(self.account, bundle)
        self.assertEqual(stats['alerts'], 1)
        self.assertEqual(stats['trips'], 1)
        self.assertEqual(OneStepGPSAlert.objects.filter(account=self.account).count(), 1)
        self.assertEqual(OneStepGPSTrip.objects.filter(account=self.account).count(), 1)


class OneStepGPSWebhookAuthTests(TestCase):
    def _request(self, header=''):
        from types import SimpleNamespace
        return SimpleNamespace(META={'HTTP_AUTHORIZATION': header} if header else {})

    def test_location_scoped_accepts_missing_header(self):
        from onestepgps_app.views import _verify_webhook_auth
        from types import SimpleNamespace

        integration = SimpleNamespace(webhook_username='ui-user', webhook_password='ui-pass')
        self.assertTrue(
            _verify_webhook_auth(self._request(), integration=integration, location_scoped=True)
        )

    def test_env_basic_accepted_when_integration_differs(self):
        import base64
        from django.test import override_settings
        from onestepgps_app.views import _verify_webhook_auth
        from types import SimpleNamespace

        integration = SimpleNamespace(webhook_username='ui-user', webhook_password='ui-pass')
        token = base64.b64encode(b'Testauth:testauth@123').decode()
        request = self._request(f'Basic {token}')
        with override_settings(ONESTEPGPS_WEBHOOK_USERNAME='Testauth', ONESTEPGPS_WEBHOOK_PASSWORD='testauth@123'):
            self.assertTrue(_verify_webhook_auth(request, integration=integration, location_scoped=True))

    def test_wrong_basic_rejected_when_not_location_scoped(self):
        import base64
        from django.test import override_settings
        from onestepgps_app.views import _verify_webhook_auth

        token = base64.b64encode(b'bad:creds').decode()
        request = self._request(f'Basic {token}')
        with override_settings(ONESTEPGPS_WEBHOOK_USERNAME='Testauth', ONESTEPGPS_WEBHOOK_PASSWORD='testauth@123'):
            self.assertFalse(_verify_webhook_auth(request, integration=None, location_scoped=False))


class OneStepGPSOfficialDataQueueTests(TestCase):
    def test_unwraps_schema_value_alert_drive_dtc(self):
        from onestepgps_app.osg_dataqueue import extract_official_dataqueue_bundle

        payload = [
            {
                'schema': 'alert',
                'value': {
                    'device_id': 'dev-9',
                    'alert_id': 'a-77',
                    'alert_name': 'Harold Speeding',
                    'device_name': 'Elias',
                    'alert_time_utc': '2026-09-01T16:22:13Z',
                    'speed_mph': 73,
                    'lat': 29.78,
                    'lng': -95.55,
                },
            },
            {
                'schema': 'drive_stop',
                'value': {
                    'device_id': 'dev-9',
                    'drive_stop': {
                        'type': 'drive',
                        'time_from': '2026-03-27T20:49:49Z',
                        'time_to': '2026-03-27T21:19:49Z',
                        'distance': {'value': 24000, 'unit': 'm', 'display': '24 km'},
                        'duration': {'value': 1800, 'unit': 's', 'display': '30m 0s'},
                        'idle_duration': {'value': 120, 'unit': 's', 'display': '2m 0s'},
                        'top_speed': {'value': 108, 'unit': 'km/h', 'display': '108 km/h'},
                        'lat_lng_from': {'lat': 32.79, 'lng': -116.93},
                        'lat_lng_to': {'lat': 32.81, 'lng': -116.95},
                        'zone_from_list': [{'name': 'Warehouse'}],
                        'zone_to_list': [{'name': 'Office'}],
                    },
                },
            },
            {
                'schema': 'dtc',
                'value': {
                    'device_id': 'dev-9',
                    'code': 'P0420',
                    'dtc_log_id': 'dtc-1',
                    'dt_tracker': '2018-08-27T05:34:55Z',
                },
            },
        ]
        bundle = extract_official_dataqueue_bundle(payload)
        self.assertEqual(len(bundle.alerts), 1)
        self.assertEqual(bundle.alerts[0].alert_name, 'Harold Speeding')
        self.assertEqual(len(bundle.trips), 1)
        self.assertEqual(bundle.trips[0].kind, 'drive')
        self.assertAlmostEqual(bundle.trips[0].distance_miles or 0, 24000 / 1609.34, places=2)
        self.assertEqual(bundle.trips[0].start_address, 'Warehouse')
        self.assertEqual(len(bundle.maintenance), 1)
        self.assertEqual(bundle.maintenance[0].dtc_codes, ['P0420'])
