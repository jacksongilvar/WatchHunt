"""Sends listing photos to Claude for identification and red-flag triage.

This is triage, not authentication. The prompt forbids the model from calling
anything authentic, because listing photos cannot establish that.
"""
import base64
import json
import re

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
- If photos are too poor to judge, say so. Poor photos are a finding, not a reason to guess.
- Value ranges are rough guesses from general knowledge, not sold data. Keep them wide and honest.

JSON schema:
{
  "brand": string or null,
  "model": string or null,
  "reference_guess": string or null,
  "era_guess": string or null,
  "movement_type": "automatic" | "manual" | "quartz" | "unknown",
  "identification_confidence": "low" | "medium" | "high",
  "authenticity_concern": "none_visible" | "some" | "high" | "cannot_assess",
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


def _load_image(url):
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
            "Referer": url.split("/", 3)[0] + "//" + url.split("/", 3)[2] + "/"})
        r.raise_for_status()
    except requests.RequestException:
        return None
    ctype = _sniff(r.content) or r.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if ctype == "image/jpg":
        ctype = "image/jpeg"
    if ctype not in SUPPORTED or len(r.content) > MAX_BYTES:
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

    def analyze(self, listing: Listing):
        blocks = [b for b in (_load_image(u) for u in listing.image_urls[: self.max_images]) if b]
        if not blocks:
            return {"summary": "No usable images could be downloaded.", "authenticity_concern": "cannot_assess",
                    "worth_a_closer_look": False, "photo_quality": "poor"}
        text = (
            f"Source: {listing.source}\n"
            f"Title: {listing.title}\n"
            f"Current price: {listing.price} {listing.currency} ({listing.buying_format}, bids: {listing.bids})\n"
            f"Description: {listing.description[:2000] or '(none)'}\n"
            f"Photos attached: {len(blocks)}"
        )
        try:
            msg = self.client.messages.create(
                model=self.model,
                max_tokens=1200,
                system=SYSTEM,
                messages=[{"role": "user", "content": blocks + [{"type": "text", "text": text}]}],
            )
        except anthropic.APIError as e:
            return {"summary": f"AI call failed: {e}", "authenticity_concern": "cannot_assess",
                    "worth_a_closer_look": False}
        raw = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        parsed = _parse_json(raw)
        return parsed or {"summary": "AI returned unparseable output.", "raw": raw[:500],
                          "authenticity_concern": "cannot_assess", "worth_a_closer_look": False}
