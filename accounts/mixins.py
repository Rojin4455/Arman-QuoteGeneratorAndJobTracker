"""
Mixins for account-scoped querysets.

Use AccountScopedQuerysetMixin on list/detail views so get_queryset() filters
by request.account. Set account_lookup on the view (or subclass) for the filter path.
"""
class AccountScopedQuerysetMixin:
    """
    Filter queryset by request.account.

    Set account_lookup on the view:
    - 'account' for models with direct account FK (Service, Location, CustomerSubmission, etc.)
    - 'service__account' for models under Service (Question, Package, Feature, etc.)
    - 'submission__account' for models under CustomerSubmission
    """

    # Override in subclass or on the view class
    account_lookup = "account"

    def get_queryset(self):
        qs = super().get_queryset()
        account = getattr(self.request, "account", None)
        if account is None:
            return qs.none()

        print(qs.filter(**{self.account_lookup: account}))
        print(self.account_lookup)
        print(account)
        return qs.filter(**{self.account_lookup: account})
