# MLART — predicting auction prices of paintings

Dataset + pipeline for predicting what a painting or work on paper sells for at auction,
built from Christie's public results (1989–2026). Pre-sale estimates are deliberately
**not** features: the goal is to explain price from the work itself (size, medium, period,
attribution, image, sale context), not to copy the auction house's experts.

## Dataset (`data/artdb/`, ~80 MB)

560,691 lots (337k paintings, 224k works on paper) · 8,249 sales · 121k artists.

```python
import sys; sys.path.insert(0, "scraper")
from artdb import ArtDB

db = ArtDB("data/artdb")
db.lots                 # numpy memmap, 64-byte records (price, size, date, flags, ...)
db.flag("medium")       # decode a bitfield column for all lots
db.describe(123)        # one lot as a readable dict
db.image_url(123)       # image link, rebuilt from a short stored key
```

| file | content |
|---|---|
| `lots.bin` | fixed 64-byte records, sorted by sale date → `lots[:k]` / `lots[k:]` is a time split |
| `lots_text.xz/.idx` | artist line, title, full description, image key (LZMA, blocks of 1024 rows) |
| `sales.bin`, `sales_text.*` | sale date/time, room, city, currency, evening-sale flag, title |
| `artists.bin`, `artists_text.*` | birth/death year, nationality, lot count, name |
| `schema.json` | enums, dictionaries and flag bit layout |

Record layouts are defined in `scraper/artdb.py`. `n_prov/n_exh/n_lit` are 255 (not scraped yet).

**Image features** exist for 34,018 lots (USD paintings sold since 2010; `db.flag("img_features") == 1`):
`img_stats` = lightness, colourfulness, contrast, edge density; `img_extra` = saturation, warmth,
entropy, near-white share (all 0–255); `palette` = 3 dominant colours (RGB565); `phash` = 64-bit
perceptual hash. Source of truth: `data/images/features.jsonl` (by object_id), merged on every build
or with `python3 scraper/images.py merge`. Computed in-browser by `scraper/image_worker.js`.

### Caveats
- **Prices** are price realised incl. buyer's premium, in the **original currency** (14, incl.
  ITL/NLG/FRF) — convert to USD and adjust for inflation before comparing.
- **Unsold lots** are only listed from ~2020; earlier sales show sold lots only.
- **Images**: 64% of lots have one (≈0% before 1995, ~100% from 2015).
- **Online-only sales** (1,606, mostly 2017+) are not included yet.
- Year painted is known for 40% of lots, size for 95%, artist birth year for 79%.

## Pipeline (`scraper/`)

```bash
pip install -r requirements.txt
cd scraper
python3 scan_sales.py   # ~10 h, resumable: raw sale JSON -> data/raw/sales (not in git)
python3 build.py        # ~8 min: parse + write data/artdb
```

- `scan_sales.py` — walks sale ids 5200–31500, keeps sales containing paintings/drawings
- `parse.py` — extracts artist, attribution, medium, support, size, year, signatures from descriptions
- `build.py` — lot-level filtering, artist/sale tables, binary writer
- `artdb.py` — format definition and reader

Lot images (`/img/lotimages/`) are not fetchable by scripts; they load from a normal
browser session (Christie's confirmed browser access is fine).
