"""eBay via the official Browse API (no scraping).

Needs EBAY_CLIENT_ID and EBAY_CLIENT_SECRET from developer.ebay.com
(a production keyset, application access token).
"""
import base64
import re
import time
from urllib.parse import quote

import requests

from models import Listing, query_items

TOKEN_URL = "https://api.ebay.com/identity/v1/oauth2/token"
SEARCH_URL = "https://api.ebay.com/buy/browse/v1/item_summary/search"
ITEM_URL = "https://api.ebay.com/buy/browse/v1/item/{}"


def _strip_html(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()


def _shipping(d):
    """Cheapest stated shipping cost, or None when eBay does not give one (calculated shipping)."""
    costs = []
    for o in d.get("shippingOptions") or []:
        c = (o.get("shippingCost") or {}).get("value")
        try:
            costs.append(float(c))
        except (TypeError, ValueError):
            pass
    return min(costs) if costs else None


class EbaySource:
    name = "ebay"

    def __init__(self, client_id, client_secret, cfg, search_cfg):
        self.client_id, self.client_secret = client_id, client_secret
        self.cfg, self.search_cfg = cfg, search_cfg
        self._token, self._expires = None, 0

    def _auth_headers(self):
        if not self._token or time.time() > self._expires - 60:
            basic = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            r = requests.post(
                TOKEN_URL,
                headers={"Authorization": f"Basic {basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials",
                      "scope": "https://api.ebay.com/oauth/api_scope"},
                timeout=30,
            )
            r.raise_for_status()
            j = r.json()
            self._token = j["access_token"]
            self._expires = time.time() + int(j.get("expires_in", 7200))
        return {"Authorization": f"Bearer {self._token}",
                "X-EBAY-C-MARKETPLACE-ID": self.cfg.get("marketplace", "EBAY_US")}

    def fetch(self):
        lo, hi = self.search_cfg["min_price"], self.search_cfg["max_price"]
        seen = set()
        for q, q_hi in query_items(self.cfg["queries"], hi):
            params = {
                "q": q,
                "limit": self.cfg.get("limit_per_query", 100),
                "sort": "newlyListed",
                "filter": f"price:[{lo}..{q_hi}],priceCurrency:USD,buyingOptions:{{AUCTION|FIXED_PRICE}}",
            }
            if self.cfg.get("category_ids"):
                params["category_ids"] = ",".join(self.cfg["category_ids"])
            try:
                r = requests.get(SEARCH_URL, headers=self._auth_headers(), params=params, timeout=30)
                r.raise_for_status()
            except requests.RequestException as e:
                print(f"  [ebay] query '{q}' failed: {e}")
                continue
            for s in r.json().get("itemSummaries", []) or []:
                if s["itemId"] in seen:
                    continue
                seen.add(s["itemId"])
                yield self._to_listing(s)
            time.sleep(0.5)

    def _to_listing(self, s):
        is_auction = "AUCTION" in (s.get("buyingOptions") or [])
        price_obj = s.get("currentBidPrice") if is_auction and s.get("currentBidPrice") else s.get("price") or {}
        images = []
        if s.get("image", {}).get("imageUrl"):
            images.append(s["image"]["imageUrl"])
        images += [i["imageUrl"] for i in s.get("additionalImages", []) or [] if i.get("imageUrl")]
        return Listing(
            source="ebay",
            item_id=s["itemId"],
            title=s.get("title", ""),
            url=s.get("itemWebUrl", ""),
            price=float(price_obj.get("value", 0) or 0),
            currency=price_obj.get("currency", "USD"),
            bids=s.get("bidCount") if is_auction else None,
            end_time=s.get("itemEndDate"),
            buying_format="auction" if is_auction else "buy_now",
            image_urls=images,
            description=s.get("shortDescription", "") or "",
            seller=(s.get("seller") or {}).get("username", ""),
            shipping=_shipping(s),
        )

    def enrich(self, listing: Listing):
        """Pull full description and every image for a shortlisted item."""
        try:
            r = requests.get(ITEM_URL.format(quote(listing.item_id, safe="")),
                             headers=self._auth_headers(), timeout=30)
            r.raise_for_status()
            d = r.json()
        except requests.RequestException as e:
            print(f"  [ebay] detail failed for {listing.item_id}: {e}")
            return listing
        listing.description = _strip_html(d.get("description") or d.get("shortDescription") or listing.description)[:3000]
        imgs = [d.get("image", {}).get("imageUrl")] + [i.get("imageUrl") for i in d.get("additionalImages", []) or []]
        imgs = [i for i in imgs if i]
        if imgs:
            listing.image_urls = imgs
        if _shipping(d) is not None:
            listing.shipping = _shipping(d)
        return listing

    def refresh(self, listing: Listing):
        """Update price and bids. Returns False once the listing has ended or disappeared."""
        try:
            r = requests.get(ITEM_URL.format(quote(listing.item_id, safe="")),
                             headers=self._auth_headers(), timeout=30)
        except requests.RequestException:
            return True  # network blip, try again next cycle
        if r.status_code == 404:
            return False
        if not r.ok:
            return True
        d = r.json()
        price_obj = d.get("currentBidPrice") or d.get("price") or {}
        listing.price = float(price_obj.get("value", listing.price) or listing.price)
        listing.bids = d.get("bidCount", listing.bids)
        listing.end_time = d.get("itemEndDate", listing.end_time)
        return True
