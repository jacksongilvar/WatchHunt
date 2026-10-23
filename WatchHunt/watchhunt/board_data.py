"""Board logic shared by the local dashboard (dashboard.py) and the GitHub run (ci.py)."""
import time
from datetime import datetime, timezone

import json
import os

from closes import Closes, Retail, competition, retail_rows
from economics import channel, numbers


def _ended_by_clock(lst):
    if not lst.end_time:
        return False
    try:
        end = datetime.fromisoformat(lst.end_time.replace("Z", "+00:00"))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        return end < datetime.now(timezone.utc)
    except ValueError:
        return False


def _name(ai):
    """Brand plus model, without repeating the brand when the model already starts with it ("Seiko Seiko 5")."""
    brand, model = (ai.get("brand") or "").strip(), (ai.get("model") or "").strip()
    if brand and model.lower().startswith(brand.lower()):
        return model
    return " ".join(x for x in [brand, model] if x)


def add_watchlist(store, cfg, sources):
    """Put hand-picked listings from config.yaml `watchlist` on the board. Returns how many were added."""
    tracked = {lst.key: (lst, status) for lst, status, _ in store.tracked(("active", "ended"))}
    added = 0
    for item in cfg.get("watchlist") or []:
        if isinstance(item, (int, str)):
            item = {"id": item}
        source = item.get("source", "shopgoodwill")
        key = f"{source}:{item['id']}"
        reasons = ["Added by hand"]
        if item.get("note"):
            reasons.append(str(item["note"]))
        if item.get("max_bid"):
            reasons.append(f"Your max bid: ${item['max_bid']}")
        ship = item.get("shipping")
        manual = {k: item[k] for k in ("value", "service", "basis", "max_bid", "wearer") if item.get(k) is not None} or None
        if key in tracked:  # already on the board: keep note, max bid and shipping in sync with config
            lst, status = tracked[key]
            if lst.reasons and lst.reasons[0] == "Added by hand" and (lst.reasons != reasons or lst.manual != manual or
                                                                      (ship is not None and lst.shipping != float(ship))):
                lst.reasons = reasons
                lst.manual = manual
                if ship is not None:
                    lst.shipping = float(ship)
                store.update_tracked(lst, status)
            continue
        src = sources.get(source)
        if not src or not hasattr(src, "get"):
            continue
        lst = src.get(item["id"])
        if not lst:
            continue
        lst.reasons = reasons
        lst.manual = manual
        if ship is not None:
            lst.shipping = float(ship)
        store.track(lst)
        added += 1
        time.sleep(cfg["search"].get("polite_delay_seconds", 2))
    store.commit()
    return added


def refresh_bids(store, cfg):
    """Re-check price and bids on every active watch on the board. Returns how many were checked."""
    from main import build_sources
    sources = {s.name: s for s in build_sources(cfg, {"ebay", "shopgoodwill", "propertyroom"}, False)}
    added = add_watchlist(store, cfg, sources)
    if added:
        print(f"Added {added} watchlist items to the board.")
    checked = 0
    for lst, status, _ in store.tracked(("active", "ended")):
        if lst.source == "shopgoodwill":
            from source_shopgoodwill import to_utc
            lst.end_time = to_utc(lst.end_time)
        if status == "ended" and (_ended_by_clock(lst) or not lst.end_time):
            continue  # genuinely over; earlier versions could mark live auctions ended by mistake
        src = sources.get(lst.source)
        if not src or not hasattr(src, "refresh"):
            continue
        try:
            alive = src.refresh(lst)
        except Exception as e:  # one bad listing should not stop the rest
            print(f"  refresh failed for {lst.key}: {e}")
            continue
        ended = (not alive) or _ended_by_clock(lst)
        store.update_tracked(lst, "ended" if ended else "active")
        checked += 1
        time.sleep(cfg["search"].get("polite_delay_seconds", 2))
    return checked


def build_board(store, cfg):
    econ = cfg["economics"]
    rows = []
    tracked = store.tracked(("active", "ended"))
    index = Closes([l for l, st, _ in tracked if st == "ended"], cfg["brands"], store.all_closes(),
                   families=cfg.get("families"), pricing=cfg.get("pricing"))
    retail = Retail(retail_rows(cfg.get("pricing"), cfg["brands"], cfg.get("families")), index.index, cfg.get("pricing"))
    calib = None
    if os.path.exists("calibration.json"):  # written by backtest.py
        with open("calibration.json") as f:
            calib = json.load(f)
    now = datetime.now(timezone.utc)
    for lst, status, updated in tracked:
        ai = lst.ai or {}
        cl = index.lookup(lst)
        rt = retail.estimate(index.key(lst), cl)
        e = numbers(lst, econ, cfg["brands"], cl, rt, calib)
        hours = None
        if lst.end_time:
            try:
                end = datetime.fromisoformat(lst.end_time.replace("Z", "+00:00"))
                hours = ((end if end.tzinfo else end.replace(tzinfo=timezone.utc)) - now).total_seconds() / 3600
            except ValueError:
                pass
        e["competition"] = competition(lst, e.get("watch_type"), hours, cfg.get("competition")) if status == "active" else None
        rows.append({
            "key": lst.key, "status": status, "updated": updated,
            "source": lst.source, "title": lst.title, "url": lst.url,
            "image": lst.image_urls[0] if lst.image_urls else None,
            "price": lst.price, "bids": lst.bids, "format": lst.buying_format,
            "end_time": lst.end_time, "time_left": lst.time_left,
            "name": _name(ai) or lst.brand_hint or "",
            "reference": ai.get("reference_guess"), "era": ai.get("era_guess"),
            "concern": ai.get("authenticity_concern") if ai else None,
            "flags": ai.get("visible_red_flags", []) if ai else [],
            "summary": ai.get("summary", "") if ai else "; ".join(lst.reasons),
            "econ": e,
            "market": lst.market,
            "lens_guess": (lst.lens or {}).get("best_guess"),
            "replicas": {"count": (lst.lens or {}).get("replica_count", 0), "share": (lst.lens or {}).get("replica_share", 0),
                         "examples": (lst.lens or {}).get("replica_examples", [])},
            "is_lot": bool(ai.get("is_lot")) if ai else False,
            "hand_picked": bool(lst.reasons and lst.reasons[0] == "Added by hand"),
            "your_max": (lst.manual or {}).get("max_bid"),
            "questions": ai.get("questions_for_seller", []) if ai else [],
            "history": store.history(lst.key),
        })
    return {
        "rows": rows,
        "assumptions": {"target_margin_pct": econ.get("target_margin_pct"), "ship_in": econ.get("ship_in"),
                        "channel": channel(econ)[0], "sell_fee_pct": round(channel(econ)[1] * 100),
                        "ship_out": channel(econ)[2]},
        "refresh_minutes": cfg.get("dashboard", {}).get("refresh_minutes", 10),
    }
