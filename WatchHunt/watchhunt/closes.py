"""What watches like this one actually sold for, from WatchHunt's own record of ended auctions.

Every watch that goes on the board and then ends leaves a final price behind. Grouped by brand and movement
(calibre), those finals are real closing prices: what the market paid, not what an AI guessed. They answer two
questions:
  - Value: what this kind of watch sells for (used as the value basis once there are enough of them)
  - Expected close: where the current auction will probably end, so a low price with days left is not mistaken
    for a bargain

Lots, Rolex/Cartier-type fake-risk brands, and listings under $5 are left out, because their finals say
nothing about one genuine watch.
"""
import re
import statistics

CAL = [
    re.compile(r"\bcal(?:ibre|iber)?\.?\s*([a-z]?\d{3,4}[a-z]?)\b", re.I),        # "Calibre 8700", "cal. 7S26"
    re.compile(r"\b([a-z]?\d[a-z0-9]\d{2})[-\s][a-z0-9]{3,4}\b", re.I),             # 6309-8019, 7S26-04V0, E870-S015294, 8F32-0069
    re.compile(r"\b(\d[a-z]\d{2}|[a-z]\d{3})\b", re.I),                              # bare 7S26, 8F32, E870, A904
    re.compile(r"\b(s[a-z]{2,3}\d{3}[a-z]?)\d?\b", re.I),                                 # Seiko model codes: SNX997, SKX007, SNKD99
]
ALIASES = {"8700": "e870", "e870": "e870"}


def calibre(text):
    for rx in CAL:
        m = rx.search(text or "")
        if m:
            c = m.group(1).lower()
            return ALIASES.get(c, c)
    return None


LOT = re.compile(r"\blot\b|\bpcs\b|\bpieces\b|assorted|\bbundle\b|\bwatches\b|\d+\s*(lbs?|pounds?)\b|\d+(\.\d+)?\s*g\b(?=.*lot)", re.I)
BROKEN = re.compile(r"for (parts|repair)|parts or repair|not working|non[- ]?working|broken|as[- ]is for parts|\brepair\b", re.I)
GOLD = re.compile(r"gold[- ]?(tone|plated|filled|cap|capped)|two[- ]?tone|\bsgp\b|\bgp\b|electroplat", re.I)
NOT_WATCH = re.compile(r"\bbox only|empty box|catalog|book|\bband only|strap only|links only|\bpouch\b", re.I)
MOVE = [("eco-drive", re.compile(r"eco[- ]?drive|solar", re.I)), ("kinetic", re.compile(r"kinetic", re.I)),
        ("automatic", re.compile(r"automatic|self[- ]?winding|\bauto\b", re.I)), ("quartz", re.compile(r"quartz", re.I)),
        ("manual", re.compile(r"manual|hand[- ]?wind|mechanical", re.I))]


def _brand(text, brands, ai_brand=""):
    from heuristics import detect_brand, normalize
    b = (ai_brand or "").lower().strip()
    for name in (brands or {}):
        if b and name in b:
            return name
    found, _ = detect_brand(normalize(text), brands or {})
    if found:
        return found
    for extra in ("citizen", "pulsar", "orient", "casio", "timex", "invicta", "fossil", "gucci", "movado", "tissot", "nixon"):
        if re.search(rf"\b{extra}\b", text, re.I):
            return extra
    return b.split()[0] if b else None


def describe(title, brands, ai=None, description=""):
    """(brand, calibre, movement, ladies, gold) for a listing or a harvested title, or None if it can't be priced."""
    ai = ai or {}
    text = " ".join(x for x in [title, ai.get("model") or "", ai.get("reference_guess") or ""] if x)
    if ai.get("is_lot") or LOT.search(title or "") or NOT_WATCH.search(title or "") or BROKEN.search(title or ""):
        return None
    brand = _brand(text, brands, ai.get("brand"))
    if not brand or (brands or {}).get(brand, {}).get("fake_risk"):
        return None
    cal = calibre(ai.get("reference_guess")) or calibre(ai.get("model")) or calibre(title)
    move = (ai.get("movement_type") or "").lower()
    if move in ("", "unknown"):
        move = next((name for name, rx in MOVE if rx.search(text)), "unknown")
    if re.search(r"eco[- ]?drive|solar", text, re.I):
        move = "eco-drive"
    from sizing import text_signals
    strong, _ = text_signals(title, description)
    ladies = (ai.get("wearer") or "").lower() == "ladies" or bool(strong)
    gold = bool(GOLD.search(" ".join([text, ai.get("condition_notes") or ""])))
    return brand, cal, move, ladies, gold


def _label(brand, cal, move, ladies, gold):
    bits = [brand.title(), cal.upper() if cal else move]
    if gold:
        bits.append("gold-tone")
    if ladies:
        bits.append("women's")
    return " ".join(bits)


class Closes:
    """Index of finished-auction prices: harvested closed searches plus board watches that ended."""

    def __init__(self, ended_listings=(), brands=None, harvested=(), min_price=5):
        self.brands = brands or {}
        self.exact, self.group = {}, {}
        rows = []
        for lst in ended_listings:
            if lst.price and lst.price >= min_price and lst.buying_format != "buy_now":
                rows.append((lst.key, lst.title, float(lst.price), describe(lst.title, brands, lst.ai, lst.description)))
        for key, _src, title, price, _bids, _ended in harvested:
            if price and price >= min_price:
                rows.append((key, title, float(price), describe(title, brands)))
        seen = set()
        for key, title, price, d in rows:
            if not d or key in seen:
                continue
            seen.add(key)
            brand, cal, move, ladies, gold = d
            if cal:
                self.exact.setdefault((brand, cal, ladies, gold), []).append((key, price, title))
            self.group.setdefault((brand, move, ladies, gold), []).append((key, price, title))

    @staticmethod
    def _stats(rows, label, level):
        prices = sorted(r[1] for r in rows)
        q = statistics.quantiles(prices, n=4) if len(prices) >= 4 else [prices[0], statistics.median(prices), prices[-1]]
        return {"count": len(prices), "p25": round(q[0], 2), "median": round(statistics.median(prices), 2),
                "p75": round(q[-1], 2), "match": label, "level": level,
                "examples": [{"title": t[:70], "price": p} for _, p, t in sorted(rows, key=lambda r: -r[1])[:5]]}

    def lookup(self, lst, brands=None, min_count=3, min_group=6):
        """Closing prices for watches like this one. level "exact" (same brand and calibre) can set the value;
        level "group" (same brand and movement type) only shows where auctions like it usually end."""
        d = describe(lst.title, brands or self.brands, lst.ai, lst.description)
        if not d:
            return None
        brand, cal, move, ladies, gold = d
        if cal:
            rows = [r for r in self.exact.get((brand, cal, ladies, gold), []) if r[0] != lst.key]
            if len(rows) >= min_count:
                return self._stats(rows, _label(brand, cal, move, ladies, gold), "exact")
        rows = [r for r in self.group.get((brand, move, ladies, gold), []) if r[0] != lst.key]
        if len(rows) >= min_group:
            return self._stats(rows, _label(brand, None, move, ladies, gold), "group")
        return None


QUARTZ_WORDS = re.compile(r"untested|not tested|needs (a )?(new )?batter|dead batter|not running|as[- ]is", re.I)


def competition(lst, wtype, hours_left, cfg):
    """Signs that few people are bidding on this listing. Returns {"quiet": bool, "reasons": [...]}."""
    c = cfg or {}
    reasons = []
    bids = lst.bids or 0
    if lst.buying_format != "buy_now" and hours_left is not None and 0 <= hours_left <= c.get("late_hours", 6) \
            and bids <= c.get("max_bids_late", 2):
        reasons.append(f"Only {bids} bid{'s' if bids != 1 else ''} with {hours_left:.0f}h left")
    if any(r == "brand misspelled" for r in (lst.reasons or [])):
        reasons.append("Brand misspelled in the title, so searches miss it")
    if any(str(r).startswith("no model or reference") for r in (lst.reasons or [])) or \
            any(str(r).startswith("vague wording") for r in (lst.reasons or [])):
        reasons.append("Vague title with no model number")
    text = f"{lst.title} {(lst.description or '')[:800]}"
    if wtype in ("quartz", "perpetual", "solar", "chrono_quartz") and QUARTZ_WORDS.search(text):
        reasons.append("Untested quartz: most bidders assume it's broken")
    if lst.end_time and lst.source == "shopgoodwill":
        try:
            from datetime import datetime, timedelta
            end = datetime.fromisoformat(lst.end_time.replace("Z", "+00:00"))
            et_hour = (end - timedelta(hours=c.get("utc_offset_hours", 4))).hour
            lo, hi = c.get("quiet_hours_et", [6, 15])
            if lo <= et_hour < hi:
                reasons.append(f"Ends at {et_hour % 12 or 12}{'am' if et_hour < 12 else 'pm'} ET, when fewer people are bidding")
        except ValueError:
            pass
    late = any(r.startswith("Only ") for r in reasons)
    return {"quiet": late or len(reasons) >= c.get("min_signals", 2), "reasons": reasons}
