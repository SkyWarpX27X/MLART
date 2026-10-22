"""Shared HTTP + parsing helpers for the Christie's scraper."""
import json
import random
import re
import time

import requests

BASE = "https://www.christies.com"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# Only the fields we actually use; the API returns ~3x more (mobile/tablet image copies etc.)
LOT_FIELDS = (
    "object_id", "lot_id_txt", "start_date", "end_date",
    "title_primary_txt", "title_secondary_txt", "title_tertiary_txt",
    "consigner_information", "description_txt",
    "estimate_low", "estimate_high", "estimate_on_request",
    "price_realised", "price_realised_txt", "price_on_request",
    "is_unsold", "lot_withdrawn",
)

# Lot-level medium keywords: does this sale contain paintings / works on paper?
ART_RE = re.compile(
    r"\b(oil|acrylic|tempera|gouache|watercolou?r|pastel|charcoal|encaustic|"
    r"pen and|brush and|ink and|pencil|crayon|chalk|mixed media|"
    r"on (canvas|panel|paper|board|card|copper|masonite|linen|vellum))\b",
    re.I,
)


class Fetcher:
    """Single-connection polite fetcher: fixed delay + jitter, retries with backoff."""

    def __init__(self, delay=0.8):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en"})
        self.delay = delay
        self._last = 0.0

    def get(self, url, params=None, tries=5, **kw):
        for attempt in range(tries):
            wait = self.delay + random.uniform(0, 0.4) - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.s.get(url, params=params, timeout=40, **kw)
            except requests.RequestException:
                r = None
            if r is not None and r.status_code == 200:
                return r
            if r is not None and (r.status_code == 404 or r.is_redirect):
                return r  # redirects only surface here when called with allow_redirects=False
            time.sleep(min(300, 5 * 3 ** attempt))  # 5s, 15s, 45s, 135s, 300s
        raise RuntimeError(f"giving up on {url} ({r.status_code if r is not None else 'no response'})")


def js_object(html, prefix_re):
    """Decode the JSON value that follows a JS assignment matched by prefix_re."""
    m = re.search(prefix_re, html)
    if not m:
        return None
    try:
        return json.JSONDecoder().raw_decode(html[m.end():])[0]
    except ValueError:
        return None


def slim_lot(x):
    d = {k: x.get(k) for k in LOT_FIELDS}
    d["image"] = (x.get("image") or {}).get("image_src")
    return d


def is_art(lot):
    text = " ".join(filter(None, (lot.get("description_txt"), lot.get("title_secondary_txt"))))
    return bool(ART_RE.search(text))
