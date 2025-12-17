from django.urls import path
from accounts.views import auth_connect,tokens,callback,sync_all_contacts_and_address,webhook_handler,sync_all_users


urlpatterns = [
    path("auth/connect/", auth_connect, name="oauth_connect"),
    path("auth/tokens/", tokens, name="oauth_tokens"),
    path("auth/callback/", callback, name="oauth_callback"),
    path("sync_contacts/", sync_all_contacts_and_address, name="sync_contacts"),
    path("sync_users/", sync_all_users, name="sync_users"),
    path("webhook/",webhook_handler, name="webhook"),
]