"""Cheap text-only scoring. Decides which listings are worth an AI call.

Every point comes with a human-readable reason so the digest can show
why a listing was flagged.
"""
import re
from datetime import datetime, timezone
from rapidfuzz import fuzz

from models import Listing

REF_NUMBER = re.compile(r"\b(?:\d{3,6}(?:[./-]\d{1,4})?|[a-z]{1,3}\d{3,5}[a-z]?)\b", re.I)


def normalize(text: str) -> str:
    text = (text or "").lower().replace("'", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return f" {text.strip()} "


def _contains(norm_text: str, phrase: str) -> bool:
    return f" {normalize(phrase).strip()} " in norm_text


def detect_brand(norm_text: str, brands: dict):
    """Returns (brand, how) where how is exact | alias | misspelled | fuzzy, or (None, None)."""
    # Longer names first so "tag heuer" wins over "heuer" and "grand seiko" over "seiko".
    for name in sorted(brands, key=len, reverse=True):
        if _contains(norm_text, name):
            return name, "exact"
    for name, b in brands.items():
        for alias in b.get("also", []) or []:
            if _contains(norm_text, alias):
                return name, "alias"
        for typo in b.get("misspellings", []) or []:
            if _contains(norm_text, typo):
                return name, "misspelled"

    # Fuzzy fallback for typos we did not list. Conservative on purpose.
    tokens = norm_text.split()
    for name in brands:
        n_words = len(name.split())
        target = name.replace(" ", "")
        if len(target) < 5:
            continue
        for i in range(len(tokens)):
            chunk = "".join(tokens[i:i + n_words])
            if len(chunk) < 5 or chunk[0] != target[0] or chunk == target:
                continue
            if fuzz.ratio(chunk, target) >= 85:
                return name, "fuzzy"
    return None, None


def _hours_left(listing: Listing):
    if listing.end_time:
        try:
            end = datetime.fromisoformat(listing.end_time.replace("Z", "+00:00"))
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            return (end - datetime.now(timezone.utc)).total_seconds() / 3600
        except ValueError:
            pass
    if listing.time_left:
        d = re.search(r"(\d+)d", listing.time_left)
        h = re.search(r"(\d+)h", listing.time_left)
        m = re.search(r"(\d+)m", listing.time_left)
        return (int(d.group(1)) * 24 if d else 0) + (int(h.group(1)) if h else 0) + (int(m.group(1)) / 60 if m else 0)
    return None


def score_listing(listing: Listing, cfg: dict) -> Listing:
    text = normalize(f"{listing.title} {listing.description[:500]}")
    reasons = []
    score = 0

    for bad in cfg.get("exclude_terms", []):
        if _contains(text, bad):
            listing.score, listing.reasons = -99, [f"excluded ({bad})"]
            return listing

    brand, how = detect_brand(text, cfg["brands"])
    listing.brand_hint = brand
    b = cfg["brands"].get(brand, {}) if brand else {}
    if b.get("fake_risk"):
        words = cfg.get("scoring", {}).get("watch_words", []) + (b.get("also") or [])
        if not any(_contains(text, w) for w in words):
            listing.score, listing.reasons = -99, [f"{brand} but not a watch"]
            return listing
    if brand:
        tier = cfg["brands"][brand].get("tier", 1)
        score += tier
        reasons.append(f"brand: {brand} (tier {tier})")
        if how in ("misspelled", "fuzzy"):
            score += 3
            reasons.append("brand misspelled")
        if not REF_NUMBER.search(listing.title):
            score += 1
            reasons.append("no model or reference number")

    vague_hits = [p for p in cfg.get("vague_terms", {}) if _contains(text, p)]
    for p in vague_hits:
        score += cfg["vague_terms"][p]
    if vague_hits:
        reasons.append("vague wording: " + ", ".join(vague_hits))

    if _contains(text, "lot") or re.search(r"\b\d+\s+watches\b", text):
        score += 2
        reasons.append("multi-watch lot")
    if len(listing.title) < 35:
        score += 1
        reasons.append("very short title")
    letters = [c for c in listing.title if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.9:
        score += 1
        reasons.append("all caps title")
    if listing.image_urls and len(listing.image_urls) <= 2 and listing.source == "ebay":
        score += 1
        reasons.append("few photos")

    hours = _hours_left(listing)
    if listing.bids == 0 and hours is not None and hours < 24:
        score += 1
        reasons.append("auction ending soon with 0 bids")

    listing.score, listing.reasons = score, reasons
    return listing
