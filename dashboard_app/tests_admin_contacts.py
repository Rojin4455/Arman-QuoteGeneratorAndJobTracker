from datetime import datetime, timedelta

from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Contact, GHLAuthCredentials
from dashboard_app.models import Invoice
from jobtracker_app.models import Job
from quote_app.models import CustomerSubmission


class AdminContactListTests(APITestCase):
    def setUp(self):
        self.location_id = 'loc-contacts-list'
        self.account = GHLAuthCredentials.objects.create(
            user_id='contacts-list-account',
            access_token='access-token',
            refresh_token='refresh-token',
            expires_in=3600,
            location_id=self.location_id,
            is_active=True,
        )
        now = timezone.make_aware(datetime(2026, 8, 24, 12, 0, 0))
        self.contacts = []
        for i in range(30):
            contact = Contact.objects.create(
                account=self.account,
                contact_id=f'ghl-list-{i:02d}',
                first_name=f'Cust{i}',
                last_name='Test',
                email=f'cust{i}@example.com',
                location_id=self.location_id,
                date_added=now - timedelta(minutes=i),
            )
            self.contacts.append(contact)

        featured = self.contacts[0]
        Job.objects.create(
            account=self.account,
            contact=featured,
            title='Pending job',
            status='pending',
        )
        Job.objects.create(
            account=self.account,
            contact=featured,
            title='Done job',
            status='completed',
        )
        CustomerSubmission.objects.create(
            account=self.account,
            contact=featured,
            house_sqft=1200,
            status='draft',
        )
        Invoice.objects.create(
            account=self.account,
            invoice_id='inv-featured',
            contact_id=featured.contact_id,
            status='sent',
        )

    def _list(self, **params):
        query = {'location_id': self.location_id, **params}
        return self.client.get('/api/dashboard/contacts/', query)

    def test_first_page_returns_page_size(self):
        resp = self._list(page=1, page_size=25)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 30)
        self.assertEqual(len(resp.data['results']), 25)

    def test_second_page_does_not_404(self):
        resp = self._list(page=2, page_size=25)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data['count'], 30)
        self.assertEqual(len(resp.data['results']), 5)

    def test_page_past_end_clamps_instead_of_invalid_page(self):
        resp = self._list(page=99, page_size=25)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.data['results']), 5)

    def test_page_counts_use_page_ids_only(self):
        resp = self._list(page=1, page_size=25, ordering='-date_added')
        self.assertEqual(resp.status_code, 200)
        featured = next(row for row in resp.data['results'] if row['contact_id'] == 'ghl-list-00')
        self.assertEqual(featured['jobs_count'], 2)
        self.assertEqual(featured['pending_jobs_count'], 1)
        self.assertEqual(featured['submissions_count'], 1)
        self.assertEqual(featured['invoices_count'], 1)
        empty = next(row for row in resp.data['results'] if row['contact_id'] == 'ghl-list-01')
        self.assertEqual(empty['jobs_count'], 0)
        self.assertEqual(empty['invoices_count'], 0)
