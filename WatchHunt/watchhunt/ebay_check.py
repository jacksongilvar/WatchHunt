"""Quick eBay health check, run every hour on GitHub.

Writes ebay_status.json so problems with the eBay keys are visible instead of
silently returning nothing. Never prints or saves the keys themselves.
"""
import base64
import json
import os
from datetime import datetime, timezone

import requests

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"


def check():
    status = {"checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    cid, secret = os.getenv("EBAY_CLIENT_ID", ""), os.getenv("EBAY_CLIENT_SECRET", "")
    status["client_id_set"] = bool(cid)
    status["client_secret_set"] = bool(secret)
    if not (cid and secret):
        status["result"] = "keys_missing"
        status["fix"] = "Add EBAY_CLIENT_ID and EBAY_CLIENT_SECRET under Settings > Secrets and variables > Actions."
        return status
    status["client_id_looks_like_sandbox"] = "SBX" in cid.upper()
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    try:
        r = requests.post(TOKEN_URL, timeout=30,
                          headers={"Authorization": f"Basic {basic}",
                                   "Content-Type": "application/x-www-form-urlencoded"},
                          data={"grant_type": "client_credentials",
                                "scope": "https://api.ebay.com/oauth/api_scope"})
    except requests.RequestException as e:
        status["result"] = "token_request_failed"
        status["detail"] = str(e)[:300]
        return status
    if not r.ok:
        status["result"] = "token_rejected"
        status["http_status"] = r.status_code
        status["detail"] = r.text[:300]
        status["fix"] = ("eBay refused the keys. Check they are the Production keyset (App ID and Cert ID, "
                         "not Sandbox) and that the keyset is enabled on developer.ebay.com.")
        return status
    token = r.json().get("access_token", "")
    try:
        s = requests.get(SEARCH_URL, timeout=30,
                         headers={"Authorization": f"Bearer {token}", "X-EBAY-C-MARKETPLACE-ID": "EBAY_US"},
                         params={"q": "seiko 7s26", "category_ids": "31387", "limit": 5})
    except requests.RequestException as e:
        status["result"] = "search_request_failed"
        status["detail"] = str(e)[:300]
        return status
    if not s.ok:
        status["result"] = "search_rejected"
        status["http_status"] = s.status_code
        status["detail"] = s.text[:300]
        return status
    j = s.json()
    status["result"] = "ok"
    status["test_search_total"] = j.get("total")
    status["sample_titles"] = [i.get("title", "")[:80] for i in (j.get("itemSummaries") or [])[:3]]
    return status


if __name__ == "__main__":
    st = check()
    with open("ebay_status.json", "w") as f:
        json.dump(st, f, indent=2)
    print("eBay check:", st.get("result"), "|", st.get("fix") or st.get("detail") or st.get("test_search_total"))
