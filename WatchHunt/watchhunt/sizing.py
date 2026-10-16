"""Is this a women's watch? Women's and very small watches resell for far less than men's.

Two sources of evidence:
  1. The listing text: words like "ladies", women's movement or reference numbers, a short bracelet.
  2. The AI check, which judges size from the photos (crown and lugs against the case, band width).

Returns a verdict the board and the economics can use:
  "ladies"   strong evidence (the AI says so, or a women's word or movement number in the text)
  "possible" weak evidence only (short bracelet, small case size stated)
  None       nothing suggests it
"""
import re

from heuristics import normalize, _contains

WORDS = ["ladies", "lady", "womens", "women", "woman", "girls", "girl", "petite", "her watch", "for her"]
# "Mens & womens lot" style titles are not a women's watch; these cancel the word signal.
CANCEL = ["mens and womens", "men and women", "mens womens", "unisex", "his and hers", "his hers"]

# Movement and reference numbers that are women's sizes.
MOVEMENTS = [
    (re.compile(r"\b(?:1f20|1n0[0-9]|2205|2206|2306|2418|2419|2906|4205|4206|4207|4n2[0-9])\b", re.I), "Seiko women's movement"),
    (re.compile(r"\b7900\d\b"), "Hamilton 7900x reference (women's)"),
]

LENGTH = re.compile(r"(?<![\d.-])(\d(?:\.\d+)?)\s*(?:\"|”|in\b|inch|inches)", re.I)
CASE_MM = re.compile(r"(?<![\d.])(\d{2}(?:\.\d)?)\s*mm\b", re.I)


def text_signals(title, description=""):
    """(strong, weak) lists of reasons found in the listing text."""
    raw = f"{title} {(description or '')[:1500]}"
    norm = normalize(raw)
    strong, weak = [], []
    if not any(_contains(norm, c) for c in CANCEL):
        for w in WORDS:
            if _contains(norm, w):
                strong.append(f'Listing says "{w}"')
                break
    for rx, label in MOVEMENTS:
        m = rx.search(raw)
        if m:
            strong.append(f"{label}: {m.group(0)}")
    for m in LENGTH.finditer(raw):
        tail = raw[m.end():m.end() + 2]
        if tail.startswith("-") or raw[max(0, m.start() - 1):m.start()] == "-":
            continue  # a range like 5.75-8.25 is an adjustable bracelet, not a size
        v = float(m.group(1))
        if 4 <= v <= 6.5:
            weak.append(f'Bracelet or wrist length only {m.group(1)}"')
            break
    for m in CASE_MM.finditer(raw):
        v = float(m.group(1))
        if 15 <= v < 32:
            weak.append(f"Case {m.group(1)}mm")
            break
    return strong, weak


def size_verdict(lst):
    """{"flag": "ladies" | "possible" | None, "reasons": [...], "case_mm": number or None}."""
    ai = lst.ai or {}
    strong, weak = text_signals(lst.title, lst.description)
    reasons = []
    wearer = (ai.get("wearer") or "").lower()
    case_mm = ai.get("case_size_mm") if isinstance(ai.get("case_size_mm"), (int, float)) else None
    if wearer == "ladies":
        reasons.append("AI check: women's size" + (f" ({ai.get('size_evidence')})" if ai.get("size_evidence") else ""))
    if case_mm and case_mm < 32:
        weak.append(f"AI estimates case about {case_mm:g}mm")
    reasons += strong + weak
    if wearer == "ladies" or strong:
        flag = "ladies"
    elif wearer in ("mens", "unisex") and not weak:
        flag = None
    elif weak:
        flag = "possible"
    else:
        flag = None
    return {"flag": flag, "reasons": reasons, "case_mm": case_mm}
