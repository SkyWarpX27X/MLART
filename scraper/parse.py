"""Stage 2: turn raw Christie's lot JSON into clean, typed fields.

parse_lot(sale, lot) -> dict | None   (None = not a painting / work on paper)
"""
import html
import re
import unicodedata

# ---------------------------------------------------------------- enums (stable ids, stored in flags)
CATEGORY = ["other", "painting", "work_on_paper", "print", "photograph", "sculpture"]
MEDIUM = ["unknown", "oil", "acrylic", "tempera", "encaustic", "mixed_media", "watercolour",
          "gouache", "pastel", "charcoal", "pencil", "ink", "chalk", "crayon", "enamel",
          "collage", "spray_paint", "oilstick"]
SUPPORT = ["unknown", "canvas", "panel", "board", "paper", "copper", "card", "vellum", "silk",
           "metal", "glass"]
ATTRIBUTION = ["by", "attributed", "studio", "circle", "follower", "manner", "after", "school"]

# ---------------------------------------------------------------- text cleaning
_FRAC = {"½": ".5", "¼": ".25", "¾": ".75", "⅛": ".125", "⅜": ".375", "⅝": ".625", "⅞": ".875",
         "⅓": ".333", "⅔": ".667"}


def clean(s):
    if not s:
        return ""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s).replace("⁄", "/").replace("\xa0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return "\n".join(x.strip() for x in s.split("\n") if x.strip())


def fold(s):
    """ascii-fold + lowercase, for artist keys."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


# ---------------------------------------------------------------- artist line
_QUAL = [
    (r"attributed to", "attributed"), (r"(?:studio|workshop|atelier) of", "studio"),
    (r"circle of", "circle"), (r"(?:follower|pupil|entourage) of", "follower"),
    (r"(?:manner|style) of", "manner"), (r"after", "after"), (r"school of", "school"),
]
_QUAL_RE = re.compile(r"^(" + "|".join(q for q, _ in _QUAL) + r")\s+", re.I)
_SCHOOL_RE = re.compile(r"\bschool\b", re.I)
_PAREN_RE = re.compile(r"\(([^()]*(?:\d{3,4}|century)[^()]*)\)\s*$", re.I)


def _years(s):
    """'1881-1973', 'b. 1928', 'Spanish, 1881-1973', 'active 1600-1620', 'c.1600-1650'."""
    nat, born, died = None, None, None
    m = re.match(r"\s*([A-Za-z][A-Za-z\- ]+?),\s*(.*)$", s)
    if m and not re.match(r"(?i)(b|born|active|fl|c|circa)\b", m.group(1)):
        nat, s = m.group(1).strip().lower(), m.group(2)
    if re.search(r"(?i)\b(active|fl\.?|flourished)\b", s):
        return nat, None, None  # activity dates, not life dates
    m = re.search(r"(?i)\b(?:b\.|born)\s*(?:c\.|circa)?\s*(\d{4})", s)
    if m:
        return nat, int(m.group(1)), None
    ys = re.findall(r"\d{4}", s)
    if len(ys) >= 2:
        born, died = int(ys[0]), int(ys[1])
    return nat, born, died


def parse_artist(line):
    line = (line or "").strip()
    out = {"attribution": "by", "name": None, "key": None, "nationality": None, "born": None, "died": None}
    m = _QUAL_RE.match(line)
    if m:
        q = m.group(1).lower()
        out["attribution"] = next(v for p, v in _QUAL if re.fullmatch(p, q))
        line = line[m.end():]
    pm = _PAREN_RE.search(line)
    if pm:
        out["nationality"], out["born"], out["died"] = _years(pm.group(1))
        line = line[:pm.start()]
    name = line.strip(" ,.*")
    if not name:
        return out
    if _SCHOOL_RE.search(name) and not pm:  # "North Italian School, 18th Century"
        out["attribution"] = "school"
    if name.isupper():
        name = name.title()
    out["name"], out["key"] = name, fold(name)
    return out


# ---------------------------------------------------------------- medium / category
_PRINT = re.compile(r"\b(screenprint|silkscreen|serigraph|lithograph|etching|engraving|woodcut|"
                    r"aquatint|drypoint|linocut|mezzotint|offset|pochoir|giclee|edition of|"
                    r"numbered \d+/\d+)", re.I)
_PHOTO = re.compile(r"\b(gelatin silver|chromogenic|c-print|albumen|platinum print|palladium|"
                    r"dye transfer|cibachrome|daguerreotype|inkjet print|pigment print|photograph)", re.I)
_SCULPT = re.compile(r"\b(bronze|marble|terracotta|plaster|carved|cast|patina)\b", re.I)
_MED = [  # first match wins, order matters (oil before oilstick handled by \b)
    ("oilstick", r"oil ?stick|paintstick"), ("oil", r"\boils?\b"), ("acrylic", r"\bacrylic"),
    ("tempera", r"\btempera"), ("encaustic", r"\bencaustic"), ("spray_paint", r"spray ?paint|aerosol"),
    ("enamel", r"\benamel|\blacquer"), ("collage", r"\bcollage"), ("mixed_media", r"mixed media"),
    ("gouache", r"\bgouache"), ("watercolour", r"watercolou?r"), ("pastel", r"\bpastel"),
    ("charcoal", r"\bcharcoal"), ("ink", r"\bink\b|\bpen\b"), ("chalk", r"\bchalk"),
    ("crayon", r"\bcrayon|wax crayon"), ("pencil", r"\bpencil|\bgraphite"),
]
_MED = [(n, re.compile(p, re.I)) for n, p in _MED]
_SUP = [
    ("canvas", r"on (?:\w+ )?(?:canvas|linen)"), ("panel", r"on (?:\w+ )?(?:panel|wood|oak|poplar)"),
    ("copper", r"on copper"), ("board", r"on (?:\w+ )?(?:board|masonite|card ?board|hardboard|cardboard)"),
    ("vellum", r"on vellum"), ("silk", r"on silk"), ("card", r"on card\b"),
    ("metal", r"on (?:aluminium|aluminum|metal|tin|steel)"), ("glass", r"on glass"),
    ("paper", r"\bpaper\b"),
]
_SUP = [(n, re.compile(p, re.I)) for n, p in _SUP]
_PAINT_MED = {"oil", "acrylic", "tempera", "encaustic", "enamel", "spray_paint", "oilstick"}


def medium_line(lines):
    """The medium is usually the first line that mentions a medium word."""
    for ln in lines:
        if any(r.search(ln) for _, r in _MED) or re.search(r"\bon (canvas|panel|paper|board)", ln, re.I):
            return ln
    return ""


def classify(text, mline):
    med = next((n for n, r in _MED if r.search(mline)), "unknown")
    sup = next((n for n, r in _SUP if r.search(mline)), "unknown")
    if _PHOTO.search(text):
        cat = "photograph"
    elif _PRINT.search(mline) or _PRINT.search(text):
        cat = "print" if med not in _PAINT_MED else ("painting" if sup != "paper" else "work_on_paper")
    elif med in ("enamel", "spray_paint") and sup == "unknown":
        cat = "other"  # lacquer boxes, enamelled objects
    elif med in _PAINT_MED:
        cat = "painting"
    elif med != "unknown":
        cat = "work_on_paper"
    elif sup in ("canvas", "panel", "copper"):
        cat = "painting"
    elif _SCULPT.search(mline):
        cat = "sculpture"
    else:
        cat = "other"
    return cat, med, sup


# ---------------------------------------------------------------- dimensions
_NUM = r"\d+(?:[.,]\d+)?"
_CM = re.compile(rf"({_NUM})\s*x\s*({_NUM})(?:\s*x\s*({_NUM}))?\s*(cm|mm)\b", re.I)
_CM_DIAM = re.compile(rf"({_NUM})\s*(cm|mm)\.?\s*(?:diam|diameter)", re.I)
_IN = re.compile(r"(\d+(?:\.\d+)?(?: \d/\d+)?)\s*x\s*(\d+(?:\.\d+)?(?: \d/\d+)?)\s*(?:in\b|in\.|inches)", re.I)


def _f(s):
    return float(s.replace(",", "."))


def _inch(s):
    m = re.match(r"(\d+(?:\.\d+)?)(?: (\d)/(\d+))?$", s.strip())
    return float(m.group(1)) + (int(m.group(2)) / int(m.group(3)) if m.group(2) else 0)


def dimensions(text):
    """-> (h_mm, w_mm, d_mm, is_round). First metric match wins (Christie's: height x width)."""
    for k, v in _FRAC.items():
        text = re.sub(rf"(\d) ?{k}", rf"\1{v}", text).replace(k, "0" + v)
    m = _CM.search(text)
    if m:
        k = 10 if m.group(4).lower() == "cm" else 1
        d = _f(m.group(3)) * k if m.group(3) else 0
        return round(_f(m.group(1)) * k), round(_f(m.group(2)) * k), round(d), False
    m = _CM_DIAM.search(text)
    if m:
        v = round(_f(m.group(1)) * (10 if m.group(2).lower() == "cm" else 1))
        return v, v, 0, True
    m = _IN.search(text)
    if m:
        return round(_inch(m.group(1)) * 25.4), round(_inch(m.group(2)) * 25.4), 0, False
    return 0, 0, 0, False


# ---------------------------------------------------------------- year created / signatures
_EXEC = re.compile(r"(?i)\b(?:painted|executed|drawn|created|made|conceived|completed)\s+"
                   r"(?:in\s+|circa\s+|c\.\s*|between\s+|on\s+[^,.]*?)?(?:(circa|c\.)\s*)?(\d{4})")
_DATED4 = re.compile(r"(?i)\bdated\b[^\n]{0,60}?(?<!\d)(1[4-9]\d\d|20[0-2]\d)(?!\d)")
_DATED2 = re.compile(r"(?i)\bdated\b[^\n]{0,40}?['‘\s](\d\d)(?:['’)\s]|$)")
_CIRCA = re.compile(r"(?i)\b(?:circa|c\.)\s*(1[4-9]\d\d|20[0-2]\d)")


def year_created(text, born, died):
    m = _EXEC.search(text)
    if m:
        return int(m.group(2)), bool(m.group(1)) or "circa" in m.group(0).lower()
    m = _DATED4.search(text)
    if m:
        return int(m.group(1)), False
    m = _DATED2.search(text)
    if m and born:
        yy = int(m.group(1))
        for century in (1800, 1900, 2000):
            y = century + yy
            if born + 8 <= y <= (died or 2030):
                return y, False
    m = _CIRCA.search(text)
    if m:
        return int(m.group(1)), True
    return 0, False


def signatures(text):
    t = text.lower()
    signed = bool(re.search(r"(?<!un)\bsigned\b", t)) and "unsigned" not in t.split("signed")[0][-3:]
    return {
        "signed": signed,
        "dated": bool(re.search(r"(?<!un)\bdated\b", t)),
        "inscribed": bool(re.search(r"\binscribed\b", t)),
        "stamped": bool(re.search(r"\bstamp(ed)?\b", t)),
    }


# ---------------------------------------------------------------- image url
IMG_PREFIX = "https://www.christies.com/img/lotimages/"
_IMG_RE = re.compile(r"(?i)^https://www\.christies\.com/img/lotimages/(.+?)(?:\?mode=max)?$")
_PLACEHOLDER = re.compile(r"(?i)/Alert/")


def image_part(url):
    """Store only the path after /img/lotimages/ (prefix and ?mode=max are constant).
    Returns None for placeholders / missing images."""
    if not url or _PLACEHOLDER.search(url):
        return None
    m = _IMG_RE.match(url)
    return m.group(1) if m else url  # unexpected host: keep full url


def image_url(part):
    if part is None:
        return None
    return part if part.startswith("http") else f"{IMG_PREFIX}{part}?mode=max"


# ---------------------------------------------------------------- lot
def _money(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return 0.0
    return v if v > 0 else 0.0


def currency_of(*txts):
    for t in txts:
        m = re.match(r"\s*([A-Z]{3})\b", t or "")
        if m:
            return m.group(1)
    return None


def parse_lot(sale, lot):
    desc = clean(lot.get("description_txt"))
    lines = desc.split("\n") if desc else []
    primary = clean(lot.get("title_primary_txt"))
    mline = medium_line(lines[1:] or lines)
    cat, med, sup = classify(desc, mline)
    if cat not in ("painting", "work_on_paper"):
        return None

    art = parse_artist(primary.split("\n")[0])
    if art["born"] is None and lines:  # 2005-era: "EDWARD RUSCHA" in title, dates in description
        a2 = parse_artist(lines[0])
        if a2["born"] and a2["key"] == art["key"]:
            art = a2
    body = "\n".join(lines[1:])
    h, w, d, is_round = dimensions(body)
    year, circa = year_created(body, art["born"], art["died"])
    lot_txt = (lot.get("lot_id_txt") or "").strip()
    m = re.match(r"(\d+)\s*([A-Za-z]*)", lot_txt)
    price = _money(lot.get("price_realised"))
    withdrawn = bool(lot.get("lot_withdrawn"))
    return {
        "object_id": int(lot["object_id"]),
        "sale_id": sale["sale_id"],
        "date": (lot.get("start_date") or sale.get("start") or "")[:10],
        "lot_number": int(m.group(1)) if m else 0,
        "lot_suffix": (m.group(2)[:1].upper() if m else ""),
        "artist": art,
        # pre-2010ish lots have no separate title field; it is the line after the artist
        "title": clean(lot.get("title_secondary_txt")) or (lines[1] if len(lines) > 1 and lines[1] != mline else ""),
        "category": cat, "medium": med, "support": sup,
        "h_mm": h, "w_mm": w, "d_mm": d, "round": is_round,
        "year": year, "circa": circa,
        **signatures(body),
        "price": price,
        "currency": currency_of(lot.get("price_realised_txt"), sale.get("total_txt")),
        "sold": price > 0,
        "withdrawn": withdrawn,
        "image": image_part(lot.get("image")),
        "description": desc,
    }
