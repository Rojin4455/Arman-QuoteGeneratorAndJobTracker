from django.urls import path

from referral_app import views

urlpatterns = [
    path("owner/dashboard/", views.OwnerReferralDashboardView.as_view(), name="referral-owner-dashboard"),
    path("owner/program/", views.OwnerReferralProgramView.as_view(), name="referral-owner-program"),
    path("owner/gift-card/", views.OwnerGiftCardView.as_view(), name="referral-owner-gift-card"),
    path("owner/ensure-link/", views.OwnerEnsureReferralLinkView.as_view(), name="referral-owner-ensure-link"),
    path(
        "owner/contact/<int:contact_id>/credit/",
        views.OwnerContactReferralCreditView.as_view(),
        name="referral-owner-contact-credit",
    ),
    path("public/program/", views.PublicProgramByLocationView.as_view(), name="referral-public-program"),
    path("public/claim/<str:code>/", views.PublicReferralClaimPageView.as_view(), name="referral-claim-page"),
    path("public/claim/", views.PublicReferralClaimView.as_view(), name="referral-claim"),
    path("public/customer/<str:code>/", views.PublicCustomerHubView.as_view(), name="referral-customer-hub"),
]
