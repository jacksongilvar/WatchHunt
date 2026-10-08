from dataclasses import dataclass, field
from typing import Optional


def query_items(queries, default_max):
    """Queries are plain strings or {q: ..., max_price: ...} for brands worth a higher ceiling."""
    for q in queries or []:
        if isinstance(q, dict):
            yield q["q"], q.get("max_price", default_max)
        else:
            yield q, default_max


def price_ceiling(cfg, brand=None):
    """Highest price worth looking at for a brand (brands can raise the global search.max_price)."""
    default = cfg["search"]["max_price"]
    if brand is None:
        return max([default] + [b.get("max_price", 0) for b in cfg["brands"].values()])
    return cfg["brands"].get(brand, {}).get("max_price", default)


@dataclass
class Listing:
    source: str                     # ebay | shopgoodwill | propertyroom
    item_id: str
    title: str
    url: str
    price: float = 0.0
    currency: str = "USD"
    bids: Optional[int] = None
    end_time: Optional[str] = None  # ISO string when the source gives one
    time_left: Optional[str] = None # human string when the source gives one
    buying_format: str = ""         # auction | buy_now | ""
    image_urls: list = field(default_factory=list)
    description: str = ""
    seller: str = ""
    shipping: Optional[float] = None  # inbound shipping when the source states it; None = use config estimate

    # filled in later
    score: int = 0
    reasons: list = field(default_factory=list)
    brand_hint: Optional[str] = None
    ai: Optional[dict] = None
    comps: Optional[dict] = None
    lens: Optional[dict] = None     # Google Lens matches for the main photo
    market: Optional[dict] = None   # asking-price band from Lens matches
    manual: Optional[dict] = None   # your own value range and service cost, from config.yaml watchlist

    @property
    def key(self) -> str:
        return f"{self.source}:{self.item_id}"
