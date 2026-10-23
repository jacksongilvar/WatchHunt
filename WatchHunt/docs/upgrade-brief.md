# WatchHunt upgrade brief

Paste everything below into a stronger model along with access to the repo (github.com/jacksongilvar/WatchHunt, code in `WatchHunt/watchhunt/`).

---

You are a senior engineer and a pricing analyst for small-scale watch resale. I want you to make my tool WatchHunt a better searcher and a more accurate source of truth. Be blunt. If something I am doing cannot work, say so first and say why.

## What I am doing

I buy underpriced used watches and resell them. Right now it is a small test: vintage and modern Seiko and Citizen, plus some Hamilton and Gucci, bought at about $25 to $80 and resold mostly locally (Facebook Marketplace) with Poshmark and eBay as backups. I live in Brooklyn, NY. I hold at most 4 unsold watches at a time. The long-term goal is Cartier and Rolex once I have proven the model and can authenticate.

My results so far: 4 purchases. The two good ones were an untested Seiko quartz perpetual calendar won for $29.57 on a ShopGoodwill auction with almost no bidders and a Seiko 5 bought for $25 with a lowball Best Offer on a $65 eBay listing. Every busy auction I won or lost went at full market price.

## What WatchHunt is today

- Python, runs on GitHub Actions (full scan twice a day, bid refresh hourly), state in SQLite on a `state` branch, board published to GitHub Pages and installable on my phone.
- Sources: ShopGoodwill (JSON API at buyerapi.shopgoodwill.com) and PropertyRoom (HTML). eBay is dead: my eBay developer account was rejected, so I search eBay by hand on my phone.
- Pipeline: text heuristics score each listing, then a Claude vision check (brand, model, reference, condition, women's vs men's size, service needs, counterfeit checklist for Rolex, Cartier and Tudor) and an optional Google Lens lookup through SerpApi.
- Economics (`economics.py`, `service.py`, `sizing.py`, `closes.py`): value band, all-in cost, job-by-job service cost, fake-risk adjustment, max bid, break-even bid, green / amber / red zone.
- Real price data (`closes.py`): every scan now harvests finished ShopGoodwill auctions for my searches into a `closes` table (538 after the first run) and prices a watch from them when brand plus calibre match 3 or more closes.

## What is wrong

1. **Values are mostly guesses.** Of 89 ended board items only a handful were priced from real sales. The rest were AI or hand estimates. The first real data showed my resale assumption ($87 for a vintage Seiko 5) was far too low: steel men's Seiko automatics on ShopGoodwill close at a median of about $136, gold-tone about $66, women's about $41.
2. **Busy auctions end at market value.** Median final price on ended single watches was $80 against a median estimated value of $80. There is no spread for the winner. Only 2 of 33 would have made money after costs.
3. **Narrow sourcing.** Two auction sites, one of them barely useful. eBay is manual. No estate sales, pawn shops, local marketplaces or regional auction houses.
4. **Matching is crude.** Comps match on brand plus calibre regex or brand plus movement type. Group medians mix 1970s Seiko 5s with modern Prospex divers.

## What I want from you

### A. Find better sources of underpriced watches

Rank concrete sources by expected margin, volume, ease of automation and whether automation is allowed by their terms. For each: how to get listings (official API, RSS, search URL, email alerts, manual), what the competition looks like and what a realistic buy rate is. Consider at least: estate sale aggregators (EstateSales.net, EstateSales.org, MaxSold, CTBids), auction aggregators (HiBid, LiveAuctioneers, Proxibid, Invaluable, AuctionZip), government and police surplus (GovDeals, PropertyRoom, PublicSurplus), local marketplaces (Facebook Marketplace, OfferUp, Craigslist, Mercari), pawn shop networks and their eBay stores, Japanese sources (Yahoo Auctions Japan through a proxy service, Mercari Japan) and anything you think I am missing. Tell me which ones are worth building, which are worth me checking by hand and which to skip.

Also tell me how to find the low-competition listings inside busy sites: misspelled and vague titles, bad photos, wrong categories, untested quartz, daytime closes, auctions with no bids late, lots with one good watch. WatchHunt already flags some of these. Tell me what else predicts a quiet auction and how to measure it from data I can collect.

### B. Make WatchHunt an accurate source of truth on price

I need sold prices I can trust, by model and condition. Evaluate every option, including: eBay sold data without the Browse API (Terapeak through Seller Hub, the Marketplace Insights API and its access rules, 130point.com, manual export), WatchCharts, Chrono24 price data, WorthPoint, Reddit r/Watchexchange sold posts, ShopGoodwill closed search (already harvesting), and my own closed-auction table. For each: cost, legality and terms, coverage of $25 to $300 watches, freshness and how to ingest it.

Then design the pricing model:
- How to normalize a listing into a comparable key (brand, model family, reference, calibre, case material, gold-tone vs steel, men's vs women's, condition, working vs untested vs parts, box and papers)
- How to weight comps by similarity and recency, and when to fall back to a broader group
- How to separate wholesale price (where auctions end) from retail price (what I can sell for locally or on a fixed-price listing), since my profit is the gap between them
- How to show confidence so I know when a value is solid and when it is a guess

### C. Prove it works

Design a backtest using the `closes` table and my tracked ended auctions: for each past listing, what would the model have said the value and max bid were before it ended, how often would I have won and how much would I have made. Give me the metrics to track (value error, win rate at max bid, realized margin) and a target for each before I trust a number on the board.

### D. Deliverables

1. A ranked source list with a recommendation for each (build, check by hand, skip).
2. A data plan: which price sources to add, how to ingest each, the schema changes.
3. Specific code changes to the repo, in priority order, small enough to review one at a time. Keep the existing structure: sources in `source_*.py`, pricing in `economics.py` and `closes.py`, config in `config.yaml`.
4. The backtest, as a script I can run.
5. Anything in my approach you think is wrong, even if I did not ask.

## Constraints

- Budget: keep running costs under about $30 a month (Claude API and SerpApi included).
- Runs on GitHub Actions. Requests from the Actions runners work. Respect robots.txt and site terms. No CAPTCHA bypassing, no logging in to scrape, no buying data I am not allowed to use.
- I am not a full-time developer. Prefer simple, robust code over clever code, and explain anything I need to set up by hand.
- Mark every factual claim about a site or API as checked or unverified. Prices, rules and API access change, so check current terms before relying on them.
