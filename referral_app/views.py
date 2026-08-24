from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import GHLAuthCredentials
from accounts.permissions import AccountScopedPermission, IsManagementUserPermission
from referral_app import services
from referral_app.serializers import (
    EnsureLinkSerializer,
    ReferralClaimSerializer,
    ReferralProgramUpdateSerializer,
)


class OwnerReferralDashboardView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        return Response(services.owner_dashboard(request.account))


class OwnerReferralProgramView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request):
        return Response(services.serialize_program(request.account))

    def patch(self, request):
        serializer = ReferralProgramUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        try:
            program = services.update_program(request.account, serializer.validated_data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(services.serialize_program(request.account, program))


class OwnerEnsureReferralLinkView(APIView):
    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def post(self, request):
        serializer = EnsureLinkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            payload = services.ensure_link_for_contact_id(
                request.account,
                serializer.validated_data["contact_id"],
            )
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(payload)


class OwnerContactReferralCreditView(APIView):
    """Read available referral credit for a contact (quote / create-job UIs)."""

    permission_classes = [AccountScopedPermission, IsManagementUserPermission]

    def get(self, request, contact_id):
        from accounts.models import Contact
        from referral_app.money import cents_to_dollars

        contact = Contact.objects.filter(account=request.account, pk=contact_id).first()
        if not contact:
            return Response({"detail": "Contact not found."}, status=status.HTTP_404_NOT_FOUND)
        program = services.get_or_create_program(request.account)
        available = services.available_credit_cents(request.account, contact)

        pending = services.pending_attribution_for_contact(request.account, contact)
        pending_referral = None
        if pending:
            pending_referral = {
                "referral_id": str(pending.id),
                "referrer_name": services.contact_display_name(pending.referrer_contact),
                "friend_discount_cents": pending.friend_discount_cents,
                "friend_discount_dollars": float(cents_to_dollars(pending.friend_discount_cents)),
                "discount_disabled": pending.discount_disabled,
                "discount_job_id": str(pending.discount_job_id) if pending.discount_job_id else None,
            }

        return Response(
            {
                "contact_id": contact.id,
                "program_enabled": bool(program.enabled),
                "available_credit_cents": available,
                "available_credit_dollars": float(cents_to_dollars(available)),
                "lifetime_credit_cents": services.lifetime_credit_cents(request.account, contact),
                "pending_referral": pending_referral,
            }
        )


class PublicReferralClaimPageView(APIView):
    """GET claim-page branding + offer for a referral code."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, code):
        payload = services.get_claim_page_payload(code)
        if not payload:
            return Response({"detail": "Referral link not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(payload)


class PublicReferralClaimView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = ReferralClaimSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            result = services.claim_referral(**serializer.validated_data)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(result, status=status.HTTP_201_CREATED)


class PublicCustomerHubView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request, code):
        payload = services.get_customer_hub(code)
        if not payload:
            return Response({"detail": "Referral account not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(payload)


class PublicProgramByLocationView(APIView):
    """Public program snapshot for customer hub landing (by location_id)."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        location_id = (
            request.query_params.get("location_id")
            or request.META.get("HTTP_X_LOCATION_ID")
            or ""
        ).strip()
        if not location_id:
            return Response(
                {"detail": "location_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        account = GHLAuthCredentials.objects.filter(location_id=location_id, is_active=True).first()
        if not account:
            return Response({"detail": "Account not found."}, status=status.HTTP_404_NOT_FOUND)
        program = services.get_or_create_program(account)
        if not program.enabled:
            return Response({"detail": "Referral program is not active."}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            {
                "business_name": account.company_name or "Business",
                "short_name": (account.company_name or "Business").split()[0],
                "logo_url": account.company_logo_url or "",
                "primary_color": program.primary_color,
                "accent_color": program.accent_color,
                "service_label": program.service_label,
                "referrer_reward_cents": program.referrer_reward_cents,
                "friend_reward_cents": program.friend_reward_cents,
                "minimum_invoice_cents": program.minimum_invoice_cents,
                "terms_text": program.terms_text,
                "location_id": location_id,
            }
        )
