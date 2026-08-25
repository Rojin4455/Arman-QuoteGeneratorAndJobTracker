from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from accounts.models import Contact, GHLAuthCredentials
from referral_app import services
from referral_app.hooks import apply_credit_before_invoice_create
from referral_app.models import (
    CustomerCreditLedger,
    ReferralAttribution,
    ReferralLink,
    ReferralProgram,
)
from referral_app.tasks import backfill_referral_links
from service_app.models import User


def make_account(location_id="loc-ref-1", company_name="TruShine"):
    return GHLAuthCredentials.objects.create(
        user_id=f"u-{location_id}",
        access_token="token",
        refresh_token="refresh",
        expires_in=3600,
        location_id=location_id,
        company_name=company_name,
        is_active=True,
    )


class ReferralLinkTests(TestCase):
    def setUp(self):
        self.account = make_account()

    def test_link_auto_created_on_contact_create(self):
        contact = Contact.objects.create(
            account=self.account,
            contact_id="ghl_auto_1",
            first_name="Auto",
            email="auto@example.com",
            location_id="loc-ref-1",
        )
        self.assertTrue(
            ReferralLink.objects.filter(account=self.account, contact=contact).exists()
        )

    def test_ensure_referral_link_is_stable(self):
        contact = Contact.objects.create(
            account=self.account,
            contact_id="ghl_stable",
            first_name="Stable",
            email="stable@example.com",
            location_id="loc-ref-1",
        )
        link1 = services.ensure_referral_link(self.account, contact)
        link2 = services.ensure_referral_link(self.account, contact)
        self.assertEqual(link1.code, link2.code)
        self.assertEqual(
            ReferralLink.objects.filter(account=self.account, contact=contact).count(), 1
        )

    def test_backfill_creates_missing_links(self):
        contact = Contact.objects.create(
            account=self.account,
            contact_id="ghl_backfill",
            first_name="Old",
            email="old@example.com",
            location_id="loc-ref-1",
        )
        # Simulate a legacy contact without a link.
        ReferralLink.objects.filter(account=self.account, contact=contact).delete()
        result = backfill_referral_links(location_id="loc-ref-1")
        self.assertGreaterEqual(result["created"], 1)
        self.assertTrue(
            ReferralLink.objects.filter(account=self.account, contact=contact).exists()
        )
        # Second run creates nothing new.
        again = backfill_referral_links(location_id="loc-ref-1")
        self.assertEqual(again["created"], 0)


class ReferralClaimTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.referrer = Contact.objects.create(
            account=self.account,
            contact_id="ghl_referrer_1",
            first_name="Maria",
            last_name="S",
            email="maria@example.com",
            phone="+17135550111",
            location_id="loc-ref-1",
        )
        self.program = services.get_or_create_program(self.account)
        self.program.referrer_reward_cents = 2500
        self.program.friend_reward_cents = 2500
        self.program.minimum_invoice_cents = 10000
        self.program.monthly_referrer_cap_cents = 5000
        self.program.save()
        self.link = services.ensure_referral_link(self.account, self.referrer)

    def _mock_contact_factory(self, contact_id):
        """Create the contact only when the claim flow calls GHL (like real life)."""

        def _create(account, *, name, email, phone=None, tags=None):
            first = name.split(" ")[0]
            last = " ".join(name.split(" ")[1:])
            return Contact.objects.create(
                account=account,
                contact_id=contact_id,
                first_name=first,
                last_name=last,
                email=email,
                phone=phone,
                location_id=account.location_id,
            )

        return _create

    def test_claim_creates_pending_attribution_with_discount_snapshot(self):
        with patch(
            "referral_app.services._create_ghl_and_local_contact",
            side_effect=self._mock_contact_factory("ghl_friend_1"),
        ):
            result = services.claim_referral(
                code=self.link.code,
                name="Jordan Lee",
                email="jordan@example.com",
                phone="555-010-0000",
            )
        attr = ReferralAttribution.objects.get(id=result["referral_id"])
        self.assertEqual(attr.status, ReferralAttribution.STATUS_PENDING)
        self.assertEqual(attr.referrer_contact_id, self.referrer.id)
        self.assertEqual(attr.friend_discount_cents, 2500)
        self.assertEqual(attr.referred_phone, "555-010-0000")
        # Referred customer immediately gets their own referral link too.
        self.assertTrue(
            ReferralLink.objects.filter(
                account=self.account, contact=attr.referred_contact
            ).exists()
        )

    def test_self_referral_rejected_by_email(self):
        with self.assertRaises(ValueError):
            services.claim_referral(
                code=self.link.code, name="Maria S", email="maria@example.com"
            )

    def test_self_referral_rejected_by_phone(self):
        with self.assertRaises(ValueError):
            services.claim_referral(
                code=self.link.code,
                name="Maria Again",
                email="other@example.com",
                phone="7135550111",
            )

    def test_existing_customer_by_email_cannot_claim(self):
        Contact.objects.create(
            account=self.account,
            contact_id="ghl_existing",
            first_name="Existing",
            email="existing@example.com",
            location_id="loc-ref-1",
        )
        with self.assertRaises(ValueError):
            services.claim_referral(
                code=self.link.code,
                name="Existing Customer",
                email="existing@example.com",
            )

    def test_existing_customer_by_phone_cannot_claim(self):
        Contact.objects.create(
            account=self.account,
            contact_id="ghl_existing_ph",
            first_name="Phoney",
            email="phoney@example.com",
            phone="(832) 555-0199",
            location_id="loc-ref-1",
        )
        with self.assertRaises(ValueError):
            services.claim_referral(
                code=self.link.code,
                name="New Email Same Phone",
                email="brand-new@example.com",
                phone="832-555-0199",
            )

    def test_duplicate_submission_is_idempotent(self):
        with patch(
            "referral_app.services._create_ghl_and_local_contact",
            side_effect=self._mock_contact_factory("ghl_dup"),
        ):
            first = services.claim_referral(
                code=self.link.code, name="Dup Person", email="dup@example.com"
            )
        second = services.claim_referral(
            code=self.link.code, name="Dup Person", email="dup@example.com"
        )
        self.assertEqual(first["referral_id"], second["referral_id"])
        self.assertTrue(second.get("already_claimed"))
        self.assertEqual(
            ReferralAttribution.objects.filter(account=self.account).count(), 1
        )

    def test_second_referrer_link_rejected_for_referred_customer(self):
        other_referrer = Contact.objects.create(
            account=self.account,
            contact_id="ghl_ref2",
            first_name="Other",
            email="other-ref@example.com",
            location_id="loc-ref-1",
        )
        other_link = services.ensure_referral_link(self.account, other_referrer)
        with patch(
            "referral_app.services._create_ghl_and_local_contact",
            side_effect=self._mock_contact_factory("ghl_multi"),
        ):
            services.claim_referral(
                code=self.link.code, name="Multi", email="multi@example.com"
            )
        with self.assertRaises(ValueError):
            services.claim_referral(
                code=other_link.code, name="Multi", email="multi@example.com"
            )

    def test_disabled_program_rejects_claim(self):
        self.program.enabled = False
        self.program.save()
        with self.assertRaises(ValueError):
            services.claim_referral(
                code=self.link.code, name="Nobody", email="nobody@example.com"
            )


class ReferralJobDiscountTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.referrer = Contact.objects.create(
            account=self.account,
            contact_id="ghl_r",
            first_name="Ref",
            email="ref@example.com",
            location_id="loc-ref-1",
        )
        self.friend = Contact.objects.create(
            account=self.account,
            contact_id="ghl_f",
            first_name="Friend",
            email="friend@example.com",
            location_id="loc-ref-1",
        )
        self.program = services.get_or_create_program(self.account)
        self.program.friend_reward_cents = 2500
        self.program.referrer_reward_cents = 2500
        self.program.minimum_invoice_cents = 10000
        self.program.monthly_referrer_cap_cents = 25000
        self.program.save()
        self.attribution = ReferralAttribution.objects.create(
            account=self.account,
            referrer_contact=self.referrer,
            referred_contact=self.friend,
            referral_code="CODE1",
            referred_email="friend@example.com",
            status=ReferralAttribution.STATUS_PENDING,
            friend_discount_cents=2500,
        )

    def _make_job(self, contact=None, total="150.00", **kwargs):
        from jobtracker_app.models import Job

        return Job.objects.create(
            account=self.account,
            contact=contact or self.friend,
            title="Window Cleaning",
            total_price=Decimal(total),
            **kwargs,
        )

    def test_first_job_gets_referral_discount(self):
        job = self._make_job()
        job.refresh_from_db()
        self.assertEqual(job.referral_attribution_id, self.attribution.id)
        self.assertEqual(job.referral_discount_amount, Decimal("25.00"))
        self.assertTrue(job.apply_referral_discount)
        self.assertEqual(job.revised_total, Decimal("125.00"))

    def test_second_job_does_not_get_discount(self):
        job1 = self._make_job()
        job2 = self._make_job()
        job2.refresh_from_db()
        self.assertIsNone(job2.referral_attribution_id)
        self.assertEqual(job2.revised_total, Decimal("150.00"))

    def test_cancelled_job_releases_discount_for_next_job(self):
        job1 = self._make_job()
        job1.refresh_from_db()
        job1.status = "cancelled"
        job1.save()
        self.attribution.refresh_from_db()
        self.assertIsNone(self.attribution.discount_job_id)

        job2 = self._make_job()
        job2.refresh_from_db()
        self.assertEqual(job2.referral_attribution_id, self.attribution.id)
        self.assertEqual(job2.referral_discount_amount, Decimal("25.00"))

    def test_admin_override_disable_and_reenable(self):
        job = self._make_job()
        job.refresh_from_db()
        result = services.set_job_referral_discount(job, enabled=False, changed_by="admin1")
        self.assertTrue(result["ok"])
        job.refresh_from_db()
        self.attribution.refresh_from_db()
        self.assertFalse(job.apply_referral_discount)
        self.assertTrue(self.attribution.discount_disabled)
        self.assertEqual(self.attribution.discount_disabled_by, "admin1")
        self.assertEqual(job.revised_total, Decimal("150.00"))

        services.set_job_referral_discount(job, enabled=True, changed_by="admin1")
        job.refresh_from_db()
        self.attribution.refresh_from_db()
        self.assertTrue(job.apply_referral_discount)
        self.assertFalse(self.attribution.discount_disabled)
        self.assertEqual(job.revised_total, Decimal("125.00"))

    def test_discount_capped_by_job_total(self):
        job = self._make_job(total="10.00")
        job.refresh_from_db()
        self.assertEqual(job.referral_discount_amount, Decimal("10.00"))
        self.assertEqual(job.revised_total, Decimal("0.00"))

    def test_manual_discount_and_referral_discount_stack_separately(self):
        job = self._make_job()
        job.refresh_from_db()
        from jobtracker_app.models import Job

        Job.objects.filter(pk=job.pk).update(
            discount_type=Job.DISCOUNT_TYPE_AMOUNT, discount_value=Decimal("20.00")
        )
        job.refresh_from_db()
        # 150 - 20 manual - 25 referral = 105
        self.assertEqual(job.revised_total, Decimal("105.00"))


class ReferralRewardTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.referrer = Contact.objects.create(
            account=self.account,
            contact_id="ghl_rw_r",
            first_name="Ref",
            email="rw-ref@example.com",
            location_id="loc-ref-1",
        )
        self.friend = Contact.objects.create(
            account=self.account,
            contact_id="ghl_rw_f",
            first_name="Friend",
            email="rw-friend@example.com",
            location_id="loc-ref-1",
        )
        self.program = services.get_or_create_program(self.account)
        self.program.referrer_reward_cents = 2500
        self.program.friend_reward_cents = 2500
        self.program.minimum_invoice_cents = 10000
        self.program.monthly_referrer_cap_cents = 5000
        self.program.save()
        self.attribution = ReferralAttribution.objects.create(
            account=self.account,
            referrer_contact=self.referrer,
            referred_contact=self.friend,
            referral_code="CODERW",
            referred_email="rw-friend@example.com",
            status=ReferralAttribution.STATUS_PENDING,
            friend_discount_cents=2500,
        )

    def test_reward_credited_to_referrer_only_on_paid(self):
        result = services.qualify_referral_on_invoice_paid(
            account=self.account,
            contact=self.friend,
            invoice_id="inv_1",
            invoice_cents=15000,
        )
        self.assertTrue(result["qualified"])
        self.assertEqual(result["referrer_reward_cents"], 2500)
        self.assertEqual(services.available_credit_cents(self.account, self.referrer), 2500)
        # Friend gets NO post-payment credit (their benefit was the upfront discount).
        self.assertEqual(services.available_credit_cents(self.account, self.friend), 0)
        self.attribution.refresh_from_db()
        self.assertEqual(self.attribution.status, ReferralAttribution.STATUS_QUALIFIED)
        self.assertEqual(self.attribution.reward_credited_cents, 2500)
        self.assertIsNotNone(self.attribution.reward_credited_at)

    def test_duplicate_paid_webhook_credits_once(self):
        services.qualify_referral_on_invoice_paid(
            account=self.account, contact=self.friend, invoice_id="inv_1", invoice_cents=15000
        )
        again = services.qualify_referral_on_invoice_paid(
            account=self.account, contact=self.friend, invoice_id="inv_1", invoice_cents=15000
        )
        self.assertFalse(again["qualified"])
        self.assertEqual(again["reason"], "duplicate_event")
        self.assertEqual(services.available_credit_cents(self.account, self.referrer), 2500)
        self.assertEqual(
            CustomerCreditLedger.objects.filter(
                account=self.account,
                entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            ).count(),
            1,
        )

    def test_below_minimum_does_not_qualify(self):
        result = services.qualify_referral_on_invoice_paid(
            account=self.account, contact=self.friend, invoice_id="inv_low", invoice_cents=5000
        )
        self.assertFalse(result["qualified"])
        self.assertEqual(result["reason"], "below_minimum")

    def test_monthly_cap_gives_partial_then_zero(self):
        # Consume 4000 of the 5000 cap this month.
        services.issue_credit(
            account=self.account,
            contact=self.referrer,
            amount_cents=4000,
            entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            idempotency_key="seed_cap",
            description="prior reward this month",
        )
        result = services.qualify_referral_on_invoice_paid(
            account=self.account, contact=self.friend, invoice_id="inv_cap", invoice_cents=20000
        )
        # Only 1000 remains under the cap — partial credit, not the full 2500.
        self.assertEqual(result["referrer_reward_cents"], 1000)
        self.assertEqual(services.available_credit_cents(self.account, self.referrer), 5000)

    def test_void_reverses_reward_and_marks_reversed(self):
        services.qualify_referral_on_invoice_paid(
            account=self.account, contact=self.friend, invoice_id="inv_void", invoice_cents=15000
        )
        self.assertEqual(services.available_credit_cents(self.account, self.referrer), 2500)
        services.reverse_invoice_credit_application(
            account=self.account, invoice_id="inv_void", reason="voided"
        )
        self.assertEqual(services.available_credit_cents(self.account, self.referrer), 0)
        self.attribution.refresh_from_db()
        self.assertEqual(self.attribution.status, ReferralAttribution.STATUS_REVERSED)
        # Reversal is idempotent.
        services.reverse_invoice_credit_application(
            account=self.account, invoice_id="inv_void", reason="voided"
        )
        self.assertEqual(services.available_credit_cents(self.account, self.referrer), 0)


class ReferralWalletApplicationTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.customer = Contact.objects.create(
            account=self.account,
            contact_id="ghl_w",
            first_name="Wallet",
            email="wallet@example.com",
            location_id="loc-ref-1",
        )
        services.get_or_create_program(self.account)

    def _make_job(self, total="100.00", **kwargs):
        from jobtracker_app.models import Job

        return Job.objects.create(
            account=self.account,
            contact=self.customer,
            title="Job",
            total_price=Decimal(total),
            **kwargs,
        )

    def test_wallet_credit_applied_in_own_field(self):
        services.issue_credit(
            account=self.account,
            contact=self.customer,
            amount_cents=4000,
            entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            idempotency_key="seed_wallet",
            description="reward",
        )
        job = self._make_job()
        applied = apply_credit_before_invoice_create(job)
        job.refresh_from_db()
        self.assertEqual(applied, 4000)
        self.assertEqual(job.referral_credit_amount, Decimal("40.00"))
        # Manual discount fields untouched.
        self.assertIsNone(job.discount_type)
        self.assertEqual(job.revised_total, Decimal("60.00"))
        self.assertEqual(services.available_credit_cents(self.account, self.customer), 0)

    def test_wallet_application_is_idempotent(self):
        services.issue_credit(
            account=self.account,
            contact=self.customer,
            amount_cents=4000,
            entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            idempotency_key="seed_wallet2",
            description="reward",
        )
        job = self._make_job()
        first = apply_credit_before_invoice_create(job)
        second = apply_credit_before_invoice_create(job)
        self.assertEqual(first, 4000)
        self.assertEqual(second, 4000)
        self.assertEqual(services.available_credit_cents(self.account, self.customer), 0)

    def test_wallet_credit_respects_manual_discount(self):
        from jobtracker_app.models import Job

        services.issue_credit(
            account=self.account,
            contact=self.customer,
            amount_cents=20000,
            entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            idempotency_key="seed_wallet3",
            description="reward",
        )
        job = self._make_job()
        Job.objects.filter(pk=job.pk).update(
            discount_type=Job.DISCOUNT_TYPE_AMOUNT, discount_value=Decimal("30.00")
        )
        job.refresh_from_db()
        applied = apply_credit_before_invoice_create(job)
        job.refresh_from_db()
        # Balance after manual discount is 70 — credit capped there, not 200.
        self.assertEqual(applied, 7000)
        self.assertEqual(job.referral_credit_amount, Decimal("70.00"))
        self.assertEqual(job.revised_total, Decimal("0.00"))
        self.assertEqual(
            services.available_credit_cents(self.account, self.customer), 13000
        )


class TruShineCompletionWebhookCreditTests(TestCase):
    """This location invoices via workorder webhook, not create_invoice()."""

    def setUp(self):
        self.location_id = "b8qvo7VooP3JD3dIZU42"
        self.account = make_account(location_id=self.location_id)
        self.customer = Contact.objects.create(
            account=self.account,
            contact_id="ghl_c2",
            first_name="Customer",
            last_name="2",
            email="customer2@test.com",
            location_id=self.location_id,
        )
        services.get_or_create_program(self.account)
        services.issue_credit(
            account=self.account,
            contact=self.customer,
            amount_cents=2500,
            entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            idempotency_key="seed-c2-credit",
            description="referrer reward",
        )

    @patch("jobtracker_app.tasks.requests.post")
    def test_webhook_payload_includes_wallet_credit(self, mock_post):
        from jobtracker_app.models import Job, JobServiceItem
        from jobtracker_app.tasks import send_job_completion_webhook
        from quote_app.models import CustomerSubmission

        mock_post.return_value.status_code = 201
        mock_post.return_value.content = b'{"invoiceId": "inv_c2"}'
        mock_post.return_value.json.return_value = {"invoiceId": "inv_c2"}
        mock_post.return_value.text = '{"invoiceId": "inv_c2"}'

        submission = CustomerSubmission.objects.create(
            account=self.account,
            contact=self.customer,
            house_sqft=1200,
        )
        job = Job.objects.create(
            account=self.account,
            contact=self.customer,
            submission=submission,
            title="Test customer 2",
            customer_email="customer2@test.com",
            customer_name="Customer 2",
            total_price=Decimal("170.00"),
            status="completed",
        )
        JobServiceItem.objects.create(job=job, custom_name="Exterior", price=Decimal("130.00"))
        JobServiceItem.objects.create(job=job, custom_name="Interior", price=Decimal("40.00"))

        result = send_job_completion_webhook(str(job.id))
        self.assertTrue(result.get("success"), result)
        payload = mock_post.call_args.kwargs["json"]
        self.assertEqual(payload["discount"]["type"], "fixed")
        self.assertEqual(payload["discount"]["value"], 25.0)
        job.refresh_from_db()
        self.assertEqual(job.referral_credit_amount, Decimal("25.00"))
        self.assertEqual(services.available_credit_cents(self.account, self.customer), 0)


class JobContactLinkRegressionTests(TestCase):
    """Regression tests for the edit bug that unlinked jobs from contacts."""

    def setUp(self):
        self.account = make_account(location_id="loc-link")
        self.customer = Contact.objects.create(
            account=self.account,
            contact_id="ghl_link",
            first_name="Linked",
            email="linked@example.com",
            location_id="loc-link",
        )
        services.get_or_create_program(self.account)

    def _make_job(self, **kwargs):
        from jobtracker_app.models import Job

        return Job.objects.create(
            account=self.account,
            title="Job",
            total_price=Decimal("100.00"),
            **kwargs,
        )

    def test_update_with_null_contact_id_keeps_contact_fk(self):
        from jobtracker_app.serializers import JobSerializer

        job = self._make_job(contact=self.customer, ghl_contact_id="ghl_link")
        serializer = JobSerializer(
            job,
            data={"title": "Edited", "contact_id": None, "address_id": None},
            partial=True,
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        serializer.save()
        job.refresh_from_db()
        self.assertEqual(job.title, "Edited")
        self.assertEqual(job.contact_id, self.customer.id)

    def test_wallet_credit_applies_via_ghl_contact_fallback(self):
        services.issue_credit(
            account=self.account,
            contact=self.customer,
            amount_cents=3000,
            entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            idempotency_key="seed_fallback",
            description="reward",
        )
        # Job without contact FK, only the GHL id — must still get the credit.
        job = self._make_job(contact=None, ghl_contact_id="ghl_link")
        applied = apply_credit_before_invoice_create(job)
        job.refresh_from_db()
        self.assertEqual(applied, 3000)
        self.assertEqual(job.referral_credit_amount, Decimal("30.00"))
        self.assertEqual(services.available_credit_cents(self.account, self.customer), 0)

    def test_wallet_credit_applies_via_email_fallback(self):
        services.issue_credit(
            account=self.account,
            contact=self.customer,
            amount_cents=2000,
            entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
            idempotency_key="seed_fallback_email",
            description="reward",
        )
        job = self._make_job(contact=None, customer_email="LINKED@example.com")
        applied = apply_credit_before_invoice_create(job)
        job.refresh_from_db()
        self.assertEqual(applied, 2000)
        self.assertEqual(job.referral_credit_amount, Decimal("20.00"))


@override_settings(FRONTEND_URL="https://snapshot.test")
class ReferralApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.account = make_account(location_id="loc-api", company_name="Clean Co")
        self.user = User.objects.create_user(
            username="mgr",
            email="mgr@example.com",
            password="pass12345",
            account=self.account,
            role="manager",
            is_admin=True,
        )
        self.referrer = Contact.objects.create(
            account=self.account,
            contact_id="ghl_api_ref",
            first_name="Chris",
            email="chris@example.com",
            location_id="loc-api",
        )
        self.link = services.ensure_referral_link(self.account, self.referrer)

    def test_public_claim_page(self):
        resp = self.client.get(f"/api/referrals/public/claim/{self.link.code}/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["referral_code"], self.link.code)
        self.assertEqual(resp.data["business_name"], "Clean Co")

    def test_owner_dashboard_requires_auth(self):
        resp = self.client.get("/api/referrals/owner/dashboard/", {"location_id": "loc-api"})
        self.assertIn(resp.status_code, (401, 403))

    def test_owner_dashboard_ok(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.get("/api/referrals/owner/dashboard/", {"location_id": "loc-api"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn("program", resp.data)
        self.assertIn("stats", resp.data)

    def test_owner_dashboard_query_count_stays_flat(self):
        for i in range(20):
            contact = Contact.objects.create(
                account=self.account,
                contact_id=f"ghl_dash_{i}",
                first_name=f"Cust{i}",
                email=f"cust{i}@example.com",
                location_id="loc-api",
            )
            services.issue_credit(
                account=self.account,
                contact=contact,
                amount_cents=1000,
                entry_type=CustomerCreditLedger.TYPE_REFERRER_REWARD,
                idempotency_key=f"dash-credit-{i}",
                description="seed",
            )
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            payload = services.owner_dashboard(self.account)
        self.assertLessEqual(len(ctx), 15)
        self.assertEqual(len(payload["customers"]), 21)
        self.assertEqual(payload["stats"]["credits_available_cents"], 20000)

    def test_owner_program_patch(self):
        self.client.force_authenticate(user=self.user)
        resp = self.client.patch(
            "/api/referrals/owner/program/?location_id=loc-api",
            {"referrer_reward_cents": 3000, "reward_mode": "two_sided"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.data["referrer_reward_cents"], 3000)

    def test_contact_credit_endpoint_includes_pending_referral(self):
        friend = Contact.objects.create(
            account=self.account,
            contact_id="ghl_api_friend",
            first_name="Friend",
            email="api-friend@example.com",
            location_id="loc-api",
        )
        ReferralAttribution.objects.create(
            account=self.account,
            referrer_contact=self.referrer,
            referred_contact=friend,
            referral_code=self.link.code,
            referred_email="api-friend@example.com",
            status=ReferralAttribution.STATUS_PENDING,
            friend_discount_cents=2500,
        )
        self.client.force_authenticate(user=self.user)
        resp = self.client.get(
            f"/api/referrals/owner/contact/{friend.id}/credit/",
            {"location_id": "loc-api"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(resp.data["pending_referral"])
        self.assertEqual(resp.data["pending_referral"]["friend_discount_cents"], 2500)


class ReferralGhlCustomFieldTests(TestCase):
    def setUp(self):
        self.account = make_account(location_id="loc-ghl-ref")
        self.account.access_token = "ghl-token"
        self.account.save(update_fields=["access_token"])
        self.contact = Contact.objects.create(
            account=self.account,
            contact_id="ghl_invite_c",
            first_name="Pat",
            email="pat@example.com",
            location_id="loc-ghl-ref",
            tags=[],
        )

    @patch("referral_app.ghl_sync.requests.put")
    @patch("referral_app.ghl_sync.requests.get")
    @patch("referral_app.ghl_sync.requests.post")
    @patch("accounts.utils.fetch_location_custom_fields")
    def test_writes_referral_link_field_before_invite_tag(
        self, fetch_utils, post_mock, get_mock, put_mock
    ):
        from accounts.models import GHLCustomField
        from referral_app.ghl_sync import push_referral_link_and_invite_tag

        fetch_utils.return_value = {}
        post_mock.return_value.status_code = 201
        post_mock.return_value.json.return_value = {"customField": {"id": "cf_ref_link", "name": "Referral Link"}}
        post_mock.return_value.raise_for_status = lambda: None
        get_mock.return_value.status_code = 200
        get_mock.return_value.json.return_value = {"contact": {"tags": []}}
        put_ok = type("R", (), {"status_code": 200, "text": "{}"})()
        put_mock.return_value = put_ok

        ok = push_referral_link_and_invite_tag(
            self.contact, self.account, "https://app.example.com/r/PAT123"
        )
        self.assertTrue(ok)
        self.assertTrue(
            GHLCustomField.objects.filter(
                account=self.account, field_name="Referral Link", ghl_field_id="cf_ref_link"
            ).exists()
        )
        self.assertGreaterEqual(put_mock.call_count, 2)
        first_payload = put_mock.call_args_list[0].kwargs["json"]
        second_payload = put_mock.call_args_list[1].kwargs["json"]
        self.assertEqual(
            first_payload["customFields"][0]["field_value"],
            "https://app.example.com/r/PAT123",
        )
        self.assertIn("referral invite", second_payload["tags"])

    @patch("referral_app.ghl_sync.requests.put")
    @patch("referral_app.ghl_sync.requests.get")
    @patch("accounts.utils.fetch_location_custom_fields")
    def test_still_tags_if_custom_field_update_fails(self, fetch_utils, get_mock, put_mock):
        from accounts.models import GHLCustomField
        from referral_app.ghl_sync import push_referral_link_and_invite_tag

        GHLCustomField.objects.create(
            account=self.account,
            field_name="Referral Link",
            ghl_field_id="cf_existing",
            field_type="url",
            is_active=True,
        )
        get_mock.return_value.status_code = 200
        get_mock.return_value.json.return_value = {"contact": {"tags": []}}

        def put_side_effect(*args, **kwargs):
            payload = kwargs.get("json") or {}
            resp = type("R", (), {})()
            resp.text = "{}"
            if "customFields" in payload:
                resp.status_code = 500
            else:
                resp.status_code = 200
            return resp

        put_mock.side_effect = put_side_effect

        ok = push_referral_link_and_invite_tag(
            self.contact, self.account, "https://app.example.com/r/PAT123"
        )
        self.assertTrue(ok)
        self.assertEqual(put_mock.call_count, 2)
        self.assertIn("customFields", put_mock.call_args_list[0].kwargs["json"])
        self.assertIn("referral invite", put_mock.call_args_list[1].kwargs["json"]["tags"])


class ReferralJobCompletedInviteTests(TestCase):
    def setUp(self):
        self.account = make_account(location_id="loc-invite")
        self.account.access_token = "ghl-token"
        self.account.save(update_fields=["access_token"])
        self.contact = Contact.objects.create(
            account=self.account,
            contact_id="ghl_old_customer",
            first_name="Old",
            email="old-customer@example.com",
            location_id="loc-invite",
            tags=[],
        )
        ReferralLink.objects.filter(account=self.account, contact=self.contact).delete()
        program = services.get_or_create_program(self.account)
        program.enabled = True
        program.auto_invite_enabled = True
        program.invitation_trigger = ReferralProgram.INVITE_COMPLETED_JOB
        program.save()

    @patch("referral_app.ghl_sync.requests.put")
    @patch("referral_app.ghl_sync.requests.get")
    @patch("accounts.utils.fetch_location_custom_fields")
    def test_job_completed_tags_customer_without_existing_link(
        self, fetch_utils, get_mock, put_mock
    ):
        from accounts.models import GHLCustomField
        from jobtracker_app.models import Job

        GHLCustomField.objects.create(
            account=self.account,
            field_name="Referral Link",
            ghl_field_id="cf_invite",
            field_type="url",
            is_active=True,
        )
        get_mock.return_value.status_code = 200
        get_mock.return_value.json.return_value = {"contact": {"tags": []}}
        put_mock.return_value.status_code = 200
        put_mock.return_value.text = "{}"

        self.assertFalse(
            ReferralLink.objects.filter(account=self.account, contact=self.contact).exists()
        )
        job = Job.objects.create(
            account=self.account,
            contact=self.contact,
            title="Existing customer job",
            total_price=Decimal("100.00"),
            status="completed",
        )
        result = services.handle_job_completed_invitation(job)
        self.assertTrue(result.get("tagged"))
        self.assertIsNotNone(result.get("referral_code"))
        self.assertTrue(
            ReferralLink.objects.filter(account=self.account, contact=self.contact).exists()
        )
        tag_calls = [
            c for c in put_mock.call_args_list if "tags" in (c.kwargs.get("json") or {})
        ]
        self.assertTrue(tag_calls)
        self.assertIn("referral invite", tag_calls[0].kwargs["json"]["tags"])

