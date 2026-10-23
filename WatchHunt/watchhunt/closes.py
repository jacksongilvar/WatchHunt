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
from datetime import datetime, timezone

CAL = [
    re.compile(r"\bcal(?:ibre|iber)?\.?\s*([a-z]?\d{3,4}[a-z]?)\b", re.I),        # "Calibre 8700", "cal. 7S26"
    re.compile(r"\b([a-z]?\d[a-z0-9]\d{2})[-\s][a-z0-9]{3,4}\b", re.I),             # 6309-8019, 7S26-04V0, E870-S015294, 8F32-0069
    re.compile(r"\b(\d[a-z]\d{2}|[a-z]\d{3})\b", re.I),                              # bare 7S26, 8F32, E870, A904
    re.compile(r"\b(s[a-z]{2,3}\d{2,3})[a-z]?\d?\b", re.I),                               # Seiko model codes: SNX997, SKX007, SNKD99
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
GOLD = re.compile(r"gold[- ]?(tone|plated|filled|cap|capped)|two[- ]?tone|\bsgp\b|\bgp\b|\bgf\b|\bg\.?\s?filled|"
                  r"rolled gold|electroplat", re.I)
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


# --- Comparable key -------------------------------------------------------------------------------------------
# One listing title (plus the AI check, when there is one) becomes a key of fields that decide price.
# Comps are matched field by field, most specific first (see LEVELS), so a 1970s Seiko 5 is never priced from
# a modern Prospex diver unless nothing closer exists, and then the answer is marked as a weak match.

REF = [
    re.compile(r"\b(\d[a-z0-9]\d{2})-([a-z0-9]{4})\b", re.I),                    # Seiko caseback 6309-8019, 7S26-0020, 8F32-0069
    re.compile(r"\b(s[a-z]{2,3}\d{2,3}[a-z]?\d?)\b", re.I),                       # Seiko model codes SKX007, SNK809, SRPD55K1
    re.compile(r"\b([a-z]{2}\d{4})(?:-\d{2}[a-z])?\b", re.I),                     # Citizen BN0150-28E, AT4008, BL8000
    re.compile(r"\b(h\d{8})\b", re.I),                                             # Hamilton H70455133
    re.compile(r"\b(ya\d{6})\b", re.I),                                            # Gucci YA126201
]
SOLID_GOLD = re.compile(r"\b(9|10|14|18)\s?k(?:t|arat)?\b(?!.{0,12}(filled|plated|\bgp\b|rolled|electro))", re.I)
TITANIUM = re.compile(r"titanium", re.I)
PARTS = BROKEN
UNTESTED = re.compile(r"untested|not tested|needs? (a )?(new )?batter|dead batter|not running|as[- ]is|unknown", re.I)
WORKING = re.compile(r"\bworking\b|\bworks\b|\bruns\b|running|keeps (good )?time|\btested\b|new battery|serviced", re.I)
BOXED = re.compile(r"\biob\b|in (the )?(original )?box|w/ ?box|with box|box (and|&) papers|\bnwt\b|new with tags|\bbnib\b", re.I)


def reference(text):
    """Full reference or caseback code, lower case, or None."""
    for rx in REF:
        m = rx.search(text or "")
        if m:
            return m.group(0).lower()
    return None


def family(brand, text, families):
    """First configured model family whose words appear in the text (config.yaml families.<brand>)."""
    from heuristics import normalize, _contains
    norm = normalize(text)
    for entry in (families or {}).get(brand) or []:
        name, words = (entry[0], entry[1]) if isinstance(entry, list) else (entry, [entry])
        for w in map(str, words):
            # "skx*" matches any word starting with skx (SKX007, SKX009); plain words match whole words only.
            if (w.endswith("*") and re.search(rf"\b{re.escape(w[:-1].lower())}", norm)) or _contains(norm, w):
                return name
    return None


def condition(text):
    if PARTS.search(text or ""):
        return "parts"
    if WORKING.search(text or "") and not UNTESTED.search(text or ""):
        return "working"
    return "unknown"   # ShopGoodwill does not test watches, so most titles land here


def comp_key(title, brands, ai=None, description="", families=None):
    """Price-relevant fields for one watch, or None for lots, non-watches and fake-risk brands.

    Unlike describe(), watches sold for parts are kept and marked condition "parts", so they can be priced
    from other parts watches instead of being dropped."""
    ai = ai or {}
    text = " ".join(x for x in [title, ai.get("model") or "", ai.get("reference_guess") or ""] if x)
    if ai.get("is_lot") or LOT.search(title or "") or NOT_WATCH.search(title or ""):
        return None
    brand = _brand(text, brands, ai.get("brand"))
    if not brand or (brands or {}).get(brand, {}).get("fake_risk"):
        return None
    move = (ai.get("movement_type") or "").lower()
    if move in ("", "unknown"):
        move = next((name for name, rx in MOVE if rx.search(text)), "unknown")
    if re.search(r"eco[- ]?drive|solar", text, re.I):
        move = "eco-drive"
    from sizing import text_signals
    strong, _ = text_signals(title, description)
    ladies = (ai.get("wearer") or "").lower() == "ladies" or bool(strong)
    notes = " ".join([text, ai.get("condition_notes") or ""])
    material = "gold" if SOLID_GOLD.search(notes) else "goldtone" if GOLD.search(notes) else \
        "titanium" if TITANIUM.search(notes) else "steel"
    # Title and AI notes only: Goodwill descriptions carry boilerplate ("sold as is, may need repair").
    cond_text = " ".join([title or "", ai.get("condition_notes") or ""])
    return {
        "brand": brand,
        "ref": reference(ai.get("reference_guess")) or reference(title),
        "cal": calibre(ai.get("reference_guess")) or calibre(ai.get("model")) or calibre(title),
        "family": family(brand, text, families),
        "move": move,
        "wearer": "ladies" if ladies else "mens",
        "material": material,
        "cond": condition(cond_text),
        "boxed": bool(BOXED.search(title or "")),
    }


def key_label(k, level, fields=("material", "wearer")):
    bits = [k["brand"].title()]
    if level == "reference" and k.get("ref"):
        bits.append(k["ref"].upper())
    elif level == "calibre" and k.get("cal"):
        bits.append(k["cal"].upper())
    elif level == "family" and k.get("family"):
        bits += [k["family"], k["move"]]
    elif level == "movement":
        bits.append(k["move"])
    if "material" in fields and k["material"] != "steel":
        bits.append({"goldtone": "gold-tone", "gold": "solid gold"}.get(k["material"], k["material"]))
    if k["wearer"] == "ladies":
        bits.append("women's")
    return " ".join(bits)


def _label(brand, cal, move, ladies, gold):
    bits = [brand.title(), cal.upper() if cal else move]
    if gold:
        bits.append("gold-tone")
    if ladies:
        bits.append("women's")
    return " ".join(bits)


DEFAULT_PRICING = {
    "half_life_days": 90, "max_age_days": 365,
    "levels": [
        {"name": "reference", "fields": ["brand", "ref", "wearer", "material"], "min": 3},
        {"name": "calibre", "fields": ["brand", "cal", "wearer", "material"], "min": 3},
        {"name": "family", "fields": ["brand", "family", "move", "wearer", "material"], "min": 4},
        {"name": "movement", "fields": ["brand", "move", "wearer", "material"], "min": 6},
        {"name": "brand", "fields": ["brand", "wearer"], "min": 8},
    ],
    "condition_factor": {"working": 1.2, "unknown": 1.0, "parts": 0.55},
    "boxed_factor": 1.1,
    "grade_a": {"levels": ["reference", "calibre"], "min_count": 5, "max_spread": 0.6},
    "grade_b": {"levels": ["reference", "calibre", "family"], "min_count": 4},
    "grade_c": {"levels": ["movement"]},
}
OPTIONAL = ("ref", "cal", "family", "move")   # a level is skipped when the watch has no value for one of these


def when(ts):
    """ISO time string to an aware UTC datetime, or None."""
    if not ts:
        return None
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def wquantile(pairs, q):
    """Weighted quantile of (value, weight) pairs sorted by value."""
    total = sum(w for _, w in pairs)
    cum = 0.0
    for v, w in pairs:
        cum += w
        if cum >= q * total - 1e-9:
            return v
    return pairs[-1][0]


class PriceIndex:
    """Comparable sales, matched field by field and weighted by age.

    rows: dicts with key, title, price, ended (datetime or None) and k (a comp_key dict).
    estimate(k) tries each level in pricing.levels, most specific first, and uses the first one with enough comps.
    Every comp's price is moved to the target's condition and box status with the configured factors, then
    weighted by age (half-life). The result carries a grade (A to D) that says how far to trust it."""

    def __init__(self, rows, pricing=None):
        self.p = {**DEFAULT_PRICING, **(pricing or {})}
        self.rows = [r for r in rows if r.get("k")]

    def _factor(self, k):
        cf = self.p.get("condition_factor") or {}
        return cf.get(k.get("cond"), 1.0) * (self.p.get("boxed_factor", 1.0) if k.get("boxed") else 1.0)

    def _grade(self, level, n, spread):
        a, b, c = self.p.get("grade_a") or {}, self.p.get("grade_b") or {}, self.p.get("grade_c") or {}
        if level in a.get("levels", []) and n >= a.get("min_count", 5) and spread <= a.get("max_spread", 0.6):
            return "A"
        if level in b.get("levels", []) and n >= b.get("min_count", 4):
            return "B"
        if level in c.get("levels", []):
            return "C"
        return "D"

    def matches(self, k, level, as_of=None, exclude=None):
        """(row, age_days) for every comp that matches k at this level and ended before as_of."""
        now = as_of or datetime.now(timezone.utc)
        max_age = self.p.get("max_age_days", 365)
        fields = level["fields"]
        if any(f in OPTIONAL and k.get(f) in (None, "unknown") for f in fields):
            return []
        out = []
        for r in self.rows:
            if r["key"] == exclude:
                continue
            ended = r.get("ended")
            if ended and ended >= now:
                continue  # in a backtest, comps from the future are not allowed
            age = (now - ended).total_seconds() / 86400 if ended else max_age / 2
            if age > max_age:
                continue
            if all(r["k"].get(f) == k.get(f) for f in fields):
                out.append((r, age))
        return out

    def estimate(self, k, as_of=None, exclude=None):
        """{count, p25, median, p75, grade, level, match, ...} for a watch with key k, or None."""
        if not k:
            return None
        hl = self.p.get("half_life_days", 90)
        target = self._factor(k)
        for level in self.p.get("levels") or DEFAULT_PRICING["levels"]:
            m = self.matches(k, level, as_of, exclude)
            if len(m) < level.get("min", 3):
                continue
            pairs = sorted((r["price"] / self._factor(r["k"]) * target, 0.5 ** (age / hl)) for r, age in m)
            p25, med, p75 = (wquantile(pairs, q) for q in (0.25, 0.5, 0.75))
            w = [x for _, x in pairs]
            eff_n = sum(w) ** 2 / sum(x * x for x in w)
            spread = (p75 - p25) / med if med else 9
            grade = self._grade(level["name"], len(m), spread)
            newest = min(age for _, age in m)
            return {"count": len(m), "eff_n": round(eff_n, 1), "p25": round(p25, 2), "median": round(med, 2),
                    "p75": round(p75, 2), "spread": round(spread, 2), "grade": grade, "level": level["name"],
                    "match": key_label(k, level["name"], level["fields"]), "newest_days": round(newest),
                    "key": {f: k.get(f) for f in ("brand", "ref", "cal", "family", "move", "wearer", "material", "cond")},
                    "examples": [{"title": r["title"][:70], "price": r["price"], "days_ago": round(a)}
                                 for r, a in sorted(m, key=lambda x: x[1])[:5]]}
        return None


def sold_rows(ended_listings=(), harvested=(), brands=None, families=None, min_price=5):
    """Rows for a PriceIndex from finished auctions. Auctions that ended with 0 bids did not sell: their
    "price" is the starting price, so they are left out. Buy-now board rows are left out too."""
    rows, seen = [], set()
    for lst in ended_listings:
        if lst.key in seen or not lst.price or lst.price < min_price or lst.buying_format == "buy_now" \
                or not (lst.bids or 0) > 0:
            continue
        seen.add(lst.key)
        rows.append({"key": lst.key, "title": lst.title, "price": float(lst.price), "ended": when(lst.end_time),
                     "bids": lst.bids, "k": comp_key(lst.title, brands, lst.ai, lst.description, families)})
    for key, _src, title, price, bids, ended in harvested:
        if key in seen or not price or price < min_price or not (bids or 0) > 0:
            continue
        seen.add(key)
        rows.append({"key": key, "title": title, "price": float(price), "ended": when(ended), "bids": bids,
                     "k": comp_key(title, brands, families=families)})
    return rows


class Closes:
    """Index of finished-auction prices (wholesale): harvested closed searches plus board watches that ended."""

    def __init__(self, ended_listings=(), brands=None, harvested=(), min_price=5, families=None, pricing=None):
        self.brands, self.families = brands or {}, families or {}
        self.index = PriceIndex(sold_rows(ended_listings, harvested, self.brands, self.families, min_price), pricing)

    def key(self, lst):
        return comp_key(lst.title, self.brands, lst.ai, lst.description, self.families)

    def lookup(self, lst, *_args, **_kw):
        """Where auctions for watches like this one end. Grade A or B can set a value; C and D only show where
        auctions like it usually end."""
        return self.index.estimate(self.key(lst), exclude=lst.key)


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


# --- Retail comps: what watches like this one sell for when YOU sell them (or on fixed-price eBay) ------------
# Two inputs, both optional:
#   data/retail_comps.csv  rows you log by hand: date,title,price,channel,source,notes
#                          title = describe the watch the way a listing would ("Seiko 5 6309-8019 automatic steel")
#                          source = own_sale | terapeak | watchexchange | other
#   your WatchHunter Google Sheet (Flips tab) published as CSV: rows with a Sale price become retail comps.

def _money(v):
    try:
        return float(str(v).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _date(v):
    from datetime import datetime as dt
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"):
        try:
            return dt.strptime(str(v).strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def retail_rows(pricing, brands, families, fetch=True):
    import csv
    import io
    import os
    p = pricing or {}
    raw = []
    path = p.get("retail_csv")
    if path and os.path.exists(path):
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                raw.append((r.get("title"), r.get("price"), r.get("date"), r.get("channel"), r.get("source") or "csv"))
    url = p.get("retail_sheet_csv_url")
    if url and fetch:
        try:
            import requests
            text = requests.get(url, timeout=30).text
            for r in csv.DictReader(io.StringIO(text)):
                title = " ".join(x for x in [r.get("Watch"), r.get("Reference")] if x)
                raw.append((title, r.get("Sale price"), r.get("Date sold"), r.get("Sold on"), "own_sale"))
        except Exception as e:  # the board still builds without it
            print(f"  [retail] could not read the sheet: {e}")
    rows = []
    for i, (title, price, date, chan, src) in enumerate(raw):
        price = _money(price)
        if not title or not price or price <= 0:
            continue
        rows.append({"key": f"retail:{i}", "title": title, "price": price, "ended": _date(date),
                     "channel": (chan or "").strip().lower(), "source": src,
                     "k": comp_key(title, brands, families=families)})
    return rows


class Retail:
    """Retail price index plus the measured retail / wholesale ratio."""

    def __init__(self, rows, closes_index, pricing=None):
        self.p = {**DEFAULT_PRICING, **(pricing or {})}
        self.index = PriceIndex(rows, pricing)
        self.ratio, self.ratio_pairs = self._ratio(closes_index)

    def _ratio(self, closes_index):
        """Median of retail median / wholesale median over watches that have both, or the configured default."""
        default = self.p.get("retail_ratio_default", 1.0)
        if closes_index is None:
            return default, 0
        ratios, done = [], set()
        for r in self.index.rows:
            sig = tuple(sorted(r["k"].items()))
            if sig in done:
                continue
            done.add(sig)
            ret, whole = self.index.estimate(r["k"]), closes_index.estimate(r["k"])
            if ret and whole and ret["grade"] in ("A", "B") and whole["grade"] in ("A", "B") and whole["median"]:
                ratios.append(ret["median"] / whole["median"])
        if len(ratios) >= self.p.get("min_ratio_pairs", 5):
            return round(statistics.median(ratios), 2), len(ratios)
        return default, len(ratios)

    def estimate(self, k, wholesale=None):
        """Retail band: own comps when there are enough, else wholesale x ratio. None if neither."""
        own = self.index.estimate(k)
        if own and own["grade"] in ("A", "B"):
            return {**own, "basis": "retail comps"}
        if wholesale and wholesale.get("grade") in ("A", "B"):
            r = self.ratio
            note = (f"measured on {self.ratio_pairs} models" if self.ratio_pairs >= self.p.get("min_ratio_pairs", 5)
                    else "assumed, not measured: add your sales to retail_comps.csv")
            return {**wholesale, "p25": round(wholesale["p25"] * r, 2), "median": round(wholesale["median"] * r, 2),
                    "p75": round(wholesale["p75"] * r, 2), "basis": f"auction closes x {r:g} ({note})"}
        return None
