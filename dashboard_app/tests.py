"""
Lead funnel report tests — filter semantics aligned with calendar / reference dashboard app.

Date rules (lead_funnel_report):
- Leads & open/rejected/submitted quotes: created in range
- Scheduled quotes (accepted): created in range OR linked job scheduled_at in range
- Jobs (scheduled / in progress / cancelled / closed): scheduled_at in range
- Quotes to convert (to_convert): created_at in range
- location_id query param is ignored (account scope only)
"""
from datetime import datetime, timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Contact, GHLAuthCredentials
from jobtracker_app.models import Job, JobAssignment
from quote_app.models import CustomerSubmission
from service_app.models import User


class LeadFunnelReportTests(APITestCase):
    def setUp(self):
        self.account = GHLAuthCredentials.objects.create(
            user_id='funnel-test-account',
            access_token='access-token',
            refresh_token='refresh-token',
            expires_in=3600,
            location_id='loc-funnel-test',
        )
        self.other_location_id = 'loc-other-should-not-filter'
        self.admin = User.objects.create_user(
            username='funnel-admin',
            email='funnel-admin@example.com',
            password='password',
            role=User.ROLE_MANAGER,
            account=self.account,
        )
        self.other_tech = User.objects.create_user(
            username='other-tech',
            email='other-tech@example.com',
            password='password',
            role=User.ROLE_WORKER,
            account=self.account,
        )
        self.client.force_authenticate(user=self.admin)

        self.range_start = timezone.make_aware(datetime(2026, 6, 1, 0, 0, 0))
        self.range_end = timezone.make_aware(datetime(2026, 6, 30, 23, 59, 59))
        self.before_range = timezone.make_aware(datetime(2026, 5, 15, 12, 0, 0))
        self.after_range = timezone.make_aware(datetime(2026, 7, 15, 12, 0, 0))
        self.in_range_mid = timezone.make_aware(datetime(2026, 6, 15, 12, 0, 0))

    def _report(self, **params):
        base = {
            'start_date': self.range_start.date().isoformat(),
            'end_date': self.range_end.date().isoformat(),
        }
        base.update(params)
        return self.client.get('/api/dashboard/invoices/lead_funnel_report/', base)

    def _create_submission(self, *, status='draft', created_at=None, final_total='100.00'):
        submission = CustomerSubmission.objects.create(
            account=self.account,
            house_sqft=1500,
            status=status,
            final_total=Decimal(final_total),
        )
        if created_at:
            CustomerSubmission.objects.filter(pk=submission.pk).update(created_at=created_at)
            submission.refresh_from_db()
        return submission

    def _create_job(
        self,
        *,
        status='pending',
        scheduled_at=None,
        created_at=None,
        contact=None,
        total_price='200.00',
        assignee=None,
    ):
        job = Job.objects.create(
            account=self.account,
            title='Funnel test job',
            status=status,
            scheduled_at=scheduled_at,
            total_price=Decimal(total_price),
            contact=contact,
        )
        updates = {}
        if created_at:
            updates['created_at'] = created_at
        if updates:
            Job.objects.filter(pk=job.pk).update(**updates)
            job.refresh_from_db()
        if assignee:
            JobAssignment.objects.create(job=job, user=assignee, role='technician')
        return job

    def test_closed_jobs_use_scheduled_at_not_created_at(self):
        """Completed jobs count when scheduled_at is in range, even if created_at is outside."""
        self._create_job(
            status='completed',
            scheduled_at=self.in_range_mid,
            created_at=self.before_range,
        )
        self._create_job(
            status='completed',
            scheduled_at=self.before_range,
            created_at=self.in_range_mid,
        )

        response = self._report()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['lead_funnel']['closed_jobs']['count'], 1)

    def test_open_estimates_use_submission_created_at(self):
        self._create_submission(status='draft', created_at=self.in_range_mid)
        self._create_submission(status='responses_completed', created_at=self.before_range)

        response = self._report()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['lead_funnel']['open_estimates']['count'], 1)

    def test_scheduled_quotes_include_accepted_with_job_scheduled_in_range(self):
        submission = self._create_submission(status='accepted', created_at=self.before_range)
        self._create_job(
            status='pending',
            scheduled_at=self.in_range_mid,
            submission=submission,
        )

        response = self._report()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['lead_funnel']['scheduled_quotes']['count'], 1)

    def test_location_id_query_param_does_not_exclude_jobs_without_contact(self):
        self._create_job(
            status='completed',
            scheduled_at=self.in_range_mid,
            contact=None,
        )

        response = self._report(location_id=self.other_location_id)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['lead_funnel']['closed_jobs']['count'], 1)

    def test_assignee_filter_limits_job_stages(self):
        job_a = self._create_job(
            status='in_progress',
            scheduled_at=self.in_range_mid,
            assignee=self.admin,
        )
        self._create_job(
            status='in_progress',
            scheduled_at=self.in_range_mid,
            assignee=self.other_tech,
        )
        self.assertIsNotNone(job_a)

        all_response = self._report()
        self.assertEqual(all_response.data['lead_funnel']['in_progress_jobs']['count'], 2)

        filtered = self._report(assignee_ids=str(self.admin.id))
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.data['lead_funnel']['in_progress_jobs']['count'], 1)
        self.assertIn(self.admin.id, filtered.data['report_period']['assignee_user_ids'])

    def test_estimate_to_convert_uses_created_at(self):
        self._create_job(status='to_convert', created_at=self.in_range_mid, scheduled_at=None)
        self._create_job(status='to_convert', created_at=self.before_range, scheduled_at=None)

        response = self._report()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['lead_funnel']['estimate_to_convert']['count'], 1)

    def test_cancelled_includes_onhold_by_scheduled_at(self):
        self._create_job(status='cancelled', scheduled_at=self.in_range_mid)
        self._create_job(status='onhold', scheduled_at=self.in_range_mid)

        response = self._report()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data['lead_funnel']['cancelled_jobs']['count'], 2)

    def test_report_period_matches_request_dates(self):
        response = self._report()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        period = response.data['report_period']
        self.assertEqual(period['start_date'], '2026-06-01')
        self.assertEqual(period['end_date'], '2026-06-30')
        self.assertIn('scheduled date', period['filter_description'].lower())

    def test_closed_revenue_nets_referral_credit_and_discount(self):
        job = self._create_job(
            status='completed',
            scheduled_at=self.in_range_mid,
            total_price='458.00',
        )
        Job.objects.filter(pk=job.pk).update(
            referral_credit_amount=Decimal('50.00'),
            referral_discount_amount=Decimal('25.00'),
            apply_referral_discount=True,
            discount_type=Job.DISCOUNT_TYPE_AMOUNT,
            discount_value=Decimal('10.00'),
        )

        response = self._report()
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        closed = response.data['lead_funnel']['closed_jobs']
        self.assertEqual(closed['count'], 1)
        self.assertEqual(closed['gross_value'], 458.0)
        self.assertEqual(closed['referral_credit_applied'], 50.0)
        self.assertEqual(closed['referral_discount_applied'], 25.0)
        # 458 - 10 manual - 25 friend - 50 wallet
        self.assertEqual(closed['total_value'], 373.0)
        self.assertEqual(response.data['summary_metrics']['total_revenue_closed_jobs'], 373.0)

        bonus = response.data['referral_bonus']
        self.assertEqual(bonus['gross_closed_revenue'], 458.0)
        self.assertEqual(bonus['net_closed_revenue'], 373.0)
        self.assertEqual(bonus['wallet_credit_applied'], 50.0)
        self.assertEqual(bonus['friend_discount_applied'], 25.0)
        self.assertEqual(bonus['manual_discount_applied'], 10.0)
        self.assertIn('pending_wallet_balance_cents', bonus)
        self.assertIn('pending_referrals_count', bonus)