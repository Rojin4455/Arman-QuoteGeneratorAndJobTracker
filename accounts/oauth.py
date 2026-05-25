from urllib.parse import quote, urlencode


GHL_MARKETPLACE_BASE_URL = "https://marketplace.gohighlevel.com"
GHL_MARKETPLACE_OAUTH_PATH = "/v2/oauth/chooselocation"


def build_ghl_marketplace_auth_url(*, redirect_uri, client_id, scope, version_id=""):
    params = {
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "scope": scope,
    }
    if version_id:
        params["version_id"] = version_id
    return (
        f"{GHL_MARKETPLACE_BASE_URL}{GHL_MARKETPLACE_OAUTH_PATH}?"
        f"{urlencode(params, quote_via=quote)}"
    )
