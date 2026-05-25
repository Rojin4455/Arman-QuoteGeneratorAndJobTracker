import json
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlparse

from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from accounts.models import GHLAuthCredentials, GHLCompanyAuth, Location
from accounts.oauth import build_ghl_marketplace_auth_url


class GHLOAuthUrlTests(TestCase):
    def test_build_ghl_marketplace_auth_url_uses_v2_path_and_version_id(self):
        url = build_ghl_marketplace_auth_url(
            redirect_uri="https://frontend.example.com/oauth/location-callback",
            client_id="client-123",
            scope="locations.readonly contacts.readonly",
            version_id="version-456",
        )

        parsed = urlparse(url)
        params = parse_qs(parsed.query)

        self.assertEqual(parsed.path, "/v2/oauth/chooselocation")
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(
            params["redirect_uri"],
            ["https://frontend.example.com/oauth/location-callback"],
        )
        self.assertEqual(params["client_id"], ["client-123"])
        self.assertEqual(params["scope"], ["locations.readonly contacts.readonly"])
        self.assertEqual(params["version_id"], ["version-456"])


class GHLOAuthTokensTests(TestCase):
    @patch("accounts.views.sync_calendars_from_ghl_task.delay")
    @patch("accounts.views.sync_all_users_to_db")
    @patch("accounts.views.sync_custom_fields_to_db")
    @patch("accounts.views.fetch_all_contacts_task.delay")
    @patch("accounts.views.LocationServices.pull_location")
    @patch("accounts.views.requests.get")
    @patch("accounts.views.requests.post")
    @patch("accounts.views.GHL_CLIENT_ID", "test-app-123")
    def test_tokens_bulk_company_oauth_saves_multiple_locations(
        self,
        post_mock,
        get_mock,
        pull_location_mock,
        contacts_delay_mock,
        custom_fields_mock,
        sync_users_mock,
        calendars_delay_mock,
    ):
        company_token_response = Mock()
        company_token_response.ok = True
        company_token_response.status_code = 200
        company_token_response.text = "{}"
        company_token_response.json.return_value = {
            "access_token": "company-token",
            "refresh_token": "company-refresh",
            "expires_in": 3600,
            "scope": "scope-one",
            "userType": "Company",
            "companyId": "company-1",
            "userId": "agency-user",
        }

        cache.set("ghl_bulk_oauth_primary:company-1", "loc-2", timeout=300)

        preferred_location_response = Mock()
        preferred_location_response.raise_for_status = Mock()
        preferred_location_response.json.return_value = {
            "access_token": "location-token-2",
            "refresh_token": "location-refresh-2",
            "expires_in": 3600,
            "scope": "scope-one",
            "userType": "Location",
            "companyId": "company-1",
            "locationId": "loc-2",
            "userId": "agency-user",
        }

        second_location_response = Mock()
        second_location_response.raise_for_status = Mock()
        second_location_response.json.return_value = {
            "access_token": "location-token-1",
            "refresh_token": "location-refresh-1",
            "expires_in": 3600,
            "scope": "scope-one",
            "userType": "Location",
            "companyId": "company-1",
            "locationId": "loc-1",
            "userId": "agency-user",
        }

        post_mock.side_effect = [
            company_token_response,
            preferred_location_response,
            second_location_response,
        ]

        installed_locations_response = Mock()
        installed_locations_response.raise_for_status = Mock()
        installed_locations_response.json.return_value = {
            "locations": [
                {"id": "loc-1"},
                {"id": "loc-2"},
            ]
        }
        get_mock.return_value = installed_locations_response

        def pull_location_side_effect(location_id):
            location = Mock()
            location.name = f"Location {location_id}"
            location.timezone = "America/Chicago"
            return location, True

        pull_location_mock.side_effect = pull_location_side_effect

        response = self.client.get(
            "/api/accounts/auth/tokens/",
            {
                "code": "auth-code",
                "redirect_uri": "https://frontend.example.com/oauth/location-callback",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["location_id"], "loc-2")
        self.assertEqual(response.json()["connected_locations"], 2)
        self.assertCountEqual(response.json()["connected_location_ids"], ["loc-1", "loc-2"])
        self.assertTrue(response.json()["company_level_oauth"])
        self.assertIsNone(cache.get("ghl_bulk_oauth_primary:company-1"))

        self.assertTrue(GHLCompanyAuth.objects.filter(company_id="company-1").exists())
        self.assertEqual(GHLAuthCredentials.objects.count(), 2)

        first_credentials = GHLAuthCredentials.objects.get(location_id="loc-1")
        second_credentials = GHLAuthCredentials.objects.get(location_id="loc-2")
        self.assertEqual(first_credentials.user_id, "agency-user")
        self.assertEqual(second_credentials.user_id, "agency-user")
        self.assertEqual(first_credentials.company_name, "Location loc-1")
        self.assertEqual(second_credentials.company_name, "Location loc-2")

        contacts_delay_mock.assert_called_once_with("loc-2", "location-token-2")
        custom_fields_mock.assert_called_once_with("loc-2", "location-token-2")
        sync_users_mock.assert_called_once_with("loc-2", "location-token-2")
        calendars_delay_mock.assert_called_once_with("loc-2", "location-token-2")


class GHLWebhookTests(TestCase):
    @patch("accounts.views.sync_calendars_from_ghl_task.delay")
    @patch("accounts.views.sync_all_users_to_db")
    @patch("accounts.views.sync_custom_fields_to_db")
    @patch("accounts.views.fetch_all_contacts_task.delay")
    @patch("accounts.views.LocationServices.pull_location")
    @patch("accounts.views.requests.post")
    def test_install_webhook_creates_location_credentials(
        self,
        post_mock,
        pull_location_mock,
        contacts_delay_mock,
        custom_fields_mock,
        sync_users_mock,
        calendars_delay_mock,
    ):
        GHLCompanyAuth.objects.create(
            company_id="company-1",
            access_token="company-token",
            refresh_token="company-refresh",
            expires_in=3600,
            scope="scope-one",
            user_id="agency-user",
        )

        location_token_response = Mock()
        location_token_response.raise_for_status = Mock()
        location_token_response.json.return_value = {
            "access_token": "location-token-1",
            "refresh_token": "location-refresh-1",
            "expires_in": 3600,
            "scope": "scope-one",
            "userType": "Location",
            "companyId": "company-1",
            "locationId": "loc-1",
            "userId": "agency-user",
        }
        post_mock.return_value = location_token_response

        location = Mock()
        location.name = "Main Branch"
        location.timezone = "America/Chicago"
        pull_location_mock.return_value = (location, True)

        response = self.client.post(
            "/api/accounts/webhook/",
            data=json.dumps(
                {
                    "type": "INSTALL",
                    "locationId": "loc-1",
                    "companyId": "company-1",
                    "userId": "agency-user",
                    "companyName": "Main Branch",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["action"], "created")
        self.assertEqual(cache.get("ghl_bulk_oauth_primary:company-1"), "loc-1")

        credentials = GHLAuthCredentials.objects.get(location_id="loc-1")
        self.assertEqual(credentials.access_token, "location-token-1")
        self.assertEqual(credentials.company_id, "company-1")
        self.assertEqual(credentials.company_name, "Main Branch")

        contacts_delay_mock.assert_called_once_with("loc-1", "location-token-1")
        custom_fields_mock.assert_called_once_with("loc-1", "location-token-1")
        sync_users_mock.assert_called_once_with("loc-1", "location-token-1")
        calendars_delay_mock.assert_called_once_with("loc-1", "location-token-1")

    def test_uninstall_webhook_deletes_credentials_and_deactivates_location(self):
        GHLAuthCredentials.objects.create(
            user_id="agency-user",
            access_token="location-token-1",
            refresh_token="location-refresh-1",
            expires_in=3600,
            scope="scope-one",
            user_type="Location",
            company_id="company-1",
            location_id="loc-1",
            company_name="Main Branch",
        )
        Location.objects.create(
            id="loc-1",
            company_id="company-1",
            name="Main Branch",
            address="123 Main St",
            city="Chicago",
            state="IL",
            country="US",
            postal_code="60601",
            website="",
            timezone="America/Chicago",
            first_name="Test",
            last_name="User",
            email="test@example.com",
            phone="1234567890",
            automatic_mobile_app_invite=False,
            date_added=timezone.now(),
            domain="example.com",
            is_active=True,
        )

        response = self.client.post(
            "/api/accounts/webhook/",
            data=json.dumps({"type": "UNINSTALL", "locationId": "loc-1"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["action"], "uninstalled")
        self.assertFalse(GHLAuthCredentials.objects.filter(location_id="loc-1").exists())
        self.assertFalse(Location.objects.get(pk="loc-1").is_active)
