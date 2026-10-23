"""Listings from saved-search alert emails (eBay, ShopGoodwill, HiBid, LiveAuctioneers, GovDeals, Mercari, ...).

Why email: most of these sites forbid scraping in their terms, but all of them will email you new listings that
match a saved search. Those emails are yours to read. WatchHunt reads them from one mailbox folder over IMAP,
pulls out item links, titles and prices, scores them like any other listing and puts them on the board.
It does NOT visit the sites. Photos in the email are only sent to the AI check if you turn on
inbox.ai_on_email_images (off by default: downloading a site's images is still automated access).

One-time setup (about 15 minutes):
  1. Use a Gmail account (a new one just for alerts is cleanest). Turn on 2-Step Verification, then create an
     App Password: Google Account > Security > App passwords. Copy the 16-character password.
  2. In Gmail, create a label called WatchHunt and a filter that applies it to alert emails
     (from: ebay.com OR shopgoodwill.com OR hibid.com OR liveauctioneers.com OR govdeals.com ...).
  3. On each site, save your searches and turn on email alerts to that Gmail address.
  4. In GitHub: Settings > Secrets and variables > Actions > New secret:
       INBOX_USER = the Gmail address, INBOX_PASS = the app password
     and add both to the env: block in .github/workflows/watchhunt.yml (see the commit that added this file).
  5. config.yaml: inbox.enabled: true
"""
import email
import imaplib
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from urllib.parse import unquote

from bs4 import BeautifulSoup

from models import Listing

# site name, regex for an item link (group 1 = item id), URL to rebuild. Patterns marked "unverified" were written
# from the sites' public URL formats and should be checked against a real alert email (python source_inbox.py --test).
SITES = [
    ("ebay", re.compile(r"ebay\.com/itm/(?:[^/?#]+/)?(\d{9,14})", re.I), "https://www.ebay.com/itm/{}"),
    ("shopgoodwill", re.compile(r"shopgoodwill\.com/item/(\d{6,})", re.I), "https://shopgoodwill.com/item/{}"),
    ("hibid", re.compile(r"hibid\.com/(?:[a-z-]+/)?lot/(\d{5,})", re.I), "https://hibid.com/lot/{}"),            # unverified
    ("liveauctioneers", re.compile(r"liveauctioneers\.com/item/(\d{5,})", re.I), "https://www.liveauctioneers.com/item/{}"),
    ("govdeals", re.compile(r"govdeals\.com/.*?(?:itemid=|asset/)(\d+(?:[-/]\d+)?)", re.I), "https://www.govdeals.com/asset/{}"),  # unverified
    ("mercari", re.compile(r"mercari\.com/us/item/(m\d{6,})", re.I), "https://www.mercari.com/us/item/{}"),
    ("craigslist", re.compile(r"(https?://[a-z]+\.craigslist\.org/[^\s\"'<>]+?/(\d{9,11})\.html)", re.I), "{}"),
    ("auctionninja", re.compile(r"auctionninja\.com/[^\s\"'<>]*?/product/[^\s\"'<>]*?(\d{5,})", re.I), None),  # unverified
    ("proxibid", re.compile(r"proxibid\.com/[^\s\"'<>]*?lot-information/(\d{5,})", re.I), None),                   # unverified
]
PRICE = re.compile(r"(?:US\s?)?\$\s?(\d{1,3}(?:,\d{3})*(?:\.\d{2})?)")


def _decode(v):
    try:
        return str(make_header(decode_header(v or "")))
    except Exception:
        return v or ""


def _html(msg):
    """The HTML body of an email, or its plain text wrapped so links still parse."""
    plain = ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        kind = part.get_content_type()
        if kind not in ("text/html", "text/plain"):
            continue
        text = (part.get_payload(decode=True) or b"").decode(part.get_content_charset() or "utf-8", errors="replace")
        if kind == "text/html":
            return text
        plain = plain or text
    return re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', plain)


def match_site(href):
    """(site, item_id, clean_url) for an item link, following URL-encoded redirect targets."""
    for candidate in (href, unquote(href), unquote(unquote(href))):
        for site, rx, fmt in SITES:
            m = rx.search(candidate)
            if m:
                if site == "craigslist":
                    return site, m.group(2), m.group(1)
                item = m.group(1)
                return site, item, fmt.format(item) if fmt else m.group(0) if m.group(0).startswith("http") \
                    else "https://www." + m.group(0)
    return None


def parse_email(html_text, received=None):
    """Listings found in one alert email."""
    soup = BeautifulSoup(html_text, "html.parser")
    found = {}
    for a in soup.find_all("a", href=True):
        hit = match_site(a["href"])
        if not hit:
            continue
        site, item, url = hit
        title = " ".join(a.get_text(" ", strip=True).split())
        img = a.find("img")
        rec = found.setdefault((site, item), {"site": site, "item": item, "url": url, "title": "", "price": None,
                                              "image": None})
        if len(title) > len(rec["title"]) and not PRICE.fullmatch(title) and title.lower() not in ("view item", "bid now"):
            rec["title"] = title
        if img and img.get("src", "").startswith("http") and not rec["image"]:
            rec["image"] = img["src"]
        if rec["price"] is None:
            # The price usually sits in the same table cell or row as the link.
            node = a
            for _ in range(4):
                node = node.parent
                if node is None:
                    break
                m = PRICE.search(node.get_text(" ", strip=True))
                if m:
                    rec["price"] = float(m.group(1).replace(",", ""))
                    break
    out = []
    for rec in found.values():
        if not rec["title"]:
            continue
        out.append(Listing(source=rec["site"], item_id=rec["item"], title=rec["title"], url=rec["url"],
                           price=rec["price"] or 0.0, image_urls=[rec["image"]] if rec["image"] else [],
                           seller="email alert", time_left=None,
                           end_time=None))
    return out


class InboxSource:
    name = "inbox"

    def __init__(self, cfg, user, password):
        self.cfg, self.user, self.password = cfg or {}, user, password

    def fetch(self):
        host = self.cfg.get("imap_host", "imap.gmail.com")
        folder = self.cfg.get("folder", "WatchHunt")
        since = (datetime.now(timezone.utc) - timedelta(days=self.cfg.get("days_back", 3))).strftime("%d-%b-%Y")
        try:
            box = imaplib.IMAP4_SSL(host)
            box.login(self.user, self.password)
            status, _ = box.select(f'"{folder}"', readonly=True)
            if status != "OK":
                print(f"  [inbox] folder {folder} not found")
                return
            _, ids = box.search(None, "SINCE", since)
            for num in (ids[0].split() if ids and ids[0] else [])[-self.cfg.get("max_emails", 200):]:
                _, data = box.fetch(num, "(RFC822)")
                msg = email.message_from_bytes(data[0][1])
                for lst in parse_email(_html(msg)):
                    if not self.cfg.get("ai_on_email_images", False):
                        lst.image_urls = []
                    lst.description = f"From alert email: {_decode(msg.get('Subject'))}"
                    yield lst
            box.logout()
        except (imaplib.IMAP4.error, OSError) as e:
            print(f"  [inbox] could not read mail: {e}")

    def enrich(self, listing):
        return listing  # never visits the site


if __name__ == "__main__":
    # python source_inbox.py --test saved_alert.eml   (save an alert email from Gmail: More > Download message)
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--test":
        with open(sys.argv[2], "rb") as f:
            m = email.message_from_bytes(f.read())
        for lst in parse_email(_html(m)):
            print(f"{lst.source:15} {lst.item_id:14} ${lst.price:<8} {lst.title[:70]}  {lst.url}")
    else:
        print("usage: python source_inbox.py --test saved_alert.eml")
