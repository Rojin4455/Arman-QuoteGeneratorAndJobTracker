"""Unit tests for dashboard job revenue expressions (no API auth)."""
from datetime import timedelta
from decimal import Decimal

from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from accounts.models import GHLAuthCredentials
from dashboard_app.views import (
    _job_gross_revenue_expression,
    _job_manual_discount_expression,
    _job_referral_credit_expression,
    _job_referral_discount_expression,
    _job_revenue_sum_expression,
    _referral_bonus_summary_for_account,
)
from jobtracker_app.models import Job


class JobRevenueExpressionTests(TestCase):
    def setUp(self):
        self.account = GHLAuthCredentials.objects.create(
            user_id="rev-expr-account",
            access_token="access-token",
            refresh_token="refresh-token",
            expires_in=3600,
            location_id="loc-rev-expr",
            is_active=True,
        )

    def test_net_revenue_subtracts_manual_referral_discount_and_credit(self):
        job = Job.objects.create(
            account=self.account,
            title="Revenue expr job",
            status="completed",
            scheduled_at=timezone.now(),
            total_price=Decimal("458.00"),
            total_surcharge=Decimal("0.00"),
            referral_credit_amount=Decimal("50.00"),
            referral_discount_amount=Decimal("25.00"),
            apply_referral_discount=True,
            discount_type=Job.DISCOUNT_TYPE_AMOUNT,
            discount_value=Decimal("10.00"),
        )
        qs = Job.objects.filter(pk=job.pk)
        self.assertEqual(qs.aggregate(s=Sum(_job_gross_revenue_expression()))["s"], Decimal("458.00"))
        self.assertEqual(qs.aggregate(s=Sum(_job_manual_discount_expression()))["s"], Decimal("10.00"))
        self.assertEqual(qs.aggregate(s=Sum(_job_referral_discount_expression()))["s"], Decimal("25.00"))
        self.assertEqual(qs.aggregate(s=Sum(_job_referral_credit_expression()))["s"], Decimal("50.00"))
        self.assertEqual(qs.aggregate(s=Sum(_job_revenue_sum_expression()))["s"], Decimal("373.00"))

    def test_referral_discount_ignored_when_admin_disabled(self):
        job = Job.objects.create(
            account=self.account,
            title="Disabled discount",
            status="completed",
            scheduled_at=timezone.now(),
            total_price=Decimal("100.00"),
            referral_discount_amount=Decimal("25.00"),
            apply_referral_discount=False,
            referral_credit_amount=Decimal("10.00"),
        )
        qs = Job.objects.filter(pk=job.pk)
        self.assertEqual(qs.aggregate(s=Sum(_job_referral_discount_expression()))["s"], Decimal("0.00"))
        self.assertEqual(qs.aggregate(s=Sum(_job_revenue_sum_expression()))["s"], Decimal("90.00"))

    def test_referral_bonus_summary_shapes(self):
        now = timezone.now()
        Job.objects.create(
            account=self.account,
            title="Closed with credit",
            status="completed",
            scheduled_at=now,
            total_price=Decimal("200.00"),
            referral_credit_amount=Decimal("50.00"),
        )
        start = now - timedelta(days=1)
        end = now + timedelta(days=1)
        summary = _referral_bonus_summary_for_account(self.account, start, end)
        self.assertEqual(summary["gross_closed_revenue"], 200.0)
        self.assertEqual(summary["net_closed_revenue"], 150.0)
        self.assertEqual(summary["wallet_credit_applied"], 50.0)
        self.assertIn("pending_wallet_balance_cents", summary)
        self.assertIn("pending_referrals_count", summary)
