"""Stage 3: raw sale JSON -> ArtDB binary dataset (see artdb.py for the format).

Usage: python build.py [--raw data/raw/sales] [--out data/artdb]
Safe to run while the scan is still going: it just builds from what is there.
"""
import argparse
import collections
import gzip
import json
import re
from pathlib import Path

import numpy as np

import artdb
from artdb import ARTIST_DTYPE, LOT_DTYPE, NOT_FETCHED, SALE_DTYPE, pack_flags, to_day
from parse import ATTRIBUTION, CATEGORY, MEDIUM, SUPPORT, parse_lot
from images import merge_image_features

ROOT = Path(__file__).resolve().parent.parent / "data"
U4_MAX = 2**32 - 1


class Vocab:
    """string -> small int; id 0 is reserved for unknown."""

    def __init__(self):
        self.items = ["?"]
        self.ids = {"?": 0}

    def __call__(self, s):
        if not s:
            return 0
        if s not in self.ids:
            self.ids[s] = len(self.items)
            self.items.append(s)
        return self.ids[s]


def load_sales(raw):
    for p in sorted(Path(raw).glob("*/*.json.gz")):
        with gzip.open(p, "rt", encoding="utf-8") as fh:
            s = json.load(fh)
        if s.get("lots"):
            yield s


def start_minute(iso):
    # Times are local even though they carry a "Z" (a 18:30Z Melbourne sale is an evening sale)
    m = re.search(r"T(\d\d):(\d\d)", iso or "")
    if not m or (m.group(1), m.group(2)) == ("00", "00"):
        return 0xFFFF
    return int(m.group(1)) * 60 + int(m.group(2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=ROOT / "raw" / "sales")
    ap.add_argument("--out", default=ROOT / "artdb")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    rooms, currencies, locations, nats = Vocab(), Vocab(), Vocab(), Vocab()
    sales, lots, seen = [], [], set()
    for s in load_sales(a.raw):
        recs = []
        for raw_lot in s["lots"]:
            r = parse_lot(s, raw_lot)
            if r and r["object_id"] not in seen and r["date"]:
                seen.add(r["object_id"])
                r["artist_line"] = raw_lot.get("title_primary_txt") or ""
                recs.append(r)
        if not recs:
            continue
        s["_recs"] = recs
        s["_lists_unsold"] = any(not l.get("price_realised") and not l.get("lot_withdrawn") for l in s["lots"])
        sales.append(s)

    # ---- artists: one entry per folded name; life dates / nationality by majority vote
    by_key = collections.defaultdict(list)
    for s in sales:
        for r in s["_recs"]:
            by_key[r["artist"]["key"] or "?"].append(r)
    keys = sorted(by_key)
    artist_id = {k: i for i, k in enumerate(keys)}
    artists = np.zeros(len(keys), ARTIST_DTYPE)
    artist_names, artist_nat = [], []
    for i, k in enumerate(keys):
        rs = by_key[k]
        vote = lambda f: collections.Counter(x for x in (f(r) for r in rs) if x).most_common(1)
        born, died, nat = vote(lambda r: r["artist"]["born"]), vote(lambda r: r["artist"]["died"]), \
            vote(lambda r: r["artist"]["nationality"])
        name = collections.Counter(r["artist"]["name"] or "?" for r in rs).most_common(1)[0][0]
        artists[i] = (born[0][0] if born else 0, died[0][0] if died else 0, len(rs),
                      min(to_day(r["date"]) for r in rs), 0,
                      int(all(r["artist"]["attribution"] == "school" for r in rs)), 0)
        artist_names.append([name])
        artist_nat.append(nat[0][0] if nat else None)
    # nationality is a u1: keep the 254 most common values (parse noise lands in the tail)
    for nat, _ in collections.Counter(n for n in artist_nat if n).most_common(254):
        nats(nat)
    artists["nationality"] = [nats.ids.get(n, 0) if n else 0 for n in artist_nat]

    # ---- sort sales by date, lots within a sale by lot number
    sales.sort(key=lambda s: (min(r["date"] for r in s["_recs"]), s["sale_id"]))
    sale_arr = np.zeros(len(sales), SALE_DTYPE)
    rows, text_rows, n_img_tpl, n_clamped = [], [], 0, 0
    for si, s in enumerate(sales):
        recs = sorted(s["_recs"], key=lambda r: (r["date"], r["lot_number"], r["lot_suffix"]))
        cur = currencies(collections.Counter(r["currency"] for r in recs).most_common(1)[0][0])
        minute = start_minute(s.get("start"))
        evening = "evening" in (s.get("title") or "").lower() or (minute != 0xFFFF and minute >= 17 * 60)
        try:
            sale_number = int(s.get("sale_number") or 0)
        except ValueError:
            sale_number = 0
        room = s.get("room") or ""
        sale_arr[si] = (s["sale_id"], sale_number, len(rows), to_day(recs[0]["date"]), minute,
                        min(s.get("n_lots") or 0, 0xFFFF), len(recs), rooms(room), cur,
                        locations(s.get("location")), int(evening) | int(s["_lists_unsold"]) << 1, 0)
        for r in recs:
            price = round(r["price"])
            clamped = price > U4_MAX
            n_clamped += clamped
            fl = pack_flags(
                category=CATEGORY.index(r["category"]), medium=MEDIUM.index(r["medium"]),
                support=SUPPORT.index(r["support"]), attribution=ATTRIBUTION.index(r["artist"]["attribution"]),
                sold=r["sold"], withdrawn=r["withdrawn"], signed=r["signed"], dated=r["dated"],
                inscribed=r["inscribed"], stamped=r["stamped"], circa=r["circa"], round=r["round"],
                has_image=r["image"] is not None, price_clamped=clamped)
            rows.append((0, r["object_id"], artist_id[r["artist"]["key"] or "?"], si, min(price, U4_MAX), fl,
                         to_day(r["date"]), min(r["h_mm"], 0xFFFF), min(r["w_mm"], 0xFFFF),
                         min(r["d_mm"], 0xFFFF), r["year"] if -32768 < r["year"] < 32768 else 0,
                         min(r["lot_number"], 0xFFFF), (0, 0, 0), ord(r["lot_suffix"]) if r["lot_suffix"] else 0,
                         NOT_FETCHED, NOT_FETCHED, NOT_FETCHED, (0, 0, 0, 0), 0, (0, 0, 0, 0), (0,) * 5))
            key = artdb.image_key(r["image"], int(r["date"][:4]), room, sale_number, r["lot_number"],
                                  r["lot_suffix"])
            n_img_tpl += key.startswith("~")
            text_rows.append([r["artist_line"], r["title"], r["description"], key])

    lots = np.array(rows, LOT_DTYPE)
    n_img = merge_image_features(lots)
    lots.tofile(out / "lots.bin")
    sale_arr.tofile(out / "sales.bin")
    artists.tofile(out / "artists.bin")
    artdb.write_text(out / "lots_text", text_rows)
    artdb.write_text(out / "sales_text", [[s.get("title") or ""] for s in sales])
    artdb.write_text(out / "artists_text", artist_names)

    schema = {
        "version": 1, "source": "christies.com", "n_lots": len(lots), "n_sales": len(sales),
        "n_artists": len(keys), "epoch": str(artdb.EPOCH),
        "categories": CATEGORY, "mediums": MEDIUM, "supports": SUPPORT, "attributions": ATTRIBUTION,
        "rooms": rooms.items, "currencies": currencies.items, "locations": locations.items,
        "nationalities": nats.items, "flags": {k: list(v) for k, v in artdb.FLAGS.items()},
        "lot_text_columns": artdb.LOT_TEXT_COLS, "text_block_rows": artdb.BLOCK,
        "notes": {
            "price": "price realised incl. buyer's premium, whole units of the sale currency; 0 = not sold",
            "image": "lots_text.image: '' = no image, '~tail' = derivable path (see artdb.image_from_key)",
            "not_fetched": "n_prov/n_exh/n_lit = 255 until lot pages are scraped; image fields 0 until computed",
        },
    }
    (out / "schema.json").write_text(json.dumps(schema, indent=1, ensure_ascii=False))

    sizes = {p.name: p.stat().st_size for p in sorted(out.iterdir())}
    n = len(lots)
    print(f"lots {n:,}  sales {len(sales):,}  artists {len(keys):,}  clamped prices {n_clamped}")
    print(f"image features merged for {n_img:,} lots")
    print(f"image templated {n_img_tpl / max(1, sum((lots['flags'] >> 23) & 1)):.1%} of lots with images")
    for k, v in sizes.items():
        print(f"  {k:<18} {v / 1e6:9.2f} MB  ({v / max(n, 1):6.1f} B/lot)")
    print(f"  {'total':<18} {sum(sizes.values()) / 1e6:9.2f} MB")
    nat_counts = np.bincount(artists["nationality"], minlength=len(nats.items))
    print("top nationalities:", [(nats.items[i], int(nat_counts[i])) for i in np.argsort(-nat_counts)[:15]])
    print(f"rooms {len(rooms.items)}, currencies {len(currencies.items)}, locations {len(locations.items)}")


if __name__ == "__main__":
    main()
