"""Turns a listing into board numbers.

Value basis, in order of trust:
  1. "sold"   : ShopGoodwill closed auctions, p25 to p75, when there are enough results
  2. "market" : Google Lens matches with prices, p25 to p75, discounted because asking is not selling
  3. "ai"     : the AI's rough range (a guess, shown as such)
  4. None     : no estimate, row shows cost only

Max bid is computed from the LOW end of the value range on purpose.

Fake-risk brands (Rolex, Cartier, Tudor; brands.<name>.fake_risk in config) get two more adjustments:
  value  = chance genuine x genuine value + (1 - chance) x fake_value
  all-in = ... + authentication cost
The chance comes from the AI's authenticity_concern, cut further when Google Lens finds many replica listings.
"""


def value_band(lst, econ):
    ai = lst.ai or {}
    # Sold comps and Lens prices describe one watch, not a mixed lot, so lots use the AI's whole-lot range.
    c = {} if ai.get("is_lot") else (lst.comps or {})
    if c.get("count", 0) >= econ.get("min_comps", 5) and c.get("p25") is not None:
        return {"low": c["p25"], "mid": c["median"], "high": c["p75"], "basis": "sold",
                "basis_note": f'{c["count"]} ShopGoodwill sales, 90 days'}
    m = {} if ai.get("is_lot") else (lst.market or {})
    lens_cfg = econ.get("lens", {})
    if m.get("count", 0) >= lens_cfg.get("min_prices", 4) and m.get("p25") is not None:
        k = lens_cfg.get("asking_discount", 0.8)
        return {"low": round(m["p25"] * k, 2), "mid": round(m["median"] * k, 2), "high": round(m["p75"] * k, 2),
                "basis": "market",
                "basis_note": f'{m["count"]} Google Lens asking prices x {k:g} (asking is not sold)'}
    rng = ai.get("rough_value_range_usd")
    if isinstance(rng, list) and len(rng) == 2 and all(isinstance(v, (int, float)) for v in rng) and rng[1] > 0:
        lo, hi = sorted(rng)
        return {"low": lo, "mid": (lo + hi) / 2, "high": hi, "basis": "ai",
                "basis_note": "AI guess for the whole lot, unverified" if ai.get("is_lot") else "AI guess, unverified"}
    return None


def risky_brand(lst, brands):
    """The fake-risk brand this listing is (title brand or the AI's identification), or None."""
    ai = lst.ai or {}
    ai_brand = (ai.get("brand") or "").lower()
    # Once the AI has named a single watch's brand, trust it over the title (a Wittnauer "tank" is not a Cartier).
    names = [ai_brand] if ai_brand and not ai.get("is_lot") and not ai.get("check_failed") else [lst.brand_hint, ai_brand]
    for name, b in (brands or {}).items():
        if b.get("fake_risk") and any(n and (n == name or name in n) for n in names):
            return name
    return None


def genuine_chance(lst, econ):
    fr = econ.get("fake_risk", {})
    concern = (lst.ai or {}).get("authenticity_concern") or "cannot_assess"
    p = fr.get("genuine_chance", {}).get(concern, fr.get("genuine_chance", {}).get("cannot_assess", 0.3))
    lens = lst.lens or {}
    if lens.get("replica_share", 0) >= fr.get("replica_share_threshold", 0.3):
        p *= fr.get("replica_match_penalty", 0.5)
    return round(p, 3)


def numbers(lst, econ, brands=None):
    prem = econ.get("buyer_premium_pct", {}).get(lst.source, 0) / 100
    movement = (lst.ai or {}).get("movement_type", "unknown") or "unknown"
    service = econ.get("service_cost", {}).get(movement, econ.get("service_cost", {}).get("unknown", 250))
    ship_in = lst.shipping if lst.shipping is not None else econ.get("ship_in", 0)
    fr = econ.get("fake_risk", {})
    risky = risky_brand(lst, brands)
    auth = fr.get("authentication_cost", {}).get(risky, 0) if risky else 0
    fixed = ship_in + service + auth
    all_in = lst.price * (1 + prem) + fixed

    band = value_band(lst, econ)
    p = None
    if band and risky:
        p = genuine_chance(lst, econ)
        fake = fr.get("fake_value", 25)
        band = dict(band, genuine_low=band["low"], genuine_high=band["high"],
                    low=round(p * band["low"] + (1 - p) * fake, 2),
                    mid=round(p * band["mid"] + (1 - p) * fake, 2),
                    high=round(p * band["high"] + (1 - p) * fake, 2),
                    basis_note=f'{band["basis_note"]}, adjusted for a {round(100 * p)}% chance it is genuine')
    out = {"all_in": round(all_in, 2), "service": service, "value": band,
           "fake_risk": bool(risky), "genuine_chance": p, "auth_cost": auth,
           "ship_in": ship_in, "ship_in_basis": "listing" if lst.shipping is not None else "estimate",
           "net_mid": None, "margin_pct": None, "max_bid": None}
    if not band:
        return out

    fee = econ.get("sell_fee_pct", 15) / 100
    ship_out = econ.get("ship_out", 0)

    def net_at(v):
        return v * (1 - fee) - ship_out - all_in

    out["net_low"] = round(net_at(band["low"]), 2)
    out["net_mid"] = round(net_at(band["mid"]), 2)
    out["margin_pct"] = round(100 * out["net_mid"] / all_in, 1) if all_in else None

    target = econ.get("target_margin_pct", 30) / 100
    revenue_low = band["low"] * (1 - fee) - ship_out
    max_bid = (revenue_low / (1 + target) - fixed) / (1 + prem)
    out["max_bid"] = round(max(max_bid, 0), 2)
    return out
