"""Backtest: would WatchHunt's prices and max bids have made money on auctions that already ended?

  python backtest.py                    # uses watchhunt.db and config.yaml in this folder
  python backtest.py --retail-ratio 1.2 # what if you can sell 20% above where auctions close?
  python backtest.py --no-write         # don't write calibration.json

How it works, in plain terms:
  1. Every finished auction that actually sold (1+ bids) is a test case: ShopGoodwill closes WatchHunt harvested
     plus watches that ended on the board.
  2. Cases are replayed in the order they ended. For each one, the model may only use comps that ended BEFORE it,
     exactly as the board would have seen them. It predicts where the auction will close (with a grade).
  3. Prediction error: how far the real close was from the prediction, by grade. Old model shown for comparison.
  4. Bidding simulation: the max bid the board would have shown. You "win" when your max bid is at or above the
     real close, and pay the real close plus tax and shipping. The watch is then valued with hindsight (comps
     from before AND after, itself excluded) to see whether the win made money. Hindsight is a proxy for what you
     could sell it for, which is the weak point: replace it with real sales as you log them.
  5. Quiet auctions: which listing features go with auctions that closed far below prediction.
  6. Writes calibration.json, which the board uses to show the chance of winning at your max bid.

Nothing here touches the network. Read the targets at the bottom of the report before trusting a number.
"""
import argparse
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

from closes import PriceIndex, describe, retail_rows, sold_rows
from heuristics import REF_NUMBER, detect_brand, normalize
from store import Store

ET = ZoneInfo("America/New_York")

TARGETS = {  # grade: (max median abs error, in-band low, in-band high, max abs bias, min cases)
    "A": (0.25, 0.40, 0.60, 0.10, 30),
    "B": (0.35, 0.35, 0.65, 0.15, 30),
}
BID_TARGETS = {"min_wins": 20, "min_median_margin": 0.15, "max_losing_share": 0.25, "max_curse_gap": 0.15}


def median(xs):
    return statistics.median(xs) if xs else None


def pct(x):
    return "n/a" if x is None else f"{100 * x:.0f}%"


def money(x):
    return "n/a" if x is None else f"${x:,.0f}"


# --- the old model (brand + calibre, else brand + movement), replayed with the same no-future rule ------------
def old_predict(row, past, brands):
    d = describe(row["title"], brands)
    if not d:
        return None
    brand, cal, move, ladies, gold = d
    if cal:
        m = [r["price"] for r in past if r["d"] and r["d"][0] == brand and r["d"][1] == cal and r["d"][3] == ladies
             and r["d"][4] == gold]
        if len(m) >= 3:
            return statistics.median(m)
    m = [r["price"] for r in past if r["d"] and r["d"][0] == brand and r["d"][2] == move and r["d"][3] == ladies
         and r["d"][4] == gold]
    return statistics.median(m) if len(m) >= 6 else None


# --- costs, mirroring economics.py --------------------------------------------------------------------------
def service_cost(k, econ):
    """Rough job cost before resale, everyday-brand column of economics.service.jobs."""
    jobs = (econ.get("service") or {}).get("jobs") or {}

    def c(name, default):
        return (jobs.get(name) or {}).get("cost", [default])[0]
    quartzish = k["move"] in ("quartz", "eco-drive", "kinetic")
    if k["cond"] == "parts":
        return c("quartz_movement", 40) if quartzish else c("full_service", 120)
    if k["cond"] == "unknown":
        return c("battery", 20) if k["move"] == "quartz" else 0 if quartzish else c("clean_regulate", 40)
    return 0


def channel(econ):
    ch = (econ.get("channels") or {}).get(econ.get("channel")) or {}
    return ch.get("sell_fee_pct", econ.get("sell_fee_pct", 15)) / 100, ch.get("ship_out", econ.get("ship_out", 0))


def max_bid(retail_low, k, econ):
    fee, ship_out = channel(econ)
    tax = econ.get("sales_tax_pct", 0) / 100
    target = econ.get("target_margin_pct", 30) / 100
    fixed = econ.get("ship_in", 13) * (1 + tax) + service_cost(k, econ)
    return max(((retail_low * (1 - fee) - ship_out) / (1 + target) - fixed) / (1 + tax), 0)


def all_in(price, k, econ):
    tax = econ.get("sales_tax_pct", 0) / 100
    return (price + econ.get("ship_in", 13)) * (1 + tax) + service_cost(k, econ)


def net_sale(value, econ):
    fee, ship_out = channel(econ)
    return value * (1 - fee) - ship_out


# --- listing features for the quiet-auction analysis ----------------------------------------------------------
def features(row, brands):
    t = row["title"] or ""
    k = row["k"]
    f = {}
    if row["ended"]:
        e = row["ended"].astimezone(ET)
        f["ends ET"] = "6am-3pm" if 6 <= e.hour < 15 else "3pm-8pm" if 15 <= e.hour < 20 else \
            "8pm-midnight" if e.hour >= 20 else "midnight-6am"
        f["day"] = "weekend" if e.weekday() >= 5 else "weekday"
    _, how = detect_brand(normalize(t), brands)
    f["brand spelling"] = "misspelled" if how in ("misspelled", "fuzzy") else "correct"
    f["model number in title"] = "yes" if REF_NUMBER.search(t) else "no"
    f["title length"] = "short (<40)" if len(t) < 40 else "long"
    f["condition words"] = k["cond"]
    f["case"] = k["material"]
    f["wearer"] = k["wearer"]
    f["movement named"] = "no" if k["move"] == "unknown" else "yes"
    return f


def grade_table(cases, grades):
    lines = ["| Grade | Cases | Median abs error | Inside p25-p75 | Bias (median actual/predicted - 1) |",
             "|---|---|---|---|---|"]
    for g in grades:
        cs = [c for c in cases if c["grade"] == g]
        if not cs:
            lines.append(f"| {g} | 0 | n/a | n/a | n/a |")
            continue
        err = median([abs(c["ratio"] - 1) for c in cs])
        inside = sum(1 for c in cs if c["p25"] <= c["actual"] <= c["p75"]) / len(cs)
        bias = median([c["ratio"] for c in cs]) - 1
        lines.append(f"| {g} | {len(cs)} | {pct(err)} | {pct(inside)} | {pct(bias)} |")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="watchhunt.db")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--retail-ratio", type=float, default=None,
                    help="resale price as a multiple of auction close (default: pricing.retail_ratio_default)")
    ap.add_argument("--min-history-days", type=int, default=14,
                    help="skip cases in the first N days of data, when there were too few comps to be fair")
    ap.add_argument("--out", default="backtest_report.md")
    ap.add_argument("--no-write", action="store_true")
    args = ap.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    brands, families, pricing, econ = cfg["brands"], cfg.get("families"), cfg.get("pricing") or {}, cfg["economics"]
    ratio = args.retail_ratio if args.retail_ratio is not None else pricing.get("retail_ratio_default", 1.0)

    store = Store(args.db)
    ended = [lst for lst, st, _ in store.tracked(("ended",))]
    rows = [r for r in sold_rows(ended, store.all_closes(), brands, families) if r["k"] and r["ended"]]
    rows.sort(key=lambda r: r["ended"])
    for r in rows:
        r["d"] = describe(r["title"], brands)
    if not rows:
        raise SystemExit("No sold auctions with an end time yet. Let a few scans harvest closes first.")

    index = PriceIndex(rows, pricing)
    neutral = PriceIndex(rows, {**pricing, "condition_factor": {}, "boxed_factor": 1.0})
    start = rows[0]["ended"]
    after_last = rows[-1]["ended"] + timedelta(days=1)  # hindsight: every comp, before and after
    cases, old_cases = [], []
    for i, r in enumerate(rows):
        if (r["ended"] - start).days < args.min_history_days:
            continue
        est = index.estimate(r["k"], as_of=r["ended"], exclude=r["key"])
        old = old_predict(r, rows[:i], brands)
        if old:
            old_cases.append({"actual": r["price"], "ratio": r["price"] / old})
        if not est or not est["median"]:
            continue
        hind = index.estimate(r["k"], as_of=after_last, exclude=r["key"])
        flat = neutral.estimate(r["k"], as_of=r["ended"], exclude=r["key"])
        cases.append({**r, "grade": est["grade"], "level": est["level"], "pred": est["median"],
                      "p25": est["p25"], "p75": est["p75"], "actual": r["price"], "ratio": r["price"] / est["median"],
                      "hind": hind["median"] if hind else None,
                      "flat_ratio": r["price"] / flat["median"] if flat and flat["median"] else None})

    eligible = sum(1 for r in rows if (r["ended"] - start).days >= args.min_history_days)
    out = [f"# WatchHunt backtest, {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC", "",
           f"Sold auctions with a usable key: {len(rows)} (first {rows[0]['ended']:%Y-%m-%d}, "
           f"last {rows[-1]['ended']:%Y-%m-%d}). Tested after a {args.min_history_days}-day warm-up: {eligible}. "
           f"Priced by the new model: {len(cases)} ({pct(len(cases) / eligible if eligible else None)}). "
           f"Priced by the old model: {len(old_cases)}.", "",
           "## 1. Where will the auction close? (wholesale)", ""]
    out += grade_table(cases, ["A", "B", "C", "D"])
    if old_cases:
        err = median([abs(c["ratio"] - 1) for c in old_cases])
        bias = median([c["ratio"] for c in old_cases]) - 1
        out += ["", f"Old model (brand + calibre, else brand + movement): {len(old_cases)} cases, median abs error "
                    f"{pct(err)}, bias {pct(bias)}."]
    by_level = defaultdict(list)
    for c in cases:
        by_level[c["level"]].append(abs(c["ratio"] - 1))
    out += ["", "By match level: " + ", ".join(f"{lv} {len(v)} cases / {pct(median(v))} error" for lv, v in by_level.items())]

    # Condition factors implied by the data (priced with no condition adjustment, compared within a level).
    implied = defaultdict(list)
    for c in cases:
        if c["flat_ratio"]:
            implied[c["k"]["cond"]].append(c["flat_ratio"])
    base = median(implied.get("unknown", []))
    if base:
        out += ["", "Condition factors your data implies (relative to an untested listing; config has "
                    f"{pricing.get('condition_factor')}): " +
                ", ".join(f"{cond} {median(v) / base:.2f} (n={len(v)})" for cond, v in sorted(implied.items()))]

    # --- 2. Bidding simulation -------------------------------------------------------------------------------
    out += ["", f"## 2. Bidding simulation (resale = {ratio:g} x auction-close comps)", "",
            "You bid the board's max bid on every priced watch. A win means your max bid was at or above the real "
            "close; you pay the close plus tax and shipping. Hindsight value uses comps from before and after.",
            "",
            "Read the win count as solid (real closes against the max bid the board would have shown). Read the "
            "profit as weak: hindsight value is built from nearly the same comps as the prediction, so it cannot see "
            "a watch that closed low for a real reason (worse condition, small size, swapped parts). Only your own "
            "logged sales can measure that."]
    for label, allowed in (("Grades A and B only (what the board lets set a value)", ("A", "B")),
                           ("Every grade", ("A", "B", "C", "D"))):
        wins = []
        for c in cases:
            if c["grade"] not in allowed:
                continue
            mb = max_bid(c["p25"] * ratio, c["k"], econ)
            if mb >= c["actual"] and c["hind"]:
                cost = all_in(c["actual"], c["k"], econ)
                pred_net = net_sale(c["pred"] * ratio, econ) - cost
                real_net = net_sale(c["hind"] * ratio, econ) - cost
                wins.append({"cost": cost, "pred_m": pred_net / cost, "real_m": real_net / cost, "net": real_net,
                             "title": c["title"], "paid": c["actual"], "pred": c["pred"], "hind": c["hind"]})
        n = sum(1 for c in cases if c["grade"] in allowed)
        out += ["", f"**{label}:** {n} auctions, {len(wins)} wins ({pct(len(wins) / n if n else None)})."]
        if wins:
            out += [f"- Median margin predicted at the time: {pct(median([w['pred_m'] for w in wins]))}; "
                    f"with hindsight: {pct(median([w['real_m'] for w in wins]))}. The gap is the winner's curse: you "
                    "win most often when your estimate is too high.",
                    f"- Wins that lose money with hindsight: {pct(sum(1 for w in wins if w['net'] < 0) / len(wins))}. "
                    f"Total hindsight profit: {money(sum(w['net'] for w in wins))} on {money(sum(w['cost'] for w in wins))} spent.",
                    "- Example wins (paid / predicted close / hindsight value):"]
            for w in sorted(wins, key=lambda w: -w["net"])[:6]:
                out.append(f"  - {money(w['paid'])} / {money(w['pred'])} / {money(w['hind'])}: {w['title'][:70]}")

    # --- 3. Quiet auctions --------------------------------------------------------------------------------------
    deal_cut = 0.6
    out += ["", f"## 3. What goes with quiet auctions (closed under {pct(deal_cut)} of prediction)", "",
            "Small samples. A feature matters only if its deal rate stays high as data grows. Overall deal rate: "
            f"{pct(sum(1 for c in cases if c['ratio'] <= deal_cut) / len(cases) if cases else None)}.", "",
            "| Feature | Value | Cases | Deal rate | Median close / predicted |", "|---|---|---|---|---|"]
    groups = defaultdict(list)
    for c in cases:
        for name, val in features(c, brands).items():
            groups[(name, val)].append(c["ratio"])
    for (name, val), rs in sorted(groups.items()):
        if len(rs) >= 5:
            out.append(f"| {name} | {val} | {len(rs)} | {pct(sum(1 for x in rs if x <= deal_cut) / len(rs))} | "
                       f"{median(rs):.2f} |")

    # --- 4. Retail data ---------------------------------------------------------------------------------------
    rrows = retail_rows(pricing, brands, families, fetch=True)
    out += ["", "## 4. Retail data", "",
            f"Retail comps loaded: {len(rrows)}. Until you log real sales, every profit number above rests on the "
            f"assumed resale ratio ({ratio:g})."]

    # --- 5. Targets ---------------------------------------------------------------------------------------------
    out += ["", "## 5. Targets before you trust a number on the board", "",
            "| Check | Target | Now | Pass |", "|---|---|---|---|"]
    for g, (max_err, lo, hi, max_bias, min_n) in TARGETS.items():
        cs = [c for c in cases if c["grade"] == g]
        err = median([abs(c["ratio"] - 1) for c in cs])
        inside = sum(1 for c in cs if c["p25"] <= c["actual"] <= c["p75"]) / len(cs) if cs else None
        bias = (median([c["ratio"] for c in cs]) - 1) if cs else None
        out += [f"| Grade {g} cases | at least {min_n} | {len(cs)} | {'yes' if len(cs) >= min_n else 'no'} |",
                f"| Grade {g} median abs error | at most {pct(max_err)} | {pct(err)} | "
                f"{'yes' if err is not None and err <= max_err else 'no'} |",
                f"| Grade {g} closes inside p25-p75 | {pct(lo)} to {pct(hi)} | {pct(inside)} | "
                f"{'yes' if inside is not None and lo <= inside <= hi else 'no'} |",
                f"| Grade {g} bias | within ±{pct(max_bias)} | {pct(bias)} | "
                f"{'yes' if bias is not None and abs(bias) <= max_bias else 'no'} |"]
    out += [f"| Simulated wins (A and B) | at least {BID_TARGETS['min_wins']} | see section 2 | |",
            f"| Hindsight median margin on wins | at least {pct(BID_TARGETS['min_median_margin'])} | see section 2 | |",
            f"| Wins losing money (meaningful only once real sales replace hindsight) | at most "
            f"{pct(BID_TARGETS['max_losing_share'])} | see section 2 | |",
            "| Your own logged sales | at least 10, median realized margin 20%+ | from your sheet | |"]

    report = "\n".join(out) + "\n"
    print(report)
    if not args.no_write:
        with open(args.out, "w") as f:
            f.write(report)
        calib = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "ratios": {g: sorted(round(c["ratio"], 3) for c in cases if c["grade"] == g) for g in "ABCD"}}
        with open("calibration.json", "w") as f:
            json.dump(calib, f)
        print(f"Wrote {args.out} and calibration.json")


if __name__ == "__main__":
    main()
