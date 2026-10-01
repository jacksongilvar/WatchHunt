"""Watch Hunter v1: eBay + ShopGoodwill + PropertyRoom, text scoring, AI photo triage, daily digest.

Usage:
  python main.py                         # full run
  python main.py --no-ai                 # scoring only, no AI or Google Lens spend
  python main.py --sources ebay,propertyroom
  python main.py --include-seen          # re-show listings from earlier runs
  python main.py --debug                 # dump raw ShopGoodwill JSON for troubleshooting
"""
import argparse
import os
import sys

import yaml
from dotenv import load_dotenv

import digest
from heuristics import score_listing
from lens import Lens, hint_text, market
from store import Store


def build_sources(cfg, wanted, debug):
    sources = []
    s = cfg["search"]
    if "ebay" in wanted and cfg["ebay"].get("enabled"):
        cid, secret = os.getenv("EBAY_CLIENT_ID"), os.getenv("EBAY_CLIENT_SECRET")
        if cid and secret:
            from source_ebay import EbaySource
            sources.append(EbaySource(cid, secret, cfg["ebay"], s))
        else:
            print("! eBay skipped: set EBAY_CLIENT_ID and EBAY_CLIENT_SECRET in .env")
    if "shopgoodwill" in wanted and cfg["shopgoodwill"].get("enabled"):
        from source_shopgoodwill import ShopGoodwillSource
        sources.append(ShopGoodwillSource(cfg["shopgoodwill"], s, debug))
    if "propertyroom" in wanted and cfg["propertyroom"].get("enabled"):
        from source_propertyroom import PropertyRoomSource
        sources.append(PropertyRoomSource(cfg["propertyroom"], s))
    return sources


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--sources", default="ebay,shopgoodwill,propertyroom")
    ap.add_argument("--no-ai", action="store_true")
    ap.add_argument("--include-seen", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    load_dotenv()
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
    s = cfg["search"]
    store = Store()
    sources = build_sources(cfg, set(args.sources.split(",")), args.debug)
    if not sources:
        sys.exit("No sources enabled.")

    # 1. Fetch and score
    by_source = {}
    candidates = []
    stats = {}
    for src in sources:
        print(f"Fetching {src.name}...")
        n_total = n_new = 0
        for lst in src.fetch():
            n_total += 1
            if not (s["min_price"] <= lst.price <= s["max_price"]):
                continue
            if not args.include_seen and store.is_seen(lst.key):
                continue
            n_new += 1
            score_listing(lst, cfg)
            store.save(lst)  # mark seen even if low score, so it is not re-scored daily
            if lst.score >= cfg["scoring"]["min_score_for_digest"]:
                candidates.append(lst)
                by_source[lst.key] = src
        stats[src.name] = f"{n_total} fetched, {n_new} new"
        print(f"  {n_total} fetched, {n_new} new")
    store.commit()
    candidates.sort(key=lambda l: -l.score)
    stats["flagged"] = len(candidates)

    # 2. Enrich and AI-check the best candidates
    ai_checked, text_only = [], []
    use_ai = cfg["ai"].get("enabled") and not args.no_ai
    vision = None
    if use_ai:
        key = os.getenv("ANTHROPIC_API_KEY")
        if key:
            from vision import Vision
            vision = Vision(key, cfg["ai"]["model"], cfg["ai"].get("max_images_per_listing", 4))
        else:
            print("! AI skipped: set ANTHROPIC_API_KEY in .env")

    lens = None
    lens_cfg = cfg.get("lens", {})
    if use_ai and lens_cfg.get("enabled"):
        if os.getenv("SERPAPI_API_KEY"):
            lens = Lens(os.getenv("SERPAPI_API_KEY"), lens_cfg)
        else:
            print("! Google Lens skipped: set SERPAPI_API_KEY in .env")
    lens_budget = lens_cfg.get("max_per_run", 25)

    budget = cfg["ai"].get("max_listings_per_run", 25)
    sgw = next((x for x in sources if x.name == "shopgoodwill"), None)
    for lst in candidates:
        if vision and budget > 0 and lst.score >= cfg["scoring"]["min_score_for_ai"]:
            print(f"  AI: {lst.title[:70]}")
            by_source[lst.key].enrich(lst)
            hints = ""
            if lens and lens_budget > 0 and lst.image_urls:
                lst.lens = lens.search(lst.image_urls[0])
                lens_budget -= 1
                hints = hint_text(lst.lens)
            lst.ai = vision.analyze(lst, hints)
            budget -= 1
            if lst.lens:
                lst.market = market(lst.lens, lst.ai, cfg.get("exclude_terms", []),
                                    lens_cfg.get("used_sources", []), lens_cfg.get("min_reference_matches", 3))
            if cfg["comps"].get("shopgoodwill_sold") and sgw and (lst.ai or {}).get("brand"):
                q = " ".join(x for x in [lst.ai.get("brand"), lst.ai.get("model")] if x)
                lst.comps = sgw.sold_comps(q)
            store.save(lst)
            store.track(lst)  # AI-checked watches go on the live board
            ai_checked.append(lst)
        else:
            if lst.score >= cfg.get("dashboard", {}).get("track_min_score", 99):
                store.track(lst)
            text_only.append(lst)
    store.commit()
    stats["AI checked"] = len(ai_checked)

    # 3. Digest
    doc = digest.build(ai_checked, text_only, stats)
    path = digest.write(doc)
    print(f"Digest: {os.path.abspath(path)}")
    worth = sum(1 for l in ai_checked if (l.ai or {}).get("worth_a_closer_look"))
    try:
        if digest.email(doc, f"Watch Hunter: {worth} worth a look, {len(candidates)} flagged"):
            print("Emailed digest.")
    except Exception as e:
        print(f"! Email failed: {e}")


if __name__ == "__main__":
    main()
