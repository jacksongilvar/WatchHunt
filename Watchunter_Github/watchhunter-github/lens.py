"""Google Lens lookups through SerpApi (Google has no public Lens API).

Two jobs:
  1. identify : the titles of visually matching pages go into the Claude prompt as hints
  2. market   : priced matches for the identified watch become an asking-price band

Asking prices are not sold prices. economics.py discounts them before using them as value.
Needs SERPAPI_API_KEY. Each listing costs one SerpApi search.
"""
import re
import statistics

import requests

LENS_URL = "https://serpapi.com/search.json"

# Matches with these words are accessories, parts or fakes, not the watch itself.
JUNK = ["strap only", "band only", "watch band", "watch strap", "bracelet only", "box only", "buckle",
        "clasp only", "dial only", "movement only", "for parts", "parts only", "crystal only", "bezel insert",
        "case only", "homage", "replica", "inspired", "style watch", " mod ", "modded", "custom", "poster"]


def _price(m):
    p = m.get("price") or {}
    v = p.get("extracted_value") or p.get("value")
    if isinstance(v, str):
        v = re.sub(r"[^\d.]", "", v)
        v = float(v) if v else None
    cur = (p.get("currency") or "$").upper()
    if not v or cur not in ("$", "USD", "US$"):
        return None
    return float(v)


class Lens:
    def __init__(self, api_key, cfg):
        self.api_key, self.cfg = api_key, cfg

    def search(self, image_url):
        """Raw Lens results for one image, trimmed to what we use. None on failure."""
        try:
            r = requests.get(LENS_URL, timeout=60, params={
                "engine": "google_lens", "url": image_url, "type": "all",
                "hl": "en", "country": self.cfg.get("country", "us"), "api_key": self.api_key})
            j = r.json()
        except (requests.RequestException, ValueError) as e:
            # Never print the exception text: it contains the request URL, which carries the API key.
            print(f"  [lens] failed: {type(e).__name__}")
            return None
        if not r.ok and not j.get("error"):
            print(f"  [lens] failed: HTTP {r.status_code}")
            return None
        if j.get("error"):
            print(f"  [lens] {j['error']}")
            return None
        matches = [{"title": m.get("title", ""), "source": m.get("source", ""), "link": m.get("link", ""),
                    "price": _price(m)}
                   for m in (j.get("visual_matches") or [])[: self.cfg.get("max_matches", 40)]]
        kg = j.get("knowledge_graph") or []
        return {"image": image_url, "matches": matches,
                "best_guess": (kg[0].get("title") if kg and isinstance(kg[0], dict) else None)}


def hint_text(lens, n=12):
    """Lens match titles for the Claude prompt."""
    if not lens or not lens.get("matches"):
        return ""
    lines = [f"- {m['title']} ({m['source']})" + (f" ${m['price']:,.0f}" if m["price"] else "")
             for m in lens["matches"][:n] if m["title"]]
    head = f"Google Lens best guess: {lens['best_guess']}\n" if lens.get("best_guess") else ""
    return head + "Google Lens visual matches (other listings that look like this watch, may be wrong):\n" + "\n".join(lines)


# Too generic to tell one model from another.
GENERIC = {"watch", "watches", "mens", "men", "womens", "ladies", "automatic", "auto", "quartz", "manual", "wind",
           "vintage", "diver", "divers", "dive", "chronograph", "chrono", "date", "day", "gold", "steel",
           "stainless", "dress", "sport", "sports", "wrist", "wristwatch", "dial", "black", "blue", "white", "or"}


def _words(s):
    return [w for w in re.split(r"[^a-z0-9]+", (s or "").lower()) if len(w) > 1]


def _compact(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _ref_tokens(ref):
    """Reference-like tokens (BM7492-57A, SKX007K, 16610) from the AI's reference guess."""
    toks = re.findall(r"[a-z0-9][a-z0-9.\-/]{3,}[a-z0-9]", (ref or "").lower())
    return {c for c in (_compact(t) for t in toks) if len(c) >= 5 and re.search(r"\d", c)}


def _is_used_source(m, used_sources):
    blob = (m["source"] + " " + m["link"]).lower()
    return any(u in blob for u in used_sources)


def market(lens, ai, exclude_terms=(), used_sources=(), min_ref_matches=3):
    """Asking-price band from secondhand listings that Lens matched to the photo.

    Only secondhand marketplaces count (new retail prices say little about a used watch).
    Matches naming the exact reference are preferred; otherwise brand plus model words.
    """
    if not lens or not ai or not ai.get("brand"):
        return None
    if ai.get("is_lot"):
        # Lens matches one watch in the photo; that price says nothing about the whole lot.
        return {"count": 0, "skipped": "lot"}
    brand = _words(ai["brand"])
    ident = set(_words(ai.get("model"))) | set(_words(ai.get("reference_guess")))
    ident -= set(brand) | GENERIC
    refs = _ref_tokens(ai.get("reference_guess"))
    junk = [t.lower() for t in list(JUNK) + list(exclude_terms)]
    used = [u.lower() for u in used_sources]
    kept = []
    for m in lens["matches"]:
        t = " " + m["title"].lower() + " "
        if not m["price"] or not all(w in t for w in brand):
            continue
        if any(j in t for j in junk):
            continue
        if used and not _is_used_source(m, used):
            continue
        # With a model or reference, require at least one of its words so a Seiko 5 does not price an SKX.
        # Substring match so "skx007" also finds "skx007k2".
        if ident and not any(w in t for w in ident):
            continue
        kept.append(m)
    by_ref = [m for m in kept if refs and any(r in _compact(m["title"]) for r in refs)]
    level = "reference" if len(by_ref) >= min_ref_matches else "model"
    if level == "reference":
        kept = by_ref
    prices = sorted(m["price"] for m in kept)
    if not prices:
        return {"count": 0}
    # Drop outliers: anything outside 0.4x to 2.5x the median (new-old-stock, mislabeled lots, parts).
    med = statistics.median(prices)
    trimmed = [p for p in prices if 0.4 * med <= p <= 2.5 * med]
    if trimmed:
        prices = trimmed
    q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0], statistics.median(prices), prices[-1]]
    return {"count": len(prices), "match_level": level, "median": round(statistics.median(prices), 2),
            "p25": round(q[0], 2), "p75": round(q[2], 2), "low": prices[0], "high": prices[-1],
            "examples": [{"title": m["title"], "source": m["source"], "link": m["link"], "price": m["price"]}
                         for m in kept if m["price"] in prices][:6]}
