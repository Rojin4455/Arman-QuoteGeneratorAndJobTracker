from unittest.mock import patch, MagicMock

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import GHLAuthCredentials, Contact, Address
from quote_app.models import CustomerSubmission
from service_app.models import User


class PublicQuoteGeneratorTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.account = GHLAuthCredentials.objects.create(
            location_id='loc_public_test',
            access_token='test-token',
            refresh_token='refresh',
            company_id='co1',
            user_id='u1',
            expires_in=3600,
            is_active=True,
        )
        self.other_account = GHLAuthCredentials.objects.create(
            location_id='loc_other',
            access_token='other-token',
            refresh_token='refresh',
            company_id='co2',
            user_id='u2',
            expires_in=3600,
            is_active=True,
        )
        self.staff = User.objects.create_user(
            username='tech1',
            email='tech@example.com',
            password='pass12345',
            account=self.account,
        )
        self.existing_contact = Contact.objects.create(
            account=self.account,
            contact_id='ghl_existing_1',
            first_name='Existing',
            last_name='Customer',
            email='existing@example.com',
            phone='5551112222',
            location_id=self.account.location_id,
        )
        Contact.objects.create(
            account=self.other_account,
            contact_id='ghl_other_1',
            first_name='Other',
            last_name='Tenant',
            email='other@example.com',
            phone='5559998888',
            location_id=self.other_account.location_id,
        )

    def test_contact_search_denied_without_staff_email(self):
        response = self.client.get(
            '/api/quote/contacts/search/',
            {'location_id': self.account.location_id, 'search': 'Existing'},
        )
        self.assertEqual(response.status_code, 403)

    def test_contact_search_allowed_with_staff_email(self):
        response = self.client.get(
            '/api/quote/contacts/search/',
            {
                'location_id': self.account.location_id,
                'search': 'Existing',
                'email': 'tech@example.com',
            },
        )
        self.assertEqual(response.status_code, 200)
        results = response.data.get('results') or response.data
        emails = [c.get('email') for c in results]
        self.assertIn('existing@example.com', emails)
        self.assertNotIn('other@example.com', emails)

    def test_invalid_location_rejected_for_public_start(self):
        response = self.client.post(
            '/api/quote/public/start-submission/?location_id=does_not_exist',
            {
                'first_name': 'Ada',
                'last_name': 'Lovelace',
                'email': 'ada@example.com',
                'phone': '5550001111',
                'street_address': '1 Analytical Engine Rd',
                'city': 'London',
                'state': 'UK',
                'postal_code': 'EC1',
                'house_sqft': 1200,
            },
            format='json',
        )
        self.assertIn(response.status_code, (400, 403))

    @patch('quote_app.helpers.create_or_update_ghl_contact_for_public')
    def test_public_start_creates_submission_with_public_origin(self, mock_ghl):
        mock_ghl.return_value = 'ghl_new_public_1'
        response = self.client.post(
            f'/api/quote/public/start-submission/?location_id={self.account.location_id}',
            {
                'first_name': 'Ada',
                'last_name': 'Lovelace',
                'email': 'ada@example.com',
                'phone': '5550001111',
                'street_address': '1 Analytical Engine Rd',
                'city': 'London',
                'state': 'UK',
                'postal_code': 'EC1',
                'house_sqft': 1200,
                'first_time': True,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['quote_origin'], 'public')
        submission = CustomerSubmission.objects.get(id=response.data['submission_id'])
        self.assertEqual(submission.quote_origin, CustomerSubmission.QUOTE_ORIGIN_PUBLIC)
        self.assertEqual(submission.account_id, self.account.id)
        self.assertEqual(submission.contact.email, 'ada@example.com')
        self.assertIsNotNone(submission.address_id)
        mock_ghl.assert_called_once()

    def test_create_submission_cannot_set_public_origin(self):
        address = Address.objects.create(
            contact=self.existing_contact,
            address_id='addr_1',
            street_address='10 Main',
            city='Austin',
            state='TX',
            postal_code='78701',
            order=1,
        )
        response = self.client.post(
            f'/api/quote/create-submission/?location_id={self.account.location_id}',
            {
                'contact': self.existing_contact.id,
                'address': address.id,
                'house_sqft': 1500,
                'first_time': False,
                'quote_origin': 'public',
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_create_submission_defaults_to_technician_origin(self):
        address = Address.objects.create(
            contact=self.existing_contact,
            address_id='addr_2',
            street_address='10 Main',
            city='Austin',
            state='TX',
            postal_code='78701',
            order=1,
        )
        response = self.client.post(
            f'/api/quote/create-submission/?location_id={self.account.location_id}',
            {
                'contact': self.existing_contact.id,
                'address': address.id,
                'house_sqft': 1500,
                'first_time': False,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        submission = CustomerSubmission.objects.get(id=response.data['submission_id'])
        self.assertEqual(submission.quote_origin, CustomerSubmission.QUOTE_ORIGIN_TECHNICIAN)

    def test_initial_data_hides_employees_for_public(self):
        response = self.client.get(
            '/api/quote/initial-data/',
            {'location_id': self.account.location_id, 'public': '1'},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data.get('project_employees'), [])
