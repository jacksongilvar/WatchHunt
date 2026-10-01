"""Board logic shared by the local dashboard (dashboard.py) and the GitHub run (ci.py)."""
import time
from datetime import datetime, timezone

from economics import numbers


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


def refresh_bids(store, cfg):
    """Re-check price and bids on every active watch on the board. Returns how many were checked."""
    from main import build_sources
    sources = {s.name: s for s in build_sources(cfg, {"ebay", "shopgoodwill", "propertyroom"}, False)}
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
    for lst, status, updated in store.tracked(("active", "ended")):
        ai = lst.ai or {}
        rows.append({
            "key": lst.key, "status": status, "updated": updated,
            "source": lst.source, "title": lst.title, "url": lst.url,
            "image": lst.image_urls[0] if lst.image_urls else None,
            "price": lst.price, "bids": lst.bids, "format": lst.buying_format,
            "end_time": lst.end_time, "time_left": lst.time_left,
            "name": " ".join(x for x in [ai.get("brand"), ai.get("model")] if x) or lst.brand_hint or "",
            "reference": ai.get("reference_guess"), "era": ai.get("era_guess"),
            "concern": ai.get("authenticity_concern") if ai else None,
            "flags": ai.get("visible_red_flags", []) if ai else [],
            "summary": ai.get("summary", "") if ai else "; ".join(lst.reasons),
            "econ": numbers(lst, econ, cfg["brands"]),
            "market": lst.market,
            "lens_guess": (lst.lens or {}).get("best_guess"),
            "replicas": {"count": (lst.lens or {}).get("replica_count", 0), "share": (lst.lens or {}).get("replica_share", 0),
                         "examples": (lst.lens or {}).get("replica_examples", [])},
            "checks": ai.get("authenticity_checks", []) if ai else [],
            "questions": ai.get("questions_for_seller", []) if ai else [],
            "history": store.history(lst.key),
        })
    return {
        "rows": rows,
        "assumptions": {k: econ[k] for k in ("target_margin_pct", "sell_fee_pct", "ship_in", "ship_out")},
        "refresh_minutes": cfg.get("dashboard", {}).get("refresh_minutes", 10),
    }
