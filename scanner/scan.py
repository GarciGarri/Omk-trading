#!/usr/bin/env python3
"""OMK Señales: escáner de reglas técnicas.

Descarga velas, calcula indicadores, evalúa las reglas, resuelve el resultado
de cada señal (TP o SL primero) y genera un documento por activo para la base
de datos del artefacto.

Uso:
  python3 scanner/scan.py --prev <dir con markets/*.json previos> --out <dir>

Salida en --out:
  markets/<ID>.json   documento completo por activo (velas + señales)
  status.json         resumen de la ejecución
  alerts.json         señales EN VIVO nuevas desde la ejecución anterior
  alerts.md           mismo contenido en texto para la notificación
"""
import argparse, json, math, os, sys, time, urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
UA = {"User-Agent": "omk-scanner/1.0"}
TF_SECONDS = {"4h": 4 * 3600, "1d": 86400}
CHART_BARS = 160

RULES = {
    "ema_cross": {
        "name": "Cruce EMA 20/50 a favor de tendencia",
        "desc": "La EMA 20 cruza la EMA 50 en la misma dirección que marca la EMA 200.",
    },
    "rsi_pullback": {
        "name": "Retroceso RSI en tendencia",
        "desc": "Con precio sobre la EMA 200, el RSI 14 sale de sobreventa cruzando 35 al alza (compra). Bajo la EMA 200, cruza 65 a la baja (venta).",
    },
    "breakout": {
        "name": "Ruptura de 20 velas con volumen",
        "desc": "Cierre por encima del máximo (o debajo del mínimo) de las 20 velas anteriores, con volumen 1,5× la media y EMA 50 alineada con la EMA 200.",
    },
}


def http_json(url):
    req = urllib.request.Request(url, headers=UA)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception as e:  # noqa: BLE001
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def fetch_binance(symbol, tf):
    url = f"https://data-api.binance.vision/api/v3/klines?symbol={symbol}&interval={tf}&limit=1000"
    rows = http_json(url)
    now_ms = time.time() * 1000
    out = []
    for k in rows:
        if k[6] > now_ms:  # vela aún abierta
            continue
        out.append({"t": int(k[0] // 1000), "o": float(k[1]), "h": float(k[2]),
                    "l": float(k[3]), "c": float(k[4]), "v": float(k[5])})
    return out


def fetch_yahoo(symbol, tf):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}?interval=1d&range=5y"
    res = http_json(url)["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    now = time.time()
    out = []
    for i, t in enumerate(res["timestamp"]):
        o, h, l, c, v = (q[k][i] for k in ("open", "high", "low", "close", "volume"))
        if None in (o, h, l, c):
            continue
        out.append({"t": int(t), "o": o, "h": h, "l": l, "c": c, "v": float(v or 0)})
    # quitar la vela de la sesión en curso: solo usamos velas cerradas
    reg = (res.get("meta", {}).get("currentTradingPeriod") or {}).get("regular") or {}
    if out and reg.get("start") and reg.get("end"):
        if reg["start"] <= now < reg["end"] and out[-1]["t"] >= reg["start"] - 3600:
            out.pop()
    elif out and now - out[-1]["t"] < 86400:
        out.pop()
    return out


# ---------- indicadores ----------
def ema(vals, n):
    k = 2 / (n + 1)
    out, prev = [], None
    for i, x in enumerate(vals):
        if i < n - 1:
            out.append(None)
            continue
        if prev is None:
            prev = sum(vals[i - n + 1:i + 1]) / n
        else:
            prev = x * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(closes, n=14):
    out = [None] * len(closes)
    gain = loss = 0.0
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        g, l = max(d, 0), max(-d, 0)
        if i <= n:
            gain += g; loss += l
            if i == n:
                gain /= n; loss /= n
                out[i] = 100 - 100 / (1 + gain / loss) if loss else 100
        else:
            gain = (gain * (n - 1) + g) / n
            loss = (loss * (n - 1) + l) / n
            out[i] = 100 - 100 / (1 + gain / loss) if loss else 100
    return out


def atr(c, n=14):
    out = [None] * len(c)
    trs = []
    a = None
    for i in range(len(c)):
        tr = c[i]["h"] - c[i]["l"] if i == 0 else max(
            c[i]["h"] - c[i]["l"], abs(c[i]["h"] - c[i - 1]["c"]), abs(c[i]["l"] - c[i - 1]["c"]))
        trs.append(tr)
        if i == n - 1:
            a = sum(trs) / n
        elif i >= n:
            a = (a * (n - 1) + tr) / n
        out[i] = a
    return out


def sma(vals, n):
    out = [None] * len(vals)
    s = 0
    for i, x in enumerate(vals):
        s += x
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


# ---------- reglas ----------
def evaluate(c, ind, i):
    """Devuelve lista de (rule_id, side, motivo) para la vela cerrada i."""
    e20, e50, e200, r, vavg = ind["e20"], ind["e50"], ind["e200"], ind["rsi"], ind["vavg"]
    if None in (e20[i], e50[i], e200[i], e20[i - 1], e50[i - 1], r[i], r[i - 1]):
        return []
    close = c[i]["c"]
    out = []
    # 1) cruce EMA 20/50 a favor de la EMA 200
    if e20[i - 1] <= e50[i - 1] and e20[i] > e50[i] and close > e200[i]:
        out.append(("ema_cross", "buy", "EMA 20 cruza al alza la EMA 50 con el precio sobre la EMA 200"))
    if e20[i - 1] >= e50[i - 1] and e20[i] < e50[i] and close < e200[i]:
        out.append(("ema_cross", "sell", "EMA 20 cruza a la baja la EMA 50 con el precio bajo la EMA 200"))
    # 2) retroceso RSI en tendencia
    if close > e200[i] and r[i - 1] < 35 <= r[i]:
        out.append(("rsi_pullback", "buy", f"RSI sale de sobreventa ({r[i - 1]:.0f} → {r[i]:.0f}) en tendencia alcista"))
    if close < e200[i] and r[i - 1] > 65 >= r[i]:
        out.append(("rsi_pullback", "sell", f"RSI sale de sobrecompra ({r[i - 1]:.0f} → {r[i]:.0f}) en tendencia bajista"))
    # 3) ruptura de 20 velas con volumen
    if i >= 21:
        hh = max(x["h"] for x in c[i - 20:i])
        ll = min(x["l"] for x in c[i - 20:i])
        has_vol = vavg[i] and vavg[i] > 0
        vol_ok = (not has_vol) or c[i]["v"] > 1.5 * vavg[i]
        vtxt = f" y volumen {c[i]['v'] / vavg[i]:.1f}× la media" if has_vol else ""
        if close > hh and vol_ok and e50[i] > e200[i]:
            out.append(("breakout", "buy", f"Cierre sobre el máximo de 20 velas ({hh:.6g}){vtxt}"))
        if close < ll and vol_ok and e50[i] < e200[i]:
            out.append(("breakout", "sell", f"Cierre bajo el mínimo de 20 velas ({ll:.6g}){vtxt}"))
    return out


def resolve(sig, c, i, risk):
    """Recorre las velas posteriores y decide si tocó antes TP o SL."""
    d = 1 if sig["side"] == "buy" else -1
    r_unit = abs(sig["entry"] - sig["sl"])
    last = min(len(c) - 1, i + risk["expiry_bars"])
    for j in range(i + 1, last + 1):
        hit_sl = c[j]["l"] <= sig["sl"] if d == 1 else c[j]["h"] >= sig["sl"]
        hit_tp = c[j]["h"] >= sig["tp"] if d == 1 else c[j]["l"] <= sig["tp"]
        if hit_sl:  # si toca ambos en la misma vela, contamos SL (conservador)
            return dict(status="loss", r=-1.0, exit=sig["sl"], closed_t=c[j]["t"], bars=j - i)
        if hit_tp:
            rr = abs(sig["tp"] - sig["entry"]) / r_unit
            return dict(status="win", r=round(rr, 2), exit=sig["tp"], closed_t=c[j]["t"], bars=j - i)
    cur = c[last]["c"]
    r_now = round(d * (cur - sig["entry"]) / r_unit, 2)
    if last == i + risk["expiry_bars"]:
        return dict(status="expired", r=r_now, exit=cur, closed_t=c[last]["t"], bars=last - i)
    return dict(status="open", r=r_now, exit=None, closed_t=None, bars=last - i, price_now=cur)


def rnd(x):
    if x is None:
        return None
    if x == 0:
        return 0
    digits = max(2, 6 - int(math.floor(math.log10(abs(x)))) - 1)
    return round(x, digits)


def scan_asset(asset, cfg, prev_doc):
    risk = cfg["risk"]
    live_since = datetime.fromisoformat(cfg["live_since"].replace("Z", "+00:00")).timestamp()
    tf_s = TF_SECONDS[asset["tf"]]
    c = fetch_binance(asset["symbol"], asset["tf"]) if asset["source"] == "binance" else fetch_yahoo(asset["symbol"], asset["tf"])
    if len(c) < 260:
        raise RuntimeError(f"{asset['id']}: solo {len(c)} velas")
    closes = [x["c"] for x in c]
    ind = {"e20": ema(closes, 20), "e50": ema(closes, 50), "e200": ema(closes, 200),
           "rsi": rsi(closes), "atr": atr(c, risk["atr_period"]),
           "vavg": sma([x["v"] for x in c], 20)}
    # evaluamos solo el último tramo (~1 año en diario, ~5 meses en 4h)
    start = max(201, len(c) - (260 if asset["tf"] == "1d" else 900))
    signals, open_until = [], {}
    for i in range(start, len(c)):
        for rule, side, why in evaluate(c, ind, i):
            key = rule
            if open_until.get(key, -1) >= i:
                continue  # misma regla con una señal todavía viva
            a = ind["atr"][i]
            d = 1 if side == "buy" else -1
            entry = c[i]["c"]
            sig = {
                "id": f"{asset['id']}-{rule}-{side}-{c[i]['t']}",
                "asset": asset["id"], "rule": rule, "side": side,
                "t": c[i]["t"] + tf_s,  # momento en que cierra la vela de la señal
                "entry": rnd(entry), "sl": rnd(entry - d * risk["sl_atr"] * a),
                "tp": rnd(entry + d * risk["tp_atr"] * a), "atr": rnd(a),
                "rsi": round(ind["rsi"][i], 1), "why": why,
            }
            res = resolve(sig, c, i, risk)
            sig.update({k: (rnd(v) if k in ("exit", "price_now") else v) for k, v in res.items()})
            sig["source"] = "live" if sig["t"] >= live_since else "backtest"
            signals.append(sig)
            open_until[key] = i + res["bars"] if res["status"] != "open" else len(c) + 1
    # conservar señales antiguas que ya salieron de la ventana recalculada
    new_ids = {s["id"] for s in signals}
    old = (prev_doc or {}).get("signals", [])
    kept = [s for s in old if s["id"] not in new_ids and s["t"] < signals[0]["t"]] if signals else old
    all_sigs = sorted(kept + signals, key=lambda s: s["t"])
    n = len(c) - 1
    tail = c[-CHART_BARS:]
    off = len(c) - len(tail)
    doc = {
        "id": asset["id"], "name": asset["name"], "class": asset["class"], "tf": asset["tf"],
        "symbol": asset["symbol"], "source": asset["source"],
        "updated": int(time.time()), "last_bar": c[-1]["t"] + tf_s,
        "price": rnd(c[-1]["c"]), "change": round((c[-1]["c"] / c[-2]["c"] - 1) * 100, 2),
        "rsi": round(ind["rsi"][n], 1), "atr": rnd(ind["atr"][n]),
        "ema20": rnd(ind["e20"][n]), "ema50": rnd(ind["e50"][n]), "ema200": rnd(ind["e200"][n]),
        "trend": "alcista" if c[-1]["c"] > ind["e200"][n] and ind["e50"][n] > ind["e200"][n]
                 else "bajista" if c[-1]["c"] < ind["e200"][n] and ind["e50"][n] < ind["e200"][n] else "lateral",
        "candles": [[x["t"], rnd(x["o"]), rnd(x["h"]), rnd(x["l"]), rnd(x["c"])] for x in tail],
        "ema": {k: [rnd(v) for v in ind[k][off:]] for k in ("e20", "e50", "e200")},
        "signals": all_sigs,
    }
    prev_ids = {s["id"] for s in old}
    fresh = [s for s in signals if s["source"] == "live" and s["id"] not in prev_ids]
    return doc, fresh


def fmt_alert(s, doc):
    side = "COMPRA" if s["side"] == "buy" else "VENTA"
    return (f"{side} {doc['name']} ({doc['tf']}) · {RULES[s['rule']]['name']}\n"
            f"  Entrada {s['entry']} · Stop {s['sl']} · Objetivo {s['tp']} · Estado: {s['status']}\n"
            f"  {s['why']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prev", help="directorio con markets/<ID>.json de la ejecución anterior")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    cfg = json.load(open(os.path.join(HERE, "config.json")))
    os.makedirs(os.path.join(args.out, "markets"), exist_ok=True)
    alerts, errors, summary = [], [], []
    for asset in cfg["assets"]:
        prev = None
        if args.prev:
            for cand in (os.path.join(args.prev, "markets", asset["id"] + ".json"),
                         os.path.join(args.prev, asset["id"] + ".json")):
                if os.path.exists(cand):
                    prev = json.load(open(cand))
                    prev = prev.get("data", prev)  # admite volcados con envoltorio
                    break
        try:
            doc, fresh = scan_asset(asset, cfg, prev)
        except Exception as e:  # noqa: BLE001
            errors.append(f"{asset['id']}: {e}")
            print("ERROR", asset["id"], e, file=sys.stderr)
            continue
        with open(os.path.join(args.out, "markets", asset["id"] + ".json"), "w") as f:
            json.dump(doc, f, separators=(",", ":"))
        alerts += [(s, doc) for s in fresh]
        st = {}
        for s in doc["signals"]:
            st[s["status"]] = st.get(s["status"], 0) + 1
        summary.append(f"{asset['id']:8} {doc['price']:>12} {doc['trend']:8} RSI {doc['rsi']:5}  señales {st}")
    status = {"last_run": int(time.time()), "errors": errors, "new_alerts": [s["id"] for s, _ in alerts],
              "rules": RULES, "risk": cfg["risk"], "live_since": cfg["live_since"]}
    json.dump(status, open(os.path.join(args.out, "status.json"), "w"), ensure_ascii=False)
    json.dump([s for s, _ in alerts], open(os.path.join(args.out, "alerts.json"), "w"), ensure_ascii=False)
    with open(os.path.join(args.out, "alerts.md"), "w") as f:
        f.write("\n\n".join(fmt_alert(s, d) for s, d in alerts) or "Sin avisos nuevos.")
    print("\n".join(summary))
    print(f"\nAvisos nuevos: {len(alerts)}  Errores: {len(errors)}")
    for s, d in alerts:
        print(fmt_alert(s, d))


if __name__ == "__main__":
    main()
