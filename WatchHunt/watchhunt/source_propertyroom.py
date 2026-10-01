"""PropertyRoom.com watch category pages (police and seized property auctions).

There is no API, so this parses category pages sorted by newest first.
It checks robots.txt before fetching and keeps request volume low.
Card text on the site looks like:
  "Quick View Movado Museum Juro Watch 10h 33m | $55.00 | 20 bids"
"""
import re
import time
from urllib import robotparser
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from models import Listing

BASE = "https://www.propertyroom.com"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
LISTING_HREF = re.compile(r"/l/[^/?#]+/(\d+)")
CARD = re.compile(
    r"^(?P<title>.+?)\s+(?P<left>\d+d\s+\d+h|\d+h\s+\d+m|\d+m)\s*\|\s*\$(?P<price>[\d,]+(?:\.\d+)?)"
    r"(?:\s*\|\s*(?P<bids>\d+)\s*bids?)?",
    re.I,
)
PREFIX = re.compile(r"^(?:free shipping\s+)?(?:quick view\s+)?", re.I)


class PropertyRoomSource:
    name = "propertyroom"

    def __init__(self, cfg, search_cfg, max_price=None):
        self.max_price = max_price
        self.cfg, self.search_cfg = cfg, search_cfg
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        self.robots = robotparser.RobotFileParser(urljoin(BASE, "/robots.txt"))
        try:
            self.robots.read()
        except Exception:
            self.robots = None

    def _allowed(self, url):
        return self.robots is None or self.robots.can_fetch(UA, url)

    def _get(self, url):
        if not self._allowed(url):
            print(f"  [propertyroom] robots.txt disallows {url}, skipping")
            return None
        r = self.session.get(url, timeout=30)
        r.raise_for_status()
        time.sleep(self.search_cfg.get("polite_delay_seconds", 2))
        return r.text

    def fetch(self):
        lo, hi = self.search_cfg["min_price"], self.max_price or self.search_cfg["max_price"]
        found = {}
        for path in self.cfg["category_paths"]:
            for page in range(1, self.cfg.get("pages_per_category", 3) + 1):
                url = f"{BASE}{path}?sort=openedrecently&page={page}"
                try:
                    html = self._get(url)
                except requests.RequestException as e:
                    print(f"  [propertyroom] {url} failed: {e}")
                    break
                if not html:
                    break
                cards = self._parse_cards(html)
                if not cards:
                    break
                for lst in cards:
                    prev = found.get(lst.item_id)
                    if prev:
                        # an image-only anchor and a text anchor can point to the same item
                        keep, other = (lst, prev) if lst.title and not prev.title else (prev, lst)
                        if not keep.image_urls and other.image_urls:
                            keep.image_urls = other.image_urls
                        found[lst.item_id] = keep
                        continue
                    found[lst.item_id] = lst
        for lst in found.values():
            if lst.title and lo <= lst.price <= hi:
                yield lst

    def _parse_cards(self, html):
        soup = BeautifulSoup(html, "html.parser")
        out = []
        for a in soup.find_all("a", href=True):
            m = LISTING_HREF.search(a["href"])
            if not m:
                continue
            text = PREFIX.sub("", " ".join(a.get_text(" ").split()))
            img = a.find("img")
            img_url = None
            if img:
                img_url = img.get("data-src") or img.get("src")
                if img_url and not img_url.startswith("http"):
                    img_url = urljoin(BASE, img_url)
            cm = CARD.search(text)
            if cm:
                out.append(Listing(
                    source="propertyroom",
                    item_id=m.group(1),
                    title=cm.group("title").strip(),
                    url=urljoin(BASE, a["href"]),
                    price=float(cm.group("price").replace(",", "")),
                    bids=int(cm.group("bids")) if cm.group("bids") else None,
                    time_left=cm.group("left"),
                    buying_format="auction" if cm.group("bids") else "buy_now",
                    image_urls=[img_url] if img_url else [],
                ))
            elif img_url:
                out.append(Listing(source="propertyroom", item_id=m.group(1), title="",
                                   url=urljoin(BASE, a["href"]), image_urls=[img_url]))
        return out

    def enrich(self, listing: Listing):
        try:
            html = self._get(listing.url)
        except requests.RequestException as e:
            print(f"  [propertyroom] detail failed for {listing.item_id}: {e}")
            return listing
        if not html:
            return listing
        soup = BeautifulSoup(html, "html.parser")
        imgs = []
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            imgs.append(og["content"])
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("src") or ""
            if "content.propertyroom.com/listings" in src and "banner" not in src.lower() and src not in imgs:
                imgs.append(src)
        if imgs:
            listing.image_urls = imgs
        desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
        if desc and desc.get("content"):
            listing.description = desc["content"][:3000]
        return listing

    PRICE_ON_PAGE = re.compile(r"(?:current bid|current price|price)[^$]{0,40}\$\s?([\d,]+(?:\.\d{2})?)", re.I)
    BIDS_ON_PAGE = re.compile(r"(\d+)\s+bids?\b", re.I)
    ENDED_ON_PAGE = re.compile(r"auction (?:has )?(?:ended|closed)|this item is no longer available", re.I)

    def refresh(self, listing: Listing):
        """Best-effort page parse. PropertyRoom has no API, so this is the most fragile refresher."""
        try:
            html = self._get(listing.url)
        except requests.RequestException as e:
            if getattr(e, "response", None) is not None and e.response.status_code == 404:
                return False
            return True
        if not html:
            return True
        text = " ".join(BeautifulSoup(html, "html.parser").get_text(" ").split())
        if self.ENDED_ON_PAGE.search(text):
            return False
        m = self.PRICE_ON_PAGE.search(text)
        if m:
            listing.price = float(m.group(1).replace(",", ""))
        b = self.BIDS_ON_PAGE.search(text)
        if b:
            listing.bids = int(b.group(1))
        return True
