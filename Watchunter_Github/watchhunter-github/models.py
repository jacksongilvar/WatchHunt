from dataclasses import dataclass, field
from typing import Optional


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

    @property
    def key(self) -> str:
        return f"{self.source}:{self.item_id}"
