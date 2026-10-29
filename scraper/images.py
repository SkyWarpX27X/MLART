"""Image features: local collector for the in-browser worker (image_worker.js).

Christie's lot images only load in a normal browser session, so the features are computed
inside a christies.com tab and posted here. Results are keyed by object_id, so they survive
dataset rebuilds; build.py merges them into lots.bin.

  python images.py serve [--subset usd2010|all] [--port 8765]
  python images.py stats
  python images.py merge      # write features into data/artdb/lots.bin in place (no rebuild)

Files: data/images/features.jsonl (one JSON object per lot), data/images/errors.jsonl
"""
import argparse
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np

from artdb import FLAGS, IMG_EXTRA, IMG_STATS, LOT_DTYPE, ArtDB, from_day

ROOT = Path(__file__).resolve().parent.parent / "data"
OUT = ROOT / "images"
FEATURES = OUT / "features.jsonl"
ERRORS = OUT / "errors.jsonl"
ORIGIN = "https://www.christies.com"
LEASE_S = 600


def subset_rows(db, subset):
    """Row indices of lots to process. usd2010 = the notebook's subset (USD paintings sold since 2010)."""
    rows = np.flatnonzero(db.flag("has_image") == 1)
    if subset == "usd2010":
        cur = np.array(db.schema["currencies"])[db.sales["currency"][db.lots["sale"]]]
        year = np.array([from_day(d).year for d in db.lots["day"]])
        keep = ((cur == "USD") & (db.flag("category") == 1) & (db.flag("sold") == 1)
                & (db.flag("withdrawn") == 0) & (year >= 2010))
        rows = rows[keep[rows]]
    return rows[::-1]  # newest first: best image quality


def merge_image_features(lots, path=FEATURES):
    """Fill image fields of a LOT_DTYPE array from features.jsonl (matched by object_id)."""
    if not Path(path).exists():
        return 0
    row = {int(o): i for i, o in enumerate(lots["object_id"])}
    bit = 1 << FLAGS["img_features"][0]
    n = 0
    with open(path) as fh:
        for line in fh:
            f = json.loads(line)
            i = row.get(f["id"])
            if i is None:
                continue
            lots["phash"][i] = int(f["phash"], 16)
            lots["palette"][i] = f["palette"]
            lots["img_stats"][i] = [f[k] for k in IMG_STATS]
            lots["img_extra"][i] = [f[k] for k in IMG_EXTRA]
            lots["flags"][i] |= bit
            n += 1
    return n


def done_ids():
    ids = set()
    for p in (FEATURES, ERRORS):
        if p.exists():
            with open(p) as fh:
                ids.update(json.loads(line)["id"] for line in fh if line.strip())
    return ids


class Queue:
    def __init__(self, items):
        self.items = items  # [(object_id, url)]
        self.pos = 0
        self.leased = {}  # id -> lease time
        self.lock = threading.Lock()
        self.done = done_ids()

    def take(self, n):
        out, now = [], time.time()
        with self.lock:
            # expired leases go back to the front
            for oid, t in list(self.leased.items()):
                if now - t > LEASE_S:
                    del self.leased[oid]
            pending = [(i, u) for i, u in self.items if i not in self.done and i not in self.leased]
            for oid, url in pending[:n]:
                self.leased[oid] = now
                out.append({"id": oid, "url": url})
        return out

    def finish(self, results):
        with self.lock, open(FEATURES, "a") as ok, open(ERRORS, "a") as bad:
            for r in results:
                (bad if "err" in r else ok).write(json.dumps(r) + "\n")
                self.done.add(r["id"])
                self.leased.pop(r["id"], None)

    def stats(self):
        total = len(self.items)
        n_done = sum(1 for i, _ in self.items if i in self.done)
        return {"total": total, "done": n_done, "left": total - n_done, "leased": len(self.leased)}


def make_handler(q):
    class H(BaseHTTPRequestHandler):
        def _cors(self):
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Allow-Private-Network", "true")

        def _json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self._cors()
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors()
            self.end_headers()

        def do_GET(self):
            u = urlparse(self.path)
            if u.path == "/todo":
                n = int(parse_qs(u.query).get("n", ["50"])[0])
                return self._json(q.take(min(n, 500)))
            if u.path == "/stats":
                return self._json(q.stats())
            self._json({"error": "not found"}, 404)

        def do_POST(self):
            if urlparse(self.path).path != "/done":
                return self._json({"error": "not found"}, 404)
            results = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            q.finish(results)
            s = q.stats()
            print(f"{time.strftime('%H:%M:%S')} +{len(results)}  done {s['done']}/{s['total']}", flush=True)
            self._json(s)

        def log_message(self, *a):
            pass

    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["serve", "stats", "merge"])
    ap.add_argument("--subset", default="usd2010", choices=["usd2010", "all"])
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()

    if a.cmd == "merge":
        lots = np.memmap(ROOT / "artdb" / "lots.bin", LOT_DTYPE, "r+")
        print(f"merged image features for {merge_image_features(lots):,} lots")
        lots.flush()
        return
    db = ArtDB(ROOT / "artdb")
    rows = subset_rows(db, a.subset)
    items = [(int(db.lots[i]["object_id"]), db.image_url(int(i))) for i in rows]
    OUT.mkdir(parents=True, exist_ok=True)
    q = Queue(items)
    print(json.dumps(q.stats()), flush=True)
    if a.cmd == "stats":
        return
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(q))
    print(f"collector on http://127.0.0.1:{a.port}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
