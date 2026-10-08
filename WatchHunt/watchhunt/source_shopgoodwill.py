"""ShopGoodwill via the same JSON backend its website uses.

This is an unofficial endpoint. If ShopGoodwill changes it, open
shopgoodwill.com, run a search with the browser Network tab open, copy the
POST body sent to /api/Search/ItemListing and update _body() below.
Run `python main.py --sources shopgoodwill --debug` to dump raw responses.
"""
import json
import re
import statistics
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

from models import Listing, query_items

SEARCH_URL = "https://buyerapi.shopgoodwill.com/api/Search/ItemListing"
DETAIL_URL = "https://buyerapi.shopgoodwill.com/api/itemDetail/GetItemDetailModelByItemId/{}"
HEADERS = {
    "Content-Type": "application/json",
    "Origin": "https://shopgoodwill.com",
    "Referer": "https://shopgoodwill.com/",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
}


def _body(query, page=1, page_size=40, closed=False, lo=0, hi=999999):
    return {
        "isSize": False, "isWeddingCatagory": "false", "isMultipleCategoryIds": False,
        "isFromHeaderMenuTab": False, "layout": "", "isFromHomePage": False,
        "searchText": query, "selectedGroup": "", "selectedCategoryIds": "",
        "selectedSellerIds": "", "lowPrice": str(lo), "highPrice": str(hi),
        "searchBuyNowOnly": "", "searchPickupOnly": "false", "searchNoPickupOnly": "false",
        "searchOneCentShippingOnly": "false", "searchDescriptions": "false",
        "searchClosedAuctions": "true" if closed else "false",
        "closedAuctionEndingDate": "", "closedAuctionDaysBack": "90" if closed else "7",
        "searchCanadaShipping": "false", "searchInternationalShippingOnly": "false",
        "sortColumn": "1", "page": str(page), "pageSize": str(page_size),
        "sortDescending": "false", "savedSearchId": 0, "useBuyerPrefs": "true",
        "searchUSOnlyShipping": "false", "categoryLevelNo": "1", "categoryLevel": 1,
        "categoryId": 0, "partNumber": "", "catIds": "",
    }


PACIFIC = ZoneInfo("America/Los_Angeles")


def to_utc(value):
    """ShopGoodwill end times are Pacific time with no timezone attached. Return UTC ISO with Z."""
    if not value:
        return value
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return value
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=PACIFIC)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_items(obj):
    """Find the list of item dicts wherever the API nests it."""
    if isinstance(obj, dict):
        sr = obj.get("searchResults")
        if isinstance(sr, dict) and isinstance(sr.get("items"), list):
            return sr["items"]
        for v in obj.values():
            found = _find_items(v)
            if found:
                return found
    elif isinstance(obj, list) and obj and isinstance(obj[0], dict) and "itemId" in obj[0]:
        return obj
    return []


def _img(url):
    if not url:
        return None
    url = url.replace("\\", "/")
    return url if url.startswith("http") else f"https://shopgoodwillimages.azureedge.net/production/{url.lstrip('/')}"


class ShopGoodwillSource:
    name = "shopgoodwill"

    def __init__(self, cfg, search_cfg, debug=False):
        self.cfg, self.search_cfg, self.debug = cfg, search_cfg, debug

    def _post(self, body):
        r = requests.post(SEARCH_URL, headers=HEADERS, data=json.dumps(body), timeout=30)
        r.raise_for_status()
        j = r.json()
        if self.debug:
            with open("debug_shopgoodwill.json", "w") as f:
                json.dump(j, f, indent=2)
        return j

    def fetch(self):
        lo, hi = self.search_cfg["min_price"], self.search_cfg["max_price"]
        seen = set()
        for q, q_hi in query_items(self.cfg["queries"], hi):
            try:
                j = self._post(_body(q, page_size=self.cfg.get("page_size", 40), lo=lo, hi=q_hi))
            except (requests.RequestException, ValueError) as e:
                print(f"  [shopgoodwill] query '{q}' failed: {e}")
                continue
            for it in _find_items(j):
                iid = str(it.get("itemId"))
                if iid in seen:
                    continue
                seen.add(iid)
                img = _img(it.get("imageURL") or it.get("imageUrl"))
                yield Listing(
                    source="shopgoodwill",
                    item_id=iid,
                    title=it.get("title", ""),
                    url=f"https://shopgoodwill.com/item/{iid}",
                    price=float(it.get("currentPrice") or it.get("minimumBid") or 0),
                    bids=it.get("numBids"),
                    end_time=to_utc(it.get("endTime")),
                    buying_format="auction",
                    image_urls=[img] if img else [],
                    seller=str(it.get("sellerName") or ""),
                )
            time.sleep(self.search_cfg.get("polite_delay_seconds", 2))

    def enrich(self, listing: Listing):
        try:
            r = requests.get(DETAIL_URL.format(listing.item_id), headers=HEADERS, timeout=30)
            r.raise_for_status()
            d = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  [shopgoodwill] detail failed for {listing.item_id}: {e}")
            return listing
        desc = d.get("description") or ""
        listing.description = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc)).strip()[:3000]
        server = (d.get("imageServer") or "").rstrip("/")
        paths = [p for p in (d.get("imageUrlString") or "").split(";") if p.strip()]
        imgs = [f"{server}/{p.strip().lstrip('/')}".replace("\\", "/") if server else _img(p) for p in paths]
        if imgs:
            listing.image_urls = imgs
        time.sleep(self.search_cfg.get("polite_delay_seconds", 2))
        return listing

    def sold_comps(self, query: str):
        """Recent ShopGoodwill closed-auction prices for a query. Real data, small sample."""
        try:
            j = self._post(_body(query, page_size=40, closed=True))
        except (requests.RequestException, ValueError):
            return None
        prices = [float(it.get("currentPrice") or 0) for it in _find_items(j)]
        prices = [p for p in prices if p > 0]
        if not prices:
            return {"query": query, "count": 0}
        prices.sort()
        q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0], statistics.median(prices), prices[-1]]
        return {"query": query, "count": len(prices), "median": round(statistics.median(prices), 2),
                "p25": round(q[0], 2), "p75": round(q[2], 2), "low": prices[0], "high": prices[-1]}

    def get(self, item_id):
        """Build a Listing for one item id (used for the manual watchlist). Returns None if it can't be read."""
        try:
            r = requests.get(DETAIL_URL.format(item_id), headers=HEADERS, timeout=30)
            r.raise_for_status()
            d = r.json()
        except (requests.RequestException, ValueError) as e:
            print(f"  [shopgoodwill] watchlist item {item_id} failed: {e}")
            return None
        if not d.get("itemId"):
            return None
        lst = Listing(
            source="shopgoodwill",
            item_id=str(item_id),
            title=d.get("title", ""),
            url=f"https://shopgoodwill.com/item/{item_id}",
            price=float(d.get("currentPrice") or d.get("minimumBid") or 0),
            bids=d.get("numberOfBids"),
            end_time=to_utc(d.get("endTime")),
            buying_format="auction",
            seller=str(d.get("sellerCompanyName") or ""),
        )
        ship = d.get("shippingPrice")
        if ship and float(ship) > 0.01:
            lst.shipping = float(ship) + float(d.get("handlingPrice") or 0)
        desc = d.get("description") or ""
        lst.description = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", desc)).strip()[:3000]
        server = (d.get("imageServer") or "").rstrip("/")
        paths = [p for p in (d.get("imageUrlString") or "").split(";") if p.strip()]
        lst.image_urls = [f"{server}/{p.strip().lstrip('/')}".replace("\\", "/") if server else _img(p) for p in paths]
        return lst

    def refresh(self, listing: Listing):
        """Update price and bids. Returns False once the auction has ended."""
        try:
            r = requests.get(DETAIL_URL.format(listing.item_id), headers=HEADERS, timeout=30)
            r.raise_for_status()
            d = r.json()
        except (requests.RequestException, ValueError):
            return True
        for k in ("currentPrice", "currentBid"):
            if d.get(k) is not None:
                listing.price = float(d[k])
                break
        for k in ("numberOfBids", "numBids", "bidCount"):
            if d.get(k) is not None:
                listing.bids = int(d[k])
                break
        listing.end_time = to_utc(d.get("endTime")) or to_utc(listing.end_time)
        if d.get("isEnded") or d.get("itemEnded"):
            return False
        return True
