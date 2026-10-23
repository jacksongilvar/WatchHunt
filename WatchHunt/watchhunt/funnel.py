"""The buying funnel: every listing a scan sees, sorted into stages, with the reason it stopped where it did.

  1. Lead        every listing the searches return
  2. Qualified   passes your rules on the title and description alone (brand, size, no refurbished/parts/lots...)
  3. Priced      the AI checked the photos and the board has a value and a max bid for it
  4. Hot         priced, under your max bid, and ending soon
  5. Action      the hot ones that fit your open slots

Nothing here bids. It narrows the list and says why, so a wrong rule can be spotted in the Rejected view and fixed.
Rules live in config.yaml under `funnel`.
"""
import csv
import os
import re

from heuristics import _contains, normalize

DEFAULT_REJECT = {
    "refurbished": "refurbished (often a redial or parts swap)",
    "redial": "redial", "refinished": "refinished dial", "custom": "custom or modded", "mod": "custom or modded",
    "for parts": "sold for parts", "parts or repair": "sold for parts or repair", "for repair": "sold for repair",
    "not working": "not working", "non working": "not working", "broken": "broken",
    "kinetic": "Kinetic (capacitor usually dead, ~$60 to replace)",
}
LOT = re.compile(r"\blot\b|\b\d+\s*(pcs|pieces|watches)\b|\bassorted\b|\d+(\.\d+)?\s*(lbs?|pounds?)\b", re.I)
GOLD = re.compile(r"gold[- ]?(tone|plated|filled|cap|capped)|\bgp\b|\bsgp\b|electroplat", re.I)
TWO_TONE = re.compile(r"two[- ]?tone|2[- ]tone", re.I)
NOT_WATCH = re.compile(r"catalog|\bbook\b|empty box|box only|no watch|presentation box|\blinks only|\bdisplay\b", re.I)
NOT_RUNNING = re.compile(r"non[- ]?running|not running|doesn'?t run|does not run|stopped", re.I)
QUARTZ_WORDS = re.compile(r"quartz|perpetual|eco[- ]?drive|solar", re.I)
QUIET_QUARTZ = re.compile(r"untested|not tested|needs (a )?(new )?batter|as[- ]is", re.I)


def qualify(lst, cfg):
    """{"stage": "qualified" | "rejected", "lane": "core" | "luxury", "fit": int, "reasons": [..]}"""
    f = cfg.get("funnel") or {}
    title = lst.title or ""
    text = normalize(f"{title} {(lst.description or '')[:600]}")
    reasons, fit = [], 5

    if lst.score == -99:  # the existing exclude list (homage, fashion brands, perfume, clocks...)
        return {"stage": "rejected", "lane": None, "fit": 0, "reasons": list(lst.reasons or ["excluded term"])}

    brand = (lst.brand_hint or "").lower()
    core = [b.lower() for b in f.get("core_brands", ["seiko", "citizen", "hamilton", "gucci"])]
    luxury = [b for b, v in (cfg.get("brands") or {}).items() if v.get("fake_risk")]
    if brand in luxury:
        lane = "luxury"
    elif brand in core:
        lane = "core"
    else:
        return {"stage": "rejected", "lane": None, "fit": 0,
                "reasons": [f"brand not on your list ({brand})" if brand else "no target brand in the title"]}

    rejects = dict(DEFAULT_REJECT, **(f.get("reject_words") or {}))
    for word, why in rejects.items():
        if _contains(text, word):
            return {"stage": "rejected", "lane": lane, "fit": 0, "reasons": [why]}
    if NOT_WATCH.search(title):
        return {"stage": "rejected", "lane": lane, "fit": 0, "reasons": ["not a watch (box, book, display or parts)"]}
    if NOT_RUNNING.search(text) and not QUARTZ_WORDS.search(text):
        return {"stage": "rejected", "lane": lane, "fit": 0, "reasons": ["not running, and not a quartz watch a battery could fix"]}
    if LOT.search(title):
        return {"stage": "rejected", "lane": lane, "fit": 0, "reasons": ["multi-watch lot"]}

    from sizing import text_signals
    strong, weak = text_signals(title, lst.description or "")
    ladies_ok = [b.lower() for b in f.get("ladies_ok_brands", ["gucci"])]
    if strong and brand not in ladies_ok:
        return {"stage": "rejected", "lane": lane, "fit": 0, "reasons": ["women's: " + "; ".join(strong)]}
    if strong:
        fit -= 1
        reasons.append("women's (allowed for this brand)")
    elif weak:
        fit -= 1
        reasons.append("possibly small: " + "; ".join(weak))

    cap = f.get("max_price_luxury", 1500) if lane == "luxury" else f.get("max_price_core", 150)
    if lst.price and lst.price > cap:
        return {"stage": "rejected", "lane": lane, "fit": 0, "reasons": [f"price ${lst.price:.0f} over your ${cap} limit"]}

    gold_ok = brand in [b.lower() for b in f.get("gold_ok_brands", ["gucci"])]
    if GOLD.search(title) and gold_ok:
        reasons.append("gold-tone (normal for this brand)")
    elif GOLD.search(title):
        fit -= 2
        reasons.append("gold-tone (sells for about half of steel)")
    elif TWO_TONE.search(title):
        fit -= 1
        reasons.append("two-tone (plated parts)")
    if re.search(r"\bmen'?s\b|\bmens\b|\bgents?\b", title, re.I):
        fit += 1
        reasons.append("men's")
    if re.search(r"stainless|\bsteel\b|\bss\b", title, re.I):
        fit += 1
        reasons.append("stainless")
    if re.search(r"automatic|eco[- ]?drive|perpetual|chronograph|diver", title, re.I):
        fit += 1
        reasons.append("movement or model people search for")
    if QUIET_QUARTZ.search(text) and re.search(r"quartz|perpetual|eco[- ]?drive", text):
        fit += 1
        reasons.append("untested quartz (scares off bidders)")
    if any(str(r).startswith(("brand misspelled", "vague wording")) for r in (lst.reasons or [])):
        fit += 1
        reasons.append("hard-to-find title")
    if lst.bids is not None and lst.bids <= 1:
        fit += 1
        reasons.append(f"{lst.bids} bids so far")

    if fit < f.get("min_fit", 4):
        return {"stage": "rejected", "lane": lane, "fit": fit, "reasons": [f"fit {fit} below {f.get('min_fit', 4)}"] + reasons}
    return {"stage": "qualified", "lane": lane, "fit": fit, "reasons": reasons}


def hot(row, cfg):
    """A priced board row that is in the buy zone and ending soon."""
    f = cfg.get("funnel") or {}
    e = row.get("econ") or {}
    h = row.get("hours_left")
    return row.get("status") == "active" and e.get("zone") == "green" and h is not None and 0 <= h <= f.get("hot_hours", 30)


# --- miss log: your own finds, checked against what the funnel did with them ---

def read_miss_log(path="data/miss_log.csv"):
    if not os.path.exists(path):
        return []
    with open(path, newline="") as fh:
        return [r for r in csv.DictReader(fh) if (r.get("id") or r.get("title"))]


def miss_report(entries, leads, board_keys, zones):
    """For each logged find: how far it got in the funnel and, if it stopped, why."""
    out, caught, bids = [], 0, 0
    for e in entries:
        key = f"shopgoodwill:{e['id']}" if e.get("id") else None
        lead = leads.get(key) if key else None
        # Judge by what the funnel itself did. A watch on the board only because you added it by hand
        # does not count as caught.
        z = zones.get(key) if key else None
        if lead and lead["stage"] == "qualified":
            if key in board_keys:
                stage, why = "priced", f"qualified and on the board ({z or 'no value yet'})"
            else:
                stage, why = "qualified", "qualified but not AI-checked yet (budget or timing)"
        elif lead:
            stage, why = "rejected", "; ".join(lead["reasons"][:2]) + (" (on the board only because you added it)" if key in board_keys else "")
        elif key and key in board_keys:
            stage, why = "not seen", "on the board only because you added it; no funnel search returned it"
        else:
            stage, why = "not seen", "no search returned it (search gap)"
        ok = stage in ("priced", "qualified")
        if (e.get("verdict") or "bid").lower() == "bid":
            caught += ok
            bids += 1
        out.append({"date": e.get("date"), "id": e.get("id"), "title": e.get("title"), "verdict": e.get("verdict"),
                    "stage": stage, "why": why, "caught": ok, "notes": e.get("notes")})
    return {"entries": out, "caught": caught, "total": bids}
