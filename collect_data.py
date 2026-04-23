"""
데이터 수집 전용 스크립트 (Claude API 없음 — GitHub Actions에서 실행)
결과를 data.json으로 저장.
"""
from __future__ import annotations
import json, warnings
warnings.filterwarnings("ignore")
from datetime import datetime
from pathlib import Path

import requests
import numpy as np
import pandas as pd

try:
    import pandas_ta as ta
except ImportError:
    import sys; sys.exit("[오류] pandas-ta 없음: pip install pandas-ta")

try:
    import yfinance as yf
except ImportError:
    yf = None

BINANCE_URL = "https://api.binance.com"
FNG_URL     = "https://api.alternative.me/fng/"
TIMEOUT     = 15

COINS = [
    ("ETHUSDT",  "ETH"),
    ("BTCUSDT",  "BTC"),
    ("DOGEUSDT", "DOGE"),
    ("SOLUSDT",  "SOL"),
    ("XRPUSDT",  "XRP"),
]


# ── 데이터 수집 ───────────────────────────────────────────────────────────────

def fetch_klines(symbol: str, limit: int = 730) -> pd.DataFrame:
    resp = requests.get(f"{BINANCE_URL}/api/v3/klines",
                        params={"symbol": symbol, "interval": "1d", "limit": limit},
                        timeout=TIMEOUT)
    resp.raise_for_status()
    cols = ["t","open","high","low","close","volume","ct","qv","n","tbv","tqv","ig"]
    df = pd.DataFrame(resp.json(), columns=cols)
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True).dt.normalize()
    return df[["open","high","low","close","volume"]].astype(float)

def fetch_fear_greed(limit: int = 730) -> pd.Series:
    resp = requests.get(FNG_URL, params={"limit": limit}, timeout=TIMEOUT)
    resp.raise_for_status()
    records = {}
    for d in resp.json().get("data", []):
        ts = pd.Timestamp(int(d["timestamp"]), unit="s", tz="UTC").normalize()
        records[ts] = int(d["value"])
    return pd.Series(records).sort_index() if records else pd.Series(dtype=int)

def fetch_macro_raw(ticker: str) -> pd.Series:
    if yf is None: return pd.Series(dtype=float)
    try:
        h = yf.Ticker(ticker).history(period="2y")
        if h.empty: return pd.Series(dtype=float)
        s = h["Close"].copy()
        s.index = s.index.tz_convert("UTC").normalize()
        return s.sort_index()
    except Exception:
        return pd.Series(dtype=float)

def align(series: pd.Series, index: pd.DatetimeIndex) -> pd.Series:
    if series.empty: return pd.Series(np.nan, index=index)
    return series.reindex(index, method="ffill")


# ── 지표 계산 ─────────────────────────────────────────────────────────────────

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["rsi"] = ta.rsi(df["close"], length=14)
    macd_df  = ta.macd(df["close"], fast=12, slow=26, signal=9)
    hist_col = next((c for c in macd_df.columns if c.startswith("MACDh_")), None)
    df["macd_hist"] = macd_df[hist_col] if hist_col else np.nan
    bb        = ta.bbands(df["close"], length=20, std=2.0)
    pct_b_col = next((c for c in bb.columns if c.startswith("BBP_")), None)
    df["bb_pct_b"] = bb[pct_b_col] if pct_b_col else np.nan
    df["vol_change_pct"] = df["volume"].pct_change() * 100
    return df.dropna()

def btc_dom_series(df_coin: pd.DataFrame, df_btc: pd.DataFrame) -> pd.Series:
    coin_ret = df_coin["close"].pct_change(7)
    btc_ret  = df_btc["close"].reindex(df_coin.index, method="ffill").pct_change(7)
    return pd.Series(np.where(btc_ret > coin_ret, "up", "down"), index=df_coin.index)


# ── 유사일 탐색 ───────────────────────────────────────────────────────────────

def _rsi_zone(v):
    return "oversold" if v<30 else ("low" if v<45 else ("neutral" if v<55 else ("high" if v<70 else "overbought")))
def _bb_zone(v):
    return "below_lower" if v<0 else ("near_lower" if v<0.2 else ("middle" if v<0.8 else ("near_upper" if v<1 else "above_upper")))
def _vol_zone(v):
    return "very_low" if v<-20 else ("low" if v<-5 else ("flat" if v<5 else ("high" if v<20 else "very_high")))
def _fg_zone(v):
    return "extreme_fear" if v<25 else ("fear" if v<45 else ("neutral" if v<55 else ("greed" if v<75 else "extreme_greed")))
def _dir(series, idx):
    if series.empty: return "unknown"
    try:
        pos = min(series.index.searchsorted(idx), len(series)-1)
        if pos == 0: return "unknown"
        return "up" if series.iloc[pos] > series.iloc[pos-1] else "down"
    except: return "unknown"

def make_vec(row, fg_val, dxy_dir, nasdaq_dir, btc_dom):
    return {
        "rsi":    _rsi_zone(float(row["rsi"])),
        "macd":   "+" if float(row["macd_hist"]) > 0 else "-",
        "bb":     _bb_zone(float(row["bb_pct_b"])),
        "vol":    _vol_zone(float(row["vol_change_pct"])),
        "fg":     _fg_zone(float(fg_val)),
        "dxy":    dxy_dir,
        "nasdaq": nasdaq_dir,
        "btcdom": btc_dom,
    }

def find_similar_days_multi(df, fg, dxy, nasdaq, btcdom, today_vec, min_match=6):
    n = len(df)
    counts = {1: [0, 0], 7: [0, 0], 30: [0, 0]}
    for i in range(1, n - 30):
        row = df.iloc[i]; idx = df.index[i]
        fg_val = float(fg.get(idx, 50) if not fg.empty else 50)
        hist_vec = make_vec(row, fg_val, _dir(dxy, idx), _dir(nasdaq, idx),
                            str(btcdom.iloc[i]) if i < len(btcdom) else "unknown")
        if sum(today_vec.get(k) == hist_vec.get(k) for k in today_vec) < min_match:
            continue
        for h in [1, 7, 30]:
            if i + h < n:
                counts[h][1] += 1
                if df.iloc[i + h]["close"] > row["close"]:
                    counts[h][0] += 1
    probs = {h: round(v[0]/v[1]*100, 1) if v[1] > 0 else 50.0 for h, v in counts.items()}
    return probs, counts[1][1]


# ── 거래량 프로파일 + 매물대 ──────────────────────────────────────────────────

def volume_profile(df, n_bins=120):
    lo_min, hi_max = df["low"].min(), df["high"].max()
    bins = np.linspace(lo_min, hi_max, n_bins + 1)
    bw   = bins[1] - bins[0]
    vols = np.zeros(n_bins)
    for _, r in df.iterrows():
        s = max(0, int((r["low"] - lo_min) / bw))
        e = min(n_bins-1, int((r["high"] - lo_min) / bw))
        if e >= s: vols[s:e+1] += r["volume"] / (e - s + 1)
    levels = (bins[:-1] + bins[1:]) / 2
    total  = vols.sum()
    return pd.DataFrame({"price": levels, "vol": vols,
                         "pct": vols / total * 100 if total > 0 else 0.0})

def key_levels(profile, top_n=5):
    v, p = profile["vol"].values, profile["price"].values
    maxima = [(v[i], p[i]) for i in range(2, len(v)-2)
              if v[i]>v[i-1] and v[i]>v[i-2] and v[i]>v[i+1] and v[i]>v[i+2]]
    maxima.sort(reverse=True)
    return [float(p) for _, p in maxima[:top_n]]

def nearest_level(price, levels):
    if not levels: return price, 0.0
    n = min(levels, key=lambda x: abs(x - price))
    return round(n, 6), round(abs(price - n) / price * 100, 2)

def _combo(row):
    rsi = float(row["rsi"]); macd = float(row["macd_hist"]); bb = float(row["bb_pct_b"])
    r = "rsi_low" if rsi<45 else ("rsi_high" if rsi>55 else "rsi_mid")
    m = "macd_pos" if macd>0 else "macd_neg"
    b = "bb_low" if bb<0.3 else ("bb_high" if bb>0.7 else "bb_mid")
    return f"{r}+{m}+{b}"

def success_rates(df, target, proximity_pct=2.0, rise_pct=3.0, days_ahead=3, min_n=3):
    margin = target * proximity_pct / 100
    near   = df[(df["low"] <= target+margin) & (df["high"] >= target-margin)]
    stats: dict = {}
    for idx in near.index:
        try: pos = df.index.get_loc(idx)
        except KeyError: continue
        future = df.iloc[pos+1: pos+1+days_ahead]
        if future.empty: continue
        entry = float(df.iloc[pos]["close"])
        ok    = bool((future["high"] >= entry*(1+rise_pct/100)).any())
        c     = _combo(df.iloc[pos])
        if c not in stats: stats[c] = {"ok": 0, "n": 0}
        stats[c]["n"] += 1
        if ok: stats[c]["ok"] += 1
    out = [{"combo": k, "rate": round(v["ok"]/v["n"]*100,1), "n": v["n"]}
           for k, v in stats.items() if v["n"] >= min_n]
    out.sort(key=lambda x: x["rate"], reverse=True)
    return out


# ── 메인 ─────────────────────────────────────────────────────────────────────

def _bb_label(v):
    if v < 0:   return "아래이탈"
    if v < 0.2: return "하단"
    if v < 0.8: return "중간"
    if v < 1.0: return "상단"
    return "위이탈"

def collect_coin(symbol, df_btc_raw, fg, dxy_raw, nasdaq_raw):
    df_raw = fetch_klines(symbol, 730)
    df     = add_indicators(df_raw)
    btcdom = btc_dom_series(df, df_btc_raw)
    dxy    = align(dxy_raw, df.index)
    nasdaq = align(nasdaq_raw, df.index)
    today  = df.iloc[-1]
    price  = float(today["close"])
    fg_now = int(fg.iloc[-1]) if not fg.empty else 50
    today_vec = make_vec(today, fg_now,
                         _dir(dxy, df.index[-1]),
                         _dir(nasdaq, df.index[-1]),
                         str(btcdom.iloc[-1]))
    probs, similar_n = find_similar_days_multi(df, fg, dxy, nasdaq, btcdom, today_vec)
    profile  = volume_profile(df_raw)
    levels   = key_levels(profile)
    kl, dist = nearest_level(price, levels)
    combos   = success_rates(df, kl)
    return {
        "price":          round(price, 6),
        "rsi":            round(float(today["rsi"]), 1),
        "macd":           "+" if today["macd_hist"] > 0 else "-",
        "bb":             _bb_label(float(today["bb_pct_b"])),
        "prob_1d":        probs[1],
        "prob_7d":        probs[7],
        "prob_30d":       probs[30],
        "similar_n":      similar_n,
        "key_level":      round(kl, 6),
        "level_dist":     dist,
        "top_combo_rate": combos[0]["rate"] if combos else 0,
    }


def main():
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{ts}] 데이터 수집 시작")

    fg         = fetch_fear_greed(730)
    dxy_raw    = fetch_macro_raw("DX-Y.NYB")
    nasdaq_raw = fetch_macro_raw("^IXIC")
    df_btc_raw = fetch_klines("BTCUSDT", 730)

    fg_now = int(fg.iloc[-1]) if not fg.empty else 50
    dxy_ref = align(dxy_raw, df_btc_raw.index)
    nasdaq_ref = align(nasdaq_raw, df_btc_raw.index)

    coins: dict = {}
    for symbol, label in COINS:
        print(f"  {label}...", end=" ")
        try:
            coins[label] = collect_coin(symbol, df_btc_raw, fg, dxy_raw, nasdaq_raw)
            d = coins[label]
            print(f"가격:{d['price']} P1:{d['prob_1d']}% P7:{d['prob_7d']}% P30:{d['prob_30d']}%")
        except Exception as e:
            print(f"오류: {e}")

    btc_ref = df_btc_raw["close"]
    macro = {
        "fg":     fg_now,
        "dxy":    "up" if (not dxy_raw.empty and dxy_raw.iloc[-1] > dxy_raw.iloc[-2]) else "down",
        "nasdaq": "up" if (not nasdaq_raw.empty and nasdaq_raw.iloc[-1] > nasdaq_raw.iloc[-2]) else "down",
    }

    result = {"timestamp": ts, "coins": coins, "macro": macro}
    Path("data.json").write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"✓ data.json 저장 완료")


if __name__ == "__main__":
    main()
