"""ArtDB binary format: layout definitions, blocked-text store, and a reader.

Directory layout (all little-endian):
  lots.bin            N x 64-byte LOT records, sorted by (day, sale, lot)   -> np.memmap
  lots_text.xz/.idx   per-lot text columns (artist line, title, description, image key)
  sales.bin           S x 32-byte SALE records (lots of a sale are rows first_row .. first_row+n_rows)
  sales_text.xz/.idx  sale titles
  artists.bin         A x 16-byte ARTIST records
  artists_text.xz/.idx artist display names
  schema.json         enums (category, medium, support, attribution), dictionaries
                      (currency, room, location, nationality), flag bit layout, counts

Usage:
  db = ArtDB("data/artdb")
  db.lots["price"], db.flag("medium"), db.lot_text(i), db.image_url(i)
"""
import json
import lzma
from datetime import date, timedelta
from pathlib import Path

import numpy as np

EPOCH = date(1980, 1, 1)  # earliest Christie's online results are 1989; u2 days reach 2159
NOT_FETCHED = 255

LOT_DTYPE = np.dtype([
    ("phash", "<u8"),          # 0   64-bit image perceptual hash (0 = not computed yet)
    ("object_id", "<u4"),      # 8   Christie's lot object id
    ("artist", "<u4"),         # 12  -> artists.bin row
    ("sale", "<u4"),           # 16  -> sales.bin row
    ("price", "<u4"),          # 20  price realised (incl. premium), whole units of sale currency; 0 = unsold
    ("flags", "<u4"),          # 24  bitfield, see FLAGS
    ("day", "<u2"),            # 28  sale day, days since EPOCH (1980-01-01)
    ("h_mm", "<u2"),           # 30  height, mm (0 = unknown)
    ("w_mm", "<u2"),           # 32  width, mm
    ("d_mm", "<u2"),           # 34  depth, mm
    ("year", "<i2"),           # 36  year created (0 = unknown)
    ("lot", "<u2"),            # 38  lot number (numeric part)
    ("palette", "<u2", (3,)),  # 40  3 dominant colours, RGB565 (0 = not computed)
    ("lot_suffix", "u1"),      # 46  lot letter suffix as ASCII ('A' in 113A), 0 = none
    ("n_prov", "u1"),          # 47  provenance entries   (255 = not fetched yet)
    ("n_exh", "u1"),           # 48  exhibitions          (255 = not fetched yet)
    ("n_lit", "u1"),           # 49  literature refs      (255 = not fetched yet)
    ("img_stats", "u1", (4,)), # 50  lightness, colourfulness, contrast, edge density (0 = not computed)
    ("subject", "u1"),         # 54  subject class (0 = not computed)
    ("reserved", "u1", (9,)),  # 55  room for future image tokens / features
])
assert LOT_DTYPE.itemsize == 64

SALE_DTYPE = np.dtype([
    ("sale_id", "<u4"),        # Christie's sale id (/en/auction/auction-{id})
    ("sale_number", "<u4"),
    ("first_row", "<u4"),      # first row in lots.bin
    ("day", "<u2"),
    ("minute", "<u2"),         # local start time, minutes after midnight (0xFFFF = unknown)
    ("n_listed", "<u2"),       # lots listed in the sale (all categories)
    ("n_rows", "<u2"),         # lots of this sale in lots.bin
    ("room", "u1"),            # -> schema.rooms
    ("currency", "u1"),        # -> schema.currencies
    ("location", "u1"),        # -> schema.locations
    ("flags", "u1"),           # bit0 evening sale, bit1 sale lists unsold lots
    ("reserved", "u1", (8,)),
])
assert SALE_DTYPE.itemsize == 32

ARTIST_DTYPE = np.dtype([
    ("born", "<i2"),           # 0 = unknown
    ("died", "<i2"),           # 0 = unknown / alive
    ("n_lots", "<u4"),
    ("first_day", "<u2"),      # first sale day in the dataset
    ("nationality", "u1"),     # -> schema.nationalities (0 = unknown)
    ("flags", "u1"),           # bit0 = generic "school" entry, not a person
    ("reserved", "u1", (4,)),
])
assert ARTIST_DTYPE.itemsize == 16

# name -> (first bit, width)
FLAGS = {
    "category": (0, 3), "medium": (3, 5), "support": (8, 4), "attribution": (12, 3),
    "sold": (15, 1), "withdrawn": (16, 1), "signed": (17, 1), "dated": (18, 1),
    "inscribed": (19, 1), "stamped": (20, 1), "circa": (21, 1), "round": (22, 1),
    "has_image": (23, 1), "price_clamped": (24, 1),
}

LOT_TEXT_COLS = ["artist_line", "title", "description", "image"]
IMG_PREFIX = "https://www.christies.com/img/lotimages/"


def pack_flags(**v):
    out = 0
    for k, x in v.items():
        b, w = FLAGS[k]
        x = int(x)
        assert 0 <= x < (1 << w), (k, x)
        out |= x << b
    return out


def to_day(iso):
    return (date.fromisoformat(iso[:10]) - EPOCH).days


def from_day(d):
    return EPOCH + timedelta(days=int(d))


# ---------------------------------------------------------------- blocked text store
# Rows are grouped in blocks of BLOCK rows; each block is LZMA-compressed independently so
# one row can be read by decompressing ~1k rows. Inside a block: fields joined by \x1f,
# rows by \x1e. .idx = uint64 byte offsets of each block (+ end offset).
BLOCK = 1024
_US, _RS = "\x1f", "\x1e"


def write_text(path, rows):
    path = Path(path)
    offs = [0]
    with open(path.with_suffix(".xz"), "wb") as fh:
        for i in range(0, len(rows), BLOCK):
            blob = _RS.join(_US.join(c or "" for c in r) for r in rows[i:i + BLOCK]).encode()
            z = lzma.compress(blob, format=lzma.FORMAT_RAW,
                              filters=[{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME}])
            fh.write(z)
            offs.append(offs[-1] + len(z))
    np.asarray(offs, "<u8").tofile(path.with_suffix(".idx"))


class TextStore:
    def __init__(self, path, cols):
        path = Path(path)
        self.data = np.memmap(path.with_suffix(".xz"), "u1", "r")
        self.idx = np.fromfile(path.with_suffix(".idx"), "<u8")
        self.cols = cols
        self._cache = (None, None)

    def block(self, b):
        if self._cache[0] != b:
            raw = bytes(self.data[self.idx[b]:self.idx[b + 1]])
            txt = lzma.decompress(raw, format=lzma.FORMAT_RAW, filters=[{"id": lzma.FILTER_LZMA2}]).decode()
            self._cache = (b, [r.split(_US) for r in txt.split(_RS)])
        return self._cache[1]

    def __getitem__(self, i):
        return dict(zip(self.cols, self.block(i // BLOCK)[i % BLOCK]))

    def column(self, name):
        """Whole column as a list (decompresses everything, ~seconds)."""
        k = self.cols.index(name)
        return [r[k] for b in range(len(self.idx) - 1) for r in self.block(b)]


# ---------------------------------------------------------------- image key compression
def image_key(url_part, year, room, sale_number, lot, suffix):
    """Most image paths are '{Y}/{R}/{Y}_{R}_{num:05}_{lot:04}{sfx}_000({tail}' and everything up to
    the '(' is derivable from the sale and lot. Then store only '~' + tail."""
    if not url_part:
        return ""
    t = f"{year}/{room}/{year}_{room}_{sale_number:05d}_{lot:04d}{suffix}_000("
    if url_part.startswith(t):
        return "~" + url_part[len(t):]
    return url_part


def image_from_key(key, year, room, sale_number, lot, suffix):
    if not key:
        return None
    if key.startswith("~"):
        key = f"{year}/{room}/{year}_{room}_{sale_number:05d}_{lot:04d}{suffix}_000({key[1:]}"
    return key if key.startswith("http") else f"{IMG_PREFIX}{key}?mode=max"


# ---------------------------------------------------------------- reader
class ArtDB:
    def __init__(self, root):
        root = Path(root)
        self.schema = json.loads((root / "schema.json").read_text())
        self.lots = np.memmap(root / "lots.bin", LOT_DTYPE, "r")
        self.sales = np.fromfile(root / "sales.bin", SALE_DTYPE)
        self.artists = np.fromfile(root / "artists.bin", ARTIST_DTYPE)
        self.text = TextStore(root / "lots_text", LOT_TEXT_COLS)
        self.sale_titles = TextStore(root / "sales_text", ["title"])
        self.artist_names = TextStore(root / "artists_text", ["name"])

    def __len__(self):
        return len(self.lots)

    def flag(self, name, rows=None):
        b, w = FLAGS[name]
        f = self.lots["flags"] if rows is None else self.lots["flags"][rows]
        return (f >> b) & ((1 << w) - 1)

    def lot_text(self, i):
        return self.text[i]

    def image_url(self, i):
        lot, sale = self.lots[i], self.sales[self.lots[i]["sale"]]
        sfx = chr(lot["lot_suffix"]) if lot["lot_suffix"] else ""
        return image_from_key(self.text[i]["image"], from_day(sale["day"]).year,
                              self.schema["rooms"][sale["room"]], int(sale["sale_number"]),
                              int(lot["lot"]), sfx)

    def describe(self, i):
        lot = self.lots[i]
        sale = self.sales[lot["sale"]]
        s = self.schema
        d = {k: self.flag(k, i).item() for k in FLAGS}
        for k, enum in (("category", "categories"), ("medium", "mediums"),
                        ("support", "supports"), ("attribution", "attributions")):
            d[k] = s[enum][d[k]]
        return {
            "artist": self.artist_names[int(lot["artist"])]["name"],
            **self.text[i], "image_url": self.image_url(i),
            "date": str(from_day(lot["day"])), "price": int(lot["price"]),
            "currency": s["currencies"][sale["currency"]],
            "sale": self.sale_titles[int(lot["sale"])]["title"],
            "size_mm": (int(lot["h_mm"]), int(lot["w_mm"])), "year": int(lot["year"]), **d,
        }
