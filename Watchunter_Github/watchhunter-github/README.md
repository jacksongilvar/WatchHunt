# Watch Hunter v1

## Running on GitHub (no computer needed)

GitHub runs the tool on a schedule and publishes the board as a web page:
- Full scan for new listings every 3 hours.
- Bid refresh on everything on the board every hour.
- Board page at https://YOUR-USERNAME.github.io/watchhunter/

The workflow lives in `.github/workflows/watchhunter.yml` (a copy is in `workflow-copy.yml` because Finder hides folders starting with a dot).
Keys go in Settings > Secrets and variables > Actions, never in files. Add `SERPAPI_API_KEY` there for Google Lens.
Saved state (what has been seen, price history) lives on a branch called `state` that the workflow overwrites each run.
To run it right away: Actions tab > watchhunter > Run workflow.

Running locally still works exactly as below.

Finds poorly written watch listings on eBay, ShopGoodwill and PropertyRoom, scores them by text, sends the best ones through Claude for photo triage and writes a daily HTML digest.

AI output is triage, not authentication. Nothing in the digest means a watch is real.

## Setup (Mac)

1. `cd watchhunter`
2. `python3 -m venv .venv && source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `cp .env.example .env` and fill it in:
   - **eBay:** create a developer account at developer.ebay.com, create a Production keyset and copy the App ID (client ID) and Cert ID (client secret).
   - **Anthropic:** create an API key at console.anthropic.com.
   - **Google Lens (SerpApi):** create an account at serpapi.com and copy your API key into `SERPAPI_API_KEY`. Google has no public Lens API, so SerpApi runs the Lens search. One search per AI-checked listing.
   - **Email (optional):** for Gmail, turn on 2-step verification and create an App Password.
5. Test without spending on AI: `python main.py --no-ai`
6. Full run: `python main.py`

The digest lands in `digests/`. Open it in a browser.

## Run it daily

`crontab -e` and add (runs 8am daily; adjust the path):

    0 8 * * * cd /Users/YOU/watchhunter && .venv/bin/python main.py >> run.log 2>&1

Your Mac has to be awake. For reliability, move it to a small cloud server later.

## Live board

    python dashboard.py

Open http://localhost:8000. Every AI-checked watch from `main.py` lands on the board. The board re-checks current bids every 10 minutes and refreshes itself every 30 seconds.

Each row shows:
- **Value:** in order of trust:
  1. ShopGoodwill sold data when there are at least 5 sales (solid band).
  2. Google Lens market: listings that Lens matched to the photo, filtered to the same brand and model, with prices. The p25 to p75 asking range is multiplied by `economics.lens.asking_discount` (default 0.8) because asking prices run above sale prices (dotted band).
  3. The AI's guess (dashed band).
  The board labels which one it used. Lens matches also go into the Claude prompt, so identification is better even when there are no prices.
- **Cost vs value gauge:** the brass band is the value range. The needle is your all-in cost: price plus inbound shipping (the listing's stated shipping on eBay when it has one, otherwise `ship_in`) plus a service estimate. A green needle clears your target margin, brass is profitable but thin, and red loses money at the low estimate.
- **Net at mid:** profit if it sells at the middle of the value range, after selling fees and shipping out.
- **Max bid:** the highest price that still clears your target margin at the LOW value estimate. "Pass" means no price does.

To open it from your phone on the same wifi, set `dashboard.host` to `0.0.0.0` in config.yaml and visit your Mac's local IP on port 8000.

Every number depends on the `economics` section of config.yaml. Check the fees and service costs before you trust any of it.

Ended auctions stay under the Ended filter with their final price. That history becomes your own sold-price data over time.

## Tuning

Everything lives in `config.yaml`:
- `queries` for eBay and ShopGoodwill. Add the misspellings you see in the wild.
- `exclude_terms` for fashion brands and junk you keep seeing.
- `brands` tiers and misspellings.
- `min_score_for_ai` and `max_listings_per_run` control AI spend.

Listings are only shown once. Use `--include-seen` to re-show old ones.

## Known fragile parts

- **ShopGoodwill** uses an unofficial endpoint. If it breaks, run `python main.py --sources shopgoodwill --debug`, then compare against the request your browser sends (Network tab) and update `_body()` in `source_shopgoodwill.py`.
- **PropertyRoom** is parsed from HTML. If the site changes layout, the regex in `source_propertyroom.py` needs updating. It respects robots.txt and skips pages it disallows.
- **ShopGoodwill sold comps** are a loose keyword match. Treat them as a starting point.
- AI value ranges are guesses. Always check eBay sold listings (linked on every card).
- **Google Lens market** prices are asking prices from whatever pages Lens matched, filtered by keyword. They can include different references, dial variants or condition. The examples are listed under each row so you can check them. Tune `asking_discount` once you have your own sold history.

## Not in v1

Estate sale email parsing, transit filter, Yahoo Auctions Japan, Mercari and the city and pawn auction sources.
