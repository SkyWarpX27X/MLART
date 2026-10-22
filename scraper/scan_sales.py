"""Stage 1: walk Christie's sale IDs and save every sale that contains paintings / works on paper.

For each sale id:
  GET /en/auction/auction-{id}  -> sale metadata + first 84 lots (embedded JSON)
  if enough of those lots look like paintings/drawings, refetch with ?page=N, which
  embeds the whole sale in one response (sales over ~1000 lots get the tail from the API).

Output: data/raw/sales/{id//1000:03d}/{id}.json.gz  (one file per sale id, also for
skipped / missing ids, so the scan is resumable and never refetches).

Usage: python scan_sales.py [--start 5200] [--end 31500] [--delay 0.8] [--redo-incomplete]
"""
import argparse
import gzip
import json
import math
import sys
import time
from pathlib import Path

import requests

from common import BASE, Fetcher, is_art, js_object, slim_lot

OUT = Path(__file__).resolve().parent.parent / "data" / "raw" / "sales"
PAGE = 84
MAX_HTML_PAGE = 11
# Sending the page's full parameter set (savedlotsonly=False, empty strings) gets a 503
API_KEYS = ("language", "pagesize", "saleid", "salenumber", "saleroomcode", "saletype")


def out_path(sid):
    return OUT / f"{sid // 1000:03d}" / f"{sid}.json.gz"


def scan(f, sid):
    # Don't follow redirects blindly: onlineonly.christies.com hangs for scripted clients,
    # and the Location header alone tells us what kind of id this is.
    r = f.get(f"{BASE}/en/auction/auction-{sid}", allow_redirects=False)
    if r.is_redirect:
        loc = r.headers.get("location", "")
        if "onlineonly.christies.com" in loc:
            return {"sale_id": sid, "status": "online_only", "url": loc}
        if "/calendar" in loc:
            return {"sale_id": sid, "status": "missing"}
        r = f.get(requests.compat.urljoin(r.url, loc))
    if "/calendar" in r.url or r.status_code == 404:
        return {"sale_id": sid, "status": "missing"}
    h = r.text
    auc = js_object(h, r"window\.chrComponents\.auction\s*=\s*\{\s*data:\s*") or {}
    lots_c = (js_object(h, r"window\.chrComponents\.lots\s*=\s*") or {}).get("data", {})
    st = auc.get("auction_status") or {}
    sale = {
        "sale_id": sid,
        "status": "ok",
        "title": auc.get("title"),
        "event_type": auc.get("event_type"),
        "location": auc.get("sale_location"),
        "sale_number": auc.get("sale_number"),
        "room": auc.get("sale_room_code"),
        "start": st.get("sale_start_date_time") or st.get("start_date"),
        "end": st.get("sale_end_date_time") or st.get("end_date"),
        "tz": st.get("time_zone_start_date"),
        "total_txt": (auc.get("auction_total") or {}).get("total_txt"),
        "n_lots": int(lots_c.get("total_hits_filtered") or 0),
    }
    lots = [slim_lot(x) for x in lots_c.get("lots", [])]
    n_art = sale["art_in_first_page"] = sum(map(is_art, lots))
    # >=3 matching lots or >=10%: a single "oil" in a jewellery description is not a painting sale
    if n_art < 3 and n_art < 0.1 * max(len(lots), 1):
        sale["status"] = "no_art"
        return sale  # don't store non-art lots

    # The page is cumulative: ?page=N embeds lots 1..N*84, so one request gets the whole sale.
    # (The lot-search API misses some older sales entirely, so we don't use it.)
    # Above ~1000 lots the page errors out, so huge sales get the tail from the lot-search API
    # (which knows most, but not all, sales).
    pages = math.ceil(sale["n_lots"] / PAGE)
    if pages > 1:
        r = f.get(f"{BASE}/en/auction/auction-{sid}", params={"page": min(pages, MAX_HTML_PAGE)})
        full = (js_object(r.text, r"window\.chrComponents\.lots\s*=\s*") or {}).get("data", {})
        lots = [slim_lot(x) for x in full.get("lots", [])]
    page_params = (lots_c.get("lot_search_api_endpoint") or {}).get("parameters") or {}
    for p in range(MAX_HTML_PAGE + 1, pages + 1):
        params = {k: page_params.get(k) for k in API_KEYS} | {"page": str(p)}
        j = f.get(f"{BASE}/api/discoverywebsite/auctionpages/lotsearch", params=params).json()
        lots += [slim_lot(x) for x in j.get("lots", [])]
    sale["lots"] = lots
    if len(lots) < sale["n_lots"]:
        sale["status"] = "incomplete"
    return sale


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=int, default=5200)
    ap.add_argument("--end", type=int, default=31500)
    ap.add_argument("--delay", type=float, default=0.8)
    ap.add_argument("--redo-incomplete", action="store_true", help="refetch sales saved as incomplete")
    a = ap.parse_args()

    f = Fetcher(a.delay)
    t0, done, fails = time.time(), 0, 0
    for sid in range(a.start, a.end):
        path = out_path(sid)
        if path.exists():
            if not a.redo_incomplete:
                continue
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                if json.load(fh).get("status") != "incomplete":
                    continue
        try:
            sale = scan(f, sid)
            fails = 0
        except Exception as e:  # noqa: BLE001 - log and keep going, stop if site is clearly unhappy
            fails += 1
            print(f"{sid} ERROR {e}", file=sys.stderr, flush=True)
            if fails >= 5:
                sys.exit("5 consecutive failures, stopping (rerun to resume)")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(sale, fh, ensure_ascii=False)
        tmp.rename(path)
        done += 1
        n = len(sale.get("lots", []))
        print(f"{sid} {sale['status']:<11} lots={n:<4} {(sale.get('start') or '')[:10]} "
              f"{(sale.get('title') or '')[:60]}  [{done / (time.time() - t0) * 3600:.0f} sales/h]",
              flush=True)


if __name__ == "__main__":
    main()
