"""Turns a listing into board numbers.

Value basis, in order of trust:
  1. "sold"  : ShopGoodwill closed auctions, p25 to p75, when there are enough results
  2. "ai"    : the AI's rough range (a guess, shown as such)
  3. None    : no estimate, row shows cost only

Max bid is computed from the LOW end of the value range on purpose.
"""


def value_band(lst, econ):
    c = lst.comps or {}
    if c.get("count", 0) >= econ.get("min_comps", 5) and c.get("p25") is not None:
        return {"low": c["p25"], "mid": c["median"], "high": c["p75"], "basis": "sold",
                "basis_note": f'{c["count"]} ShopGoodwill sales, 90 days'}
    ai = lst.ai or {}
    rng = ai.get("rough_value_range_usd")
    if isinstance(rng, list) and len(rng) == 2 and all(isinstance(v, (int, float)) for v in rng) and rng[1] > 0:
        lo, hi = sorted(rng)
        return {"low": lo, "mid": (lo + hi) / 2, "high": hi, "basis": "ai", "basis_note": "AI guess, unverified"}
    return None


def numbers(lst, econ):
    prem = econ.get("buyer_premium_pct", {}).get(lst.source, 0) / 100
    movement = (lst.ai or {}).get("movement_type", "unknown") or "unknown"
    service = econ.get("service_cost", {}).get(movement, econ.get("service_cost", {}).get("unknown", 250))
    fixed = econ.get("ship_in", 0) + service
    all_in = lst.price * (1 + prem) + fixed

    band = value_band(lst, econ)
    out = {"all_in": round(all_in, 2), "service": service, "value": band,
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
