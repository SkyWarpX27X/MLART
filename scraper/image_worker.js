// In-browser image feature worker. Paste into the console of a christies.com auction page
// (those pages have no CSP) while `python images.py serve` runs. It runs in a Web Worker so
// background-tab timer throttling doesn't stall it. Status: window.__imgw; stop: __imgw.stop().
(() => {
  const COLLECTOR = "http://127.0.0.1:8765";
  const PACE_MS = 300;      // >= time per image, incl. download
  const BATCH = 50;

  const workerSrc = `
const COLLECTOR = ${JSON.stringify(COLLECTOR)}, PACE_MS = ${PACE_MS}, BATCH = ${BATCH};
const sleep = ms => new Promise(r => setTimeout(r, ms));
let running = true;
onmessage = e => { if (e.data === "stop") running = false; };

const N = 128, P = 32;
const COS = new Float32Array(8 * P);  // DCT basis for pHash
for (let u = 0; u < 8; u++) for (let x = 0; x < P; x++) COS[u * P + x] = Math.cos((2 * x + 1) * u * Math.PI / (2 * P));
const rgb565 = (r, g, b) => ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
const u8 = v => Math.max(0, Math.min(255, Math.round(v)));

function features(bmp) {
  const c = new OffscreenCanvas(N, N), g = c.getContext("2d", { willReadFrequently: true });
  g.drawImage(bmp, 0, 0, N, N);
  const d = g.getImageData(0, 0, N, N).data, n = N * N;
  const L = new Float32Array(n), hist = new Uint32Array(256), bins = new Map();
  let sL = 0, sL2 = 0, sRG = 0, sRG2 = 0, sYB = 0, sYB2 = 0, sS = 0, sR = 0, sB = 0, white = 0;
  for (let i = 0; i < n; i++) {
    const r = d[4 * i], gg = d[4 * i + 1], b = d[4 * i + 2];
    const l = 0.299 * r + 0.587 * gg + 0.114 * b, mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
    const s = mx ? (mx - mn) / mx : 0, rg = r - gg, yb = 0.5 * (r + gg) - b;
    L[i] = l; hist[l | 0]++;
    sL += l; sL2 += l * l; sRG += rg; sRG2 += rg * rg; sYB += yb; sYB2 += yb * yb;
    sS += s; sR += r; sB += b;
    if (l > 225 && s < 0.1) white++;
    const k = ((r >> 5) << 6) | ((gg >> 5) << 3) | (b >> 5);  // 3 bits/channel palette bins
    const e = bins.get(k) || [0, 0, 0, 0];
    e[0]++; e[1] += r; e[2] += gg; e[3] += b; bins.set(k, e);
  }
  const mL = sL / n, mRG = sRG / n, mYB = sYB / n;
  const sdRG = Math.sqrt(sRG2 / n - mRG * mRG), sdYB = Math.sqrt(sYB2 / n - mYB * mYB);
  let ent = 0;
  for (const h of hist) if (h) { const p = h / n; ent -= p * Math.log2(p); }
  let edges = 0;  // Sobel on luminance
  for (let y = 1; y < N - 1; y++) for (let x = 1; x < N - 1; x++) {
    const i = y * N + x;
    const gx = L[i - N + 1] + 2 * L[i + 1] + L[i + N + 1] - L[i - N - 1] - 2 * L[i - 1] - L[i + N - 1];
    const gy = L[i + N - 1] + 2 * L[i + N] + L[i + N + 1] - L[i - N - 1] - 2 * L[i - N] - L[i - N + 1];
    if (gx * gx + gy * gy > 100 * 100) edges++;
  }
  const palette = [...bins.values()].sort((a, b) => b[0] - a[0]).slice(0, 3)
    .map(e => rgb565(e[1] / e[0] | 0, e[2] / e[0] | 0, e[3] / e[0] | 0));
  while (palette.length < 3) palette.push(palette[0] || 0);

  // pHash: 32x32 grey, low 8x8 DCT, bit = coefficient > median (DC excluded from median)
  const g2 = new OffscreenCanvas(P, P).getContext("2d", { willReadFrequently: true });
  g2.drawImage(bmp, 0, 0, P, P);
  const q = g2.getImageData(0, 0, P, P).data, G = new Float32Array(P * P);
  for (let i = 0; i < P * P; i++) G[i] = 0.299 * q[4 * i] + 0.587 * q[4 * i + 1] + 0.114 * q[4 * i + 2];
  const dct = new Float32Array(64);
  for (let u = 0; u < 8; u++) for (let v = 0; v < 8; v++) {
    let s = 0;
    for (let y = 0; y < P; y++) { const cy = COS[v * P + y]; for (let x = 0; x < P; x++) s += G[y * P + x] * COS[u * P + x] * cy; }
    dct[u * 8 + v] = s;
  }
  const med = [...dct.slice(1)].sort((a, b) => a - b)[31];
  let hi = 0, lo = 0;
  for (let i = 0; i < 64; i++) if (dct[i] > med) { if (i < 32) lo |= 1 << i; else hi |= 1 << (i - 32); }
  const phash = (hi >>> 0).toString(16).padStart(8, "0") + (lo >>> 0).toString(16).padStart(8, "0");

  return {
    phash, palette, w: bmp.width, h: bmp.height,
    lightness: u8(mL), contrast: u8(Math.sqrt(sL2 / n - mL * mL) * 2),
    colourfulness: u8(Math.hypot(sdRG, sdYB) + 0.3 * Math.hypot(mRG, mYB)),
    saturation: u8(sS / n * 255), warmth: u8((sR - sB) / n / 2 + 128),
    entropy: u8(ent / 8 * 255), white: u8(white / n * 255), edges: u8(edges / n * 255),
  };
}

async function fetchTimeout(url, opts = {}, ms = 30000) {
  const ac = new AbortController(), t = setTimeout(() => ac.abort(), ms);
  try { return await fetch(url, { ...opts, signal: ac.signal }); } finally { clearTimeout(t); }
}

(async () => {
  let fails = 0;
  while (running) {
    let todo;
    try { todo = await (await fetchTimeout(COLLECTOR + "/todo?n=" + BATCH)).json(); }
    catch (e) { postMessage({ type: "error", msg: "collector unreachable: " + e }); await sleep(30000); continue; }
    if (!todo.length) break;
    const out = [];
    for (const t of todo) {
      if (!running) break;
      const t0 = performance.now();
      try {
        const r = await fetchTimeout(t.url + "&w=256");
        if (!r.ok) throw new Error("HTTP " + r.status);
        const bmp = await createImageBitmap(await r.blob());
        out.push({ id: t.id, ...features(bmp) });
        bmp.close(); fails = 0;
        postMessage({ type: "ok" });
      } catch (e) {
        out.push({ id: t.id, err: String(e) });
        if (!String(e).includes("HTTP 404")) fails++;  // 404 = image missing on Christie's side, not a block
        postMessage({ type: "err", msg: String(e) });
        if (fails >= 10) { postMessage({ type: "error", msg: "10 failures in a row, pausing 2 min" }); await sleep(120000); fails = 0; }
      }
      const wait = PACE_MS - (performance.now() - t0);
      if (wait > 0) await sleep(wait);
    }
    if (out.length) {
      try { await fetchTimeout(COLLECTOR + "/done", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(out) }); }
      catch (e) { postMessage({ type: "error", msg: "could not post results: " + e }); }  // ids get re-leased after 10 min
    }
  }
  postMessage({ type: "finished" });
})();
`;
  const w = new Worker(URL.createObjectURL(new Blob([workerSrc], { type: "text/javascript" })));
  const st = window.__imgw = { done: 0, err: 0, lastError: null, finished: false, started: new Date().toISOString(),
    stop: () => w.postMessage("stop") };
  w.onmessage = e => {
    const m = e.data;
    if (m.type === "ok") st.done++;
    else if (m.type === "err") { st.err++; st.lastError = m.msg; }
    else if (m.type === "error") st.lastError = m.msg;
    else if (m.type === "finished") st.finished = true;
  };
  w.onerror = e => { st.lastError = "worker error: " + e.message; };
  return "image worker started";
})();
