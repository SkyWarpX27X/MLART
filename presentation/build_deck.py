"""Build the class presentation as PPTX (with the "$ ?" -> price animation) and a static PPTX for the PDF.

Coordinates are in px on a 1920x1080 canvas (1 px = 6350 EMU, font px / 2 = pt), mirroring the web deck.

  .venv/bin/python presentation/build_deck.py
  soffice --headless --convert-to pdf --outdir presentation presentation/What_is_a_painting_worth_static.pptx
"""
from pathlib import Path

from lxml import etree
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

HERE = Path(__file__).resolve().parent
PX = 6350
SERIF, SANS = "Cormorant Garamond", "DM Sans"
INK, PAPER, RED, GOLD = "1C1A17", "F6F2EA", "A33B24", "D9A441"
MUTED, MUTED_DARK, CHIP, RULE = "5A5348", "CFC6B6", "E9E1D2", "B8AE9C"


def E(v):
    return Emu(int(round(v * PX)))


def _plain(shp):
    """Drop the default theme style (it adds a shadow); fill and line are set explicitly."""
    st = shp._element.find(qn("p:style"))
    if st is not None:
        shp._element.remove(st)


def rgb(h):
    return RGBColor.from_string(h)


class Slide:
    def __init__(self, prs, bg, notes):
        self.s = prs.slides.add_slide(prs.slide_layouts[6])
        self.s.background.fill.solid()
        self.s.background.fill.fore_color.rgb = rgb(bg)
        self.s.notes_slide.notes_text_frame.text = notes
        # fade transition between slides
        tr = etree.SubElement(self.s._element, qn("p:transition"))
        etree.SubElement(tr, qn("p:fade"))
        self.s._element.insert(self.s._element.index(self.s._element.find(qn("p:clrMapOvr"))) + 1, tr)

    def text(self, x, y, w, h, runs, size, color, font=SANS, bold=False, align="l", spacing=None,
             line=None, anchor="t", caps=False):
        """runs: str or list of (text, bold) pieces."""
        tb = self.s.shapes.add_textbox(E(x), E(y), E(w), E(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
        tf.vertical_anchor = {"t": MSO_ANCHOR.TOP, "m": MSO_ANCHOR.MIDDLE, "b": MSO_ANCHOR.BOTTOM}[anchor]
        p = tf.paragraphs[0]
        p.alignment = {"l": PP_ALIGN.LEFT, "r": PP_ALIGN.RIGHT, "c": PP_ALIGN.CENTER}[align]
        if line:
            p.line_spacing = line
        for t, b in ([(runs, bold)] if isinstance(runs, str) else runs):
            r = p.add_run()
            r.text = t.upper() if caps else t
            f = r.font
            f.name, f.size, f.bold = font, Pt(size / 2), b
            f.color.rgb = rgb(color)
            if spacing:
                r._r.get_or_add_rPr().set("spc", str(int(spacing / 2 * 100)))
        return tb

    def rect(self, x, y, w, h, fill, radius=None):
        shp = self.s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
                                      E(x), E(y), E(w), E(h))
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
        shp.line.fill.background()
        _plain(shp)
        if radius:
            shp.adjustments[0] = min(0.5, radius / min(w, h))
        return shp

    def arrow(self, x, y, w, h, fill):
        shp = self.s.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, E(x), E(y), E(w), E(h))
        shp.fill.solid()
        shp.fill.fore_color.rgb = rgb(fill)
        shp.line.fill.background()
        _plain(shp)

    def chip(self, x, y, label, size=32, fill=CHIP, color=INK, bold=False, padx=28, pady=16, radius=999):
        w = len(label) * size * 0.56 + 2 * padx
        h = size * 1.4 + 2 * pady
        self.rect(x, y, w, h, fill, radius=min(radius, h / 2))
        self.text(x, y, w, h, label, size, color, bold=bold, align="c", anchor="m")
        return w, h

    def image_contain(self, path, x, y, w, h):
        iw, ih = Image.open(path).size
        s = min(w / iw, h / ih)
        dw, dh = iw * s, ih * s
        self.s.shapes.add_picture(str(path), E(x + (w - dw) / 2), E(y + (h - dh) / 2), E(dw), E(dh))

    def painting(self, img, price, caption, dark, animate):
        price_c, cap_c = (GOLD, MUTED_DARK) if dark else (RED, MUTED)
        self.image_contain(HERE / "img" / img, 1432, 64, 360, 200)
        if animate:
            q = self.text(1432, 276, 360, 56, "$ ?", 44, price_c, font=SERIF, bold=True, align="r")
        p = self.text(1432, 276, 360, 56, price, 44, price_c, bold=True, align="r")
        self.text(1432, 334, 360, 34, caption, 24, cap_c, align="r")
        if animate:
            add_reveal(self.s, q.shape_id, p.shape_id)

    def header(self, eyebrow, title, dark=False, size=72, line=1.1):
        self.text(128, 128, 1256, 34, eyebrow, 24, GOLD if dark else RED, spacing=4, caps=True)
        lines = 3 if size > 80 else 2
        self.text(128, 170, 1256, size * line * lines + 10, title, size, PAPER if dark else INK,
                  font=SERIF, bold=True, line=line * 0.95)


def add_reveal(slide, q_id, p_id):
    """After the slide appears: '$ ?' fades out, then the price fades in (no clicks needed)."""
    xml = f"""
<p:timing xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:tnLst><p:par><p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot"><p:childTnLst>
  <p:seq concurrent="1" nextAc="seek"><p:cTn id="2" dur="indefinite" nodeType="mainSeq"><p:childTnLst>
   <p:par><p:cTn id="3" fill="hold"><p:stCondLst><p:cond delay="indefinite"/><p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond></p:stCondLst><p:childTnLst>
    <p:par><p:cTn id="4" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>
     <p:par><p:cTn id="5" presetID="10" presetClass="exit" presetSubtype="0" fill="hold" grpId="0" nodeType="afterEffect"><p:stCondLst><p:cond delay="700"/></p:stCondLst><p:childTnLst>
      <p:animEffect transition="out" filter="fade"><p:cBhvr><p:cTn id="6" dur="400"/><p:tgtEl><p:spTgt spid="{q_id}"/></p:tgtEl></p:cBhvr></p:animEffect>
      <p:set><p:cBhvr><p:cTn id="7" dur="1" fill="hold"><p:stCondLst><p:cond delay="399"/></p:stCondLst></p:cTn><p:tgtEl><p:spTgt spid="{q_id}"/></p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr><p:to><p:strVal val="hidden"/></p:to></p:set>
     </p:childTnLst></p:cTn></p:par>
    </p:childTnLst></p:cTn></p:par>
    <p:par><p:cTn id="8" fill="hold"><p:stCondLst><p:cond delay="1100"/></p:stCondLst><p:childTnLst>
     <p:par><p:cTn id="9" presetID="10" presetClass="entr" presetSubtype="0" fill="hold" grpId="0" nodeType="afterEffect"><p:stCondLst><p:cond delay="0"/></p:stCondLst><p:childTnLst>
      <p:set><p:cBhvr><p:cTn id="10" dur="1" fill="hold"><p:stCondLst><p:cond delay="0"/></p:stCondLst></p:cTn><p:tgtEl><p:spTgt spid="{p_id}"/></p:tgtEl><p:attrNameLst><p:attrName>style.visibility</p:attrName></p:attrNameLst></p:cBhvr><p:to><p:strVal val="visible"/></p:to></p:set>
      <p:animEffect transition="in" filter="fade"><p:cBhvr><p:cTn id="11" dur="600"/><p:tgtEl><p:spTgt spid="{p_id}"/></p:tgtEl></p:cBhvr></p:animEffect>
     </p:childTnLst></p:cTn></p:par>
    </p:childTnLst></p:cTn></p:par>
   </p:childTnLst></p:cTn></p:par>
  </p:childTnLst></p:cTn>
  <p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>
  <p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst></p:seq>
 </p:childTnLst></p:cTn></p:par></p:tnLst>
 <p:bldLst><p:bldP spid="{q_id}" grpId="0"/><p:bldP spid="{p_id}" grpId="0"/></p:bldLst>
</p:timing>"""
    el = slide._element
    el.insert(el.index(el.find(qn("p:transition"))) + 1, etree.fromstring(xml))


def build(animate):
    prs = Presentation()
    prs.slide_width, prs.slide_height = E(1920), E(1080)

    # 1 cover
    s = Slide(prs, INK, "Hi, we are Dmytro Lapko and Mykhailo Ostapchuk. Our project: can you predict what a painting "
                        "sells for at Christie’s, just from its catalogue entry?")
    s.text(128, 128, 1256, 34, "ML project · Christie’s auctions 1989–2026", 24, GOLD, spacing=4, caps=True)
    s.text(128, 360, 1256, 330, "What is a painting worth?", 160, PAPER, font=SERIF, bold=True, line=0.95)
    s.text(128, 720, 1256, 62, "Predicting auction prices from the catalogue alone", 44, MUTED_DARK)
    s.text(128, 907, 1256, 45, "Dmytro Lapko · Mykhailo Ostapchuk", 32, MUTED_DARK)
    s.painting("6110563.jpg", "$450,312,500", "Leonardo da Vinci · 2017", True, animate)

    # 2 task
    s = Slide(prs, PAPER, "The task: given only what’s in the catalogue — artist, size, medium, dates, sale — predict the "
                          "final price. We deliberately don’t use the auction house’s own estimate. Prices span seven "
                          "orders of magnitude, so we predict log price.")
    s.header("Task", "Predict the sale price — without Christie’s estimate")
    x, y = 128, 470
    for label in ["artist", "size", "medium", "year painted"]:
        w, h = s.chip(x, y, label)
        x += w + 16
    x, y = 128, y + h + 16
    for label in ["signed", "sale date & type"]:
        w, _ = s.chip(x, y, label)
        x += w + 16
    s.arrow(1036, 513, 160, 80, RED)
    s.text(1244, 455, 420, 180, "$ ?", 160, RED, font=SERIF, bold=True)
    s.text(128, 907, 1664, 45, [("Prices range from ", False), ("$37", True), (" to ", False), ("$450,312,500", True),
                                (" · tested on future sales (2022+)", False)], 32, MUTED)
    s.painting("6369449.jpg", "$195,040,000", "Andy Warhol · 2022", False, animate)

    # 3 data
    s = Slide(prs, PAPER, "No ready dataset existed, so we scraped Christie’s public results: 560 thousand paintings and "
                          "works on paper. Descriptions were parsed into size, medium, dates and signatures, and stored "
                          "in a compact custom binary format of about 80 MB. For 34 thousand paintings we also computed "
                          "simple image features in the browser.")
    s.header("Data", "We built our own dataset from Christie’s")
    for i, (num, lab) in enumerate([("560k", "lots"), ("121k", "artists"), ("8,249", "sales, 1989–2026"),
                                     ("34k", "images analysed")]):
        x = 128 + i * (392 + 32)
        s.text(x, 450, 392, 110, num, 96, RED, font=SERIF, bold=True)
        s.text(x, 562, 392, 45, lab, 32, MUTED)
    x = 128
    for i, label in enumerate(["Scrape", "Parse text", "64-byte binary records · 80 MB", "Model"]):
        w, h = s.chip(x, 860, label, fill=INK, color=PAPER, radius=12)
        x += w + 24
        if i < 3:
            s.arrow(x, 860 + h / 2 - 16, 64, 32, RULE)
            x += 64 + 24
    s.painting("5895962.jpg", "$179,365,000", "Pablo Picasso · 2015", False, animate)

    # 4 approach
    s = Slide(prs, PAPER, "We kept USD paintings sold since 2010, about 35 thousand. Features: size, dates, medium, artist "
                          "history, sale context. We went from trivial baselines up to gradient boosting, each with and "
                          "without the artist as a feature. Training and validation on 2010–2021, split by sale in time "
                          "order; 2022 onwards is a held-out test used once.")
    s.header("Approach", "35k USD paintings, models from simple to strong")
    x = 128
    steps = ["Median", "Artist average", "Ridge", "Random forest", "Gradient boosting"]
    for i, label in enumerate(steps):
        last = i == len(steps) - 1
        w, h = s.chip(x, 520, label, fill=RED if last else CHIP, color=PAPER if last else INK, bold=last,
                      padx=20, radius=12)
        x += w + 14
        if not last:
            s.arrow(x, 520 + h / 2 - 10, 40, 20, RULE)
            x += 40 + 14
    tw = 1664 * 27636 / 35141
    s.rect(128, 790, tw, 96, INK)
    s.text(160, 790, tw - 64, 96, "Train + time-ordered CV · 2010–2021 · 27,636 lots", 32, PAPER, anchor="m")
    s.rect(128 + tw, 790, 1664 - tw, 96, GOLD)
    s.text(128 + tw + 24, 790, 1664 - tw - 48, 96, "Test 2022+", 32, INK, bold=True, anchor="m")
    s.text(128, 898, 1664, 45, "Every model with and without the artist · also tested on unseen artists", 32, MUTED)
    s.painting("6508394.jpg", "$121,160,000", "René Magritte · 2024", False, animate)

    # 5 results
    s = Slide(prs, PAPER, "Typical error as a factor: ×2.3 means a $100k painting is typically predicted between about "
                          "$43k and $230k. The best model, gradient boosting with the artist, cuts the error of a naive "
                          "guess by more than half. For artists the model has never seen it is weaker, about ×3.")
    s.header("Results · 2022+ test", "Typical error: from ×5.3 down to ×2.3")
    bars = [("Median guess", 1000, RULE, "×5.3", False), ("Artist average", 604, RULE, "×3.2", False),
            ("GB, no artist", 509, INK, "×2.7", False), ("Ridge", 491, INK, "×2.6", False),
            ("Gradient boosting", 434, RED, "×2.3 best", True)]
    for i, (lab, w, c, val, best) in enumerate(bars):
        y = 400 + i * 74
        s.text(128, y, 420, 56, lab, 32, INK, bold=best, align="r", anchor="m")
        s.rect(572, y, w, 56, c)
        s.text(572 + w + 24, y, 300, 56, val, 32, RED if best else INK, bold=True, anchor="m")
    s.text(128, 907, 1664, 45, "Artists seen before ×2.2 · artists new to the model ×3.0", 32, MUTED)
    s.painting("6397113.jpg", "$117,180,000", "Vincent van Gogh · 2022", False, animate)

    # 6 lessons
    s = Slide(prs, PAPER, "What helped most: gradient boosting, the artist’s history at auction, and how the auction house "
                          "places the lot — evening sale and lot position. Image colour statistics and random forest did "
                          "not help. And real data bites: some clocks and documents were filed as paintings, and one "
                          "artist can appear under several names, like Canaletto.")
    s.header("What we tried", "What worked, what didn’t")
    cards = [("Worked", "2F6B4F", "2F6B4F", ["Gradient boosting", "Artist’s auction history",
                                              "Sale context: evening sale, lot position"]),
             ("Didn’t", RED, RED, ["Colour & image statistics", "Random forest, plain linear"]),
             ("Data traps", GOLD, "8A6416", ["Clocks & documents filed as paintings", "One artist, several names"])]
    cw = (1664 - 64) / 3
    for i, (title, bar, tcol, items) in enumerate(cards):
        x = 128 + i * (cw + 32)
        s.rect(x, 450, cw, 420, "FFFDF8", radius=12)
        s.rect(x, 450, cw, 8, bar)
        s.text(x + 40, 490, cw - 80, 56, title, 44, tcol, bold=True)
        y = 570
        for it in items:
            lines = 1 if len(it) * 32 * 0.55 < cw - 80 else 2
            s.text(x + 40, y, cw - 80, 45 * lines, it, 32, INK)
            y += 45 * lines + 20
    s.painting("6168966.jpg", "$91,875,000", "Edward Hopper · 2018", False, animate)

    # 7 wrap
    s = Slide(prs, INK, "Our main conclusion: the price is driven more by who the artist is and how the auction house "
                        "positions the lot than by the painting’s own characteristics. Team: Dmytro Lapko built the "
                        "scraper and dataset, Mykhailo Ostapchuk did the modelling and analysis. About two weeks of "
                        "work. Thank you.")
    s.header("Conclusion", "Reputation and placement matter more than the painting itself", dark=True, size=96,
             line=1.05)
    colw = (1664 - 96) / 3
    blocks = [("Team", ["Dmytro Lapko — data & scraping", "Mykhailo Ostapchuk — modelling & analysis"]),
              ("Tools", ["Python · scikit-learn · Jupyter · GitHub · Claude Code"]),
              ("Time", ["~2 weeks · 21 Oct – 4 Nov"])]
    for i, (lab, lines) in enumerate(blocks):
        x = 128 + i * (colw + 48)
        s.text(x, 720, colw, 34, lab, 24, GOLD, spacing=3, caps=True)
        y = 764
        for ln in lines:
            n = 1 if len(ln) * 32 * 0.55 < colw else 2
            s.text(x, y, colw, 45 * n, ln, 32, PAPER)
            y += 45 * n + 12
    s.painting("6171867.jpg", "$90,312,500", "David Hockney · 2018", True, animate)
    return prs


if __name__ == "__main__":
    build(animate=True).save(HERE / "What_is_a_painting_worth.pptx")
    build(animate=False).save(HERE / "What_is_a_painting_worth_static.pptx")
    print("saved")
