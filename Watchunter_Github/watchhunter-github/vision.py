"""Sends listing photos to Claude for identification and red-flag triage.

This is triage, not authentication. The prompt forbids the model from calling
anything authentic, because listing photos cannot establish that.
"""
import base64
import json
import re
import time

import anthropic
import requests

from models import Listing

SUPPORTED = {"image/jpeg", "image/png", "image/gif", "image/webp"}
MAX_BYTES = 4_500_000

SYSTEM = """You are an experienced pre-owned and vintage watch specialist helping a reseller triage online listings.
Look at the photos and listing text and return ONLY a JSON object, no prose, no code fences.

Rules:
- Never say or imply a watch is authentic. Photos cannot prove authenticity.
- If you see no red flags, use authenticity_concern "none_visible". That means nothing visible, not genuine.
- Be specific about red flags: dial printing and font, logo shape, date window and cyclops, hands, crown, bezel,
  case shape and finishing, lug proportions, caseback engravings, bracelet and clasp, movement if shown,
  mismatched parts from different eras or references (frankenwatch), redials, relumes, heavy polishing.
- If the listing is a lot of several watches, set is_lot true, put the most valuable watch in brand/model/
  reference_guess, list each watch briefly in lot_items, and give a value range for the whole lot.
- Keep every string short. The whole reply must be complete, valid JSON.
- If photos are too poor to judge, say so. Poor photos are a finding, not a reason to guess.
- Value ranges are rough guesses from general knowledge, not sold data. Keep them wide and honest.
  Value it as-is, in the condition shown, at typical pre-owned resale prices, not retail.
- Google Lens matches may be included. They are visually similar pages, often right about brand and model,
  sometimes wrong. Use them as evidence, check them against the photos, and say in the summary if you
  disagree with them. A match listing does not make this watch genuine.
- Value rough_value_range_usd as if the watch were genuine. Counterfeit risk is handled separately from
  authenticity_concern, so do not shrink the value range for it.

Rolex, Tudor and Cartier are the most counterfeited brands. For them, be strict and check every item visible:
- The price itself: a genuine one selling far below market from a seller who does not know watches is exactly
  what this tool hunts, but it is also the most common fake pattern. Weigh the photos, not the story.
- Stock or catalog-looking photos, photos lifted from other listings, or no caseback/clasp/side shots: say so.
- Rolex: cyclops magnifies the date about 2.5x and sits centred over it; crisp, even dial print with no bleed;
  coronet shape and proportions; rehaut engraving (ROLEX ROLEX ROLEX plus serial at 6) on most watches made
  after about 2007; crown with coronet and correct guard shape; solid end links and engraved clasp code on modern
  bracelets; plain caseback on almost all models (a display caseback or engraved caseback is a major red flag);
  smooth sweep of an automatic seconds hand (a quartz tick is a red flag on anything but an Oysterquartz);
  date wheel font; lume plot alignment; bezel insert font and alignment; "Swiss Made" / "Swiss" at 6.
- Tudor: era-correct logo (rose on older pieces, shield later); vintage Tudors used Rolex-signed cases and crowns
  ("Original Oyster Case by Rolex" caseback on many); snowflake hands only on certain references; clean dial print.
- Cartier: secret signature hidden in the VII or X numeral on many Tank/Santos/Must dials; blued steel sword hands;
  sapphire or spinel cabochon on the crown; caseback with model and serial numbers; Must de Cartier is vermeil
  (gold-plated silver) and marked 925; Santos screws aligned and evenly finished; Roman numerals crisp and even.
- When the photos do not show enough to check these, use "cannot_assess" and list exactly which shots to ask for.

JSON schema:
{
  "is_lot": boolean,
  "lot_items": [string],
  "brand": string or null,
  "model": string or null,
  "reference_guess": string or null,
  "era_guess": string or null,
  "movement_type": "automatic" | "manual" | "quartz" | "unknown",
  "identification_confidence": "low" | "medium" | "high",
  "authenticity_concern": "none_visible" | "some" | "high" | "cannot_assess",
  "authenticity_checks": [string],
  "visible_red_flags": [string],
  "condition_notes": string,
  "photo_quality": "poor" | "ok" | "good",
  "questions_for_seller": [string],
  "rough_value_range_usd": [number, number] or null,
  "worth_a_closer_look": boolean,
  "summary": string
}"""


def _sniff(data):
    """Some image hosts send a generic content type, so check the file's first bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _load_image(url, why=None, tries=3):
    """One image block for Claude, or None. Appends the reason for a failure to `why`."""
    r = None
    for attempt in range(tries):
        try:
            r = requests.get(url, timeout=20, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
                "Referer": url.split("/", 3)[0] + "//" + url.split("/", 3)[2] + "/"})
            if r.ok:
                break
            err = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            err = type(e).__name__
        r = None
        time.sleep(2 * (attempt + 1))
    if r is None:
        if why is not None:
            why.append(err)
        return None
    ctype = _sniff(r.content) or r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if ctype == "image/jpg":
        ctype = "image/jpeg"
    if ctype not in SUPPORTED or len(r.content) > MAX_BYTES:
        if why is not None:
            why.append(f"unsupported ({ctype or 'unknown type'}, {len(r.content) // 1000} KB)")
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": ctype,
                                          "data": base64.b64encode(r.content).decode()}}


def _parse_json(text):
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return None


class Vision:
    def __init__(self, api_key, model, max_images=4):
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model, self.max_images = model, max_images

    def analyze(self, listing: Listing, hints: str = "", max_images=None):
        why = []
        urls = listing.image_urls[: max_images or self.max_images]
        blocks = [b for b in (_load_image(u, why) for u in urls) if b]
        if not blocks:
            reason = ", ".join(sorted(set(why))) or "listing has no photos"
            print(f"    no usable images ({len(urls)} tried: {reason})")
            return {"summary": f"No usable images could be downloaded ({reason}).", "check_failed": True,
                    "authenticity_concern": "cannot_assess", "worth_a_closer_look": False, "photo_quality": "poor"}
        text = (
            f"Source: {listing.source}\n"
            f"Title: {listing.title}\n"
            f"Current price: {listing.price} {listing.currency} ({listing.buying_format}, bids: {listing.bids})\n"
            f"Description: {listing.description[:2000] or '(none)'}\n"
            f"Photos attached: {len(blocks)}"
        )
        if hints:
            text += "\n\n" + hints
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=3000,
                system=SYSTEM,
                messages=[{"role": "user", "content": blocks + [{"type": "text", "text": text}]}],
            )
        except anthropic.APIError as e:
            return {"summary": f"AI call failed: {e}", "check_failed": True, "authenticity_concern": "cannot_assess",
                    "worth_a_closer_look": False}
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        parsed = _parse_json(raw)
        return parsed or {"summary": "AI returned unparseable output.", "raw": raw[:500], "check_failed": True,
                          "authenticity_concern": "cannot_assess", "worth_a_closer_look": False}
