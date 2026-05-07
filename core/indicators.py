"""
Technical Indicators Module
============================
Computes technical indicators from raw OHLCV candle data using pandas-ta.

Indicators computed:
  - EMA 20 / EMA 50 (trend direction)
  - RSI 14 (momentum)
  - Bollinger Bands 20/2 (volatility envelope)
  - MACD 12/26/9 (trend momentum)
  - ATR 14 (volatility measure)
  - Volume SMA 20 (volume baseline)

Derived fields:
  - trend: bullish | bearish | neutral  (EMA 20 vs EMA 50)
  - volatility: high | normal | low      (ATR vs 20-period ATR average)
  - volume_surge: bool                   (volume vs 1.5x volume SMA 20)
"""

import math
from typing import Optional

import pandas as pd
import pandas_ta as ta


def _safe_round(value, decimals: int = 4) -> Optional[float]:
    """Round a value to *decimals* places; return None if NaN / None."""
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except (TypeError, ValueError):
        return None
    return round(float(value), decimals)


def _classify_trend(ema_20: Optional[float], ema_50: Optional[float]) -> str:
    """Classify trend based on EMA crossover."""
    if ema_20 is None or ema_50 is None:
        return "neutral"
    if ema_20 > ema_50:
        return "bullish"
    elif ema_20 < ema_50:
        return "bearish"
    return "neutral"


def _classify_volatility(atr_current: Optional[float], atr_series: pd.Series) -> str:
    """Classify volatility by comparing current ATR to its 20-period average."""
    if atr_current is None:
        return "normal"

    atr_avg_20 = atr_series.rolling(window=20).mean().iloc[-1]
    if math.isnan(atr_avg_20):
        return "normal"

    if atr_current > 1.5 * atr_avg_20:
        return "high"
    elif atr_current < 0.7 * atr_avg_20:
        return "low"
    return "normal"


def _detect_volume_surge(
    volume: Optional[float], volume_sma20: Optional[float]
) -> bool:
    """Return True if current volume exceeds 1.5× volume SMA 20."""
    if volume is None or volume_sma20 is None or volume_sma20 == 0:
        return False
    return volume > 1.5 * volume_sma20


def _classify_volume_trend(volume_series: pd.Series, lookback: int = 5) -> str:
    """Classify recent volume direction over *lookback* periods."""
    if len(volume_series) < lookback:
        return "neutral"
    recent = volume_series.tail(lookback).dropna()
    if len(recent) < 2:
        return "neutral"
    # Simple linear slope check
    first_half = recent.iloc[: len(recent) // 2].mean()
    second_half = recent.iloc[len(recent) // 2 :].mean()
    if first_half == 0:
        return "neutral"
    change_pct = (second_half - first_half) / first_half
    if change_pct > 0.15:
        return "increasing"
    elif change_pct < -0.15:
        return "decreasing"
    return "neutral"


def compute_indicators(candles: list) -> dict:
    """Compute technical indicators from raw OHLCV candle data.

    Parameters
    ----------
    candles : list[dict]
        Each dict must contain: timestamp, open, high, low, close, volume.

    Returns
    -------
    dict
        Latest bar's indicator snapshot.  All float values rounded to 4
        decimal places; NaN values replaced with None.
    """
    # --- build DataFrame ------------------------------------------------
    df = pd.DataFrame(candles)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # --- compute indicators using pandas-ta -----------------------------
    # Exponential Moving Averages
    df["ema_20"] = ta.ema(df["close"], length=20)
    df["ema_50"] = ta.ema(df["close"], length=50)

    # Relative Strength Index
    df["rsi"] = ta.rsi(df["close"], length=14)

    # Bollinger Bands (20, 2)
    bbands = ta.bbands(df["close"], length=20, std=2)
    df["bb_lower"] = bbands.iloc[:, 0]   # BBL
    df["bb_mid"] = bbands.iloc[:, 1]     # BBM
    df["bb_upper"] = bbands.iloc[:, 2]   # BBU

    # MACD (12, 26, 9)
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["macd_line"] = macd.iloc[:, 0]     # MACD line
    df["macd_signal"] = macd.iloc[:, 2]   # Signal line
    df["macd_hist"] = macd.iloc[:, 1]     # Histogram

    # Average True Range
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # Average Directional Index (ADX) — measures trend strength
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
    if adx_df is not None and len(adx_df.columns) > 0:
        df["adx"] = adx_df.iloc[:, 0]  # ADX value (0-100)
    else:
        df["adx"] = float("nan")

    # EMA 200 — long-term trend anchor
    df["ema_200"] = ta.ema(df["close"], length=200)

    # Volume SMA
    df["volume_sma20"] = ta.sma(df["volume"], length=20)

    # --- extract latest row ---------------------------------------------
    latest = df.iloc[-1]

    ema_20 = _safe_round(latest["ema_20"])
    ema_50 = _safe_round(latest["ema_50"])
    rsi = _safe_round(latest["rsi"])
    bb_upper = _safe_round(latest["bb_upper"])
    bb_mid = _safe_round(latest["bb_mid"])
    bb_lower = _safe_round(latest["bb_lower"])
    macd_line = _safe_round(latest["macd_line"])
    macd_signal = _safe_round(latest["macd_signal"])
    macd_hist = _safe_round(latest["macd_hist"])
    atr = _safe_round(latest["atr"])
    volume = _safe_round(latest["volume"])
    volume_sma20 = _safe_round(latest["volume_sma20"])

    adx = _safe_round(latest.get("adx"))
    ema_200 = _safe_round(latest.get("ema_200"))

    # --- derived fields --------------------------------------------------
    trend = _classify_trend(ema_20, ema_50)
    volatility = _classify_volatility(
        atr, df["atr"]
    )
    volume_surge = _detect_volume_surge(volume, volume_sma20)
    volume_trend = _classify_volume_trend(df["volume"])

    return {
        "timestamp": latest["timestamp"],
        "close": _safe_round(latest["close"]),
        "ema_20": ema_20,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "rsi": rsi,
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "atr": atr,
        "adx": adx,
        "volume": volume,
        "volume_sma20": volume_sma20,
        "trend": trend,
        "volatility": volatility,
        "volume_surge": volume_surge,
        "volume_trend": volume_trend,
    }


def compute_mtf_indicators(candles: list) -> dict:
    """Compute a lighter set of indicators for higher-timeframe candles.

    Used by the regime and MTF confluence agents to evaluate 1H, 4H, 1D data.

    Parameters
    ----------
    candles : list[dict]
        OHLCV candle dicts.

    Returns
    -------
    dict
        Indicator snapshot: EMAs, RSI, ADX, ATR, trend, structure.
    """
    if not candles or len(candles) < 50:
        return {
            "close": None, "ema_20": None, "ema_50": None, "ema_200": None,
            "rsi": None, "adx": None, "atr": None, "macd_hist": None,
            "trend": "neutral", "volume_trend": "neutral",
        }

    df = pd.DataFrame(candles)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Core EMAs
    df["ema_20"] = ta.ema(df["close"], length=20)
    df["ema_50"] = ta.ema(df["close"], length=50)
    df["ema_200"] = ta.ema(df["close"], length=200) if len(df) >= 200 else None

    # Momentum
    df["rsi"] = ta.rsi(df["close"], length=14)

    # MACD for momentum confirmation
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is not None and len(macd.columns) >= 2:
        df["macd_hist"] = macd.iloc[:, 1]
    else:
        df["macd_hist"] = float("nan")

    # Trend strength
    adx_df = ta.adx(df["high"], df["low"], df["close"], length=14)
    if adx_df is not None and len(adx_df.columns) > 0:
        df["adx"] = adx_df.iloc[:, 0]
    else:
        df["adx"] = float("nan")

    # Volatility
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    latest = df.iloc[-1]

    ema_20 = _safe_round(latest.get("ema_20"))
    ema_50 = _safe_round(latest.get("ema_50"))
    ema_200 = _safe_round(latest.get("ema_200")) if "ema_200" in latest.index else None

    return {
        "close": _safe_round(latest["close"]),
        "ema_20": ema_20,
        "ema_50": ema_50,
        "ema_200": ema_200,
        "rsi": _safe_round(latest.get("rsi")),
        "adx": _safe_round(latest.get("adx")),
        "atr": _safe_round(latest.get("atr")),
        "macd_hist": _safe_round(latest.get("macd_hist")),
        "trend": _classify_trend(ema_20, ema_50),
        "volume_trend": _classify_volume_trend(df["volume"]),
    }


def compute_vwap_and_poc(candles: list) -> dict:
    """Compute Volume Weighted Average Price (VWAP) and Point of Control (POC).

    Parameters
    ----------
    candles : list[dict]
        OHLCV candle dicts. Usually the last 24h of data (e.g., 288 5m candles).

    Returns
    -------
    dict
        ``{"vwap": float, "poc_price": float}``
    """
    if not candles:
        return {"vwap": 0.0, "poc_price": 0.0}

    df = pd.DataFrame(candles)
    for col in ("high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    
    # VWAP = sum(Typical Price * Volume) / sum(Volume)
    typical_price = (df["high"] + df["low"] + df["close"]) / 3
    cum_vol_price = (typical_price * df["volume"]).sum()
    cum_vol = df["volume"].sum()
    vwap = cum_vol_price / cum_vol if cum_vol > 0 else 0.0

    # POC (Point of Control) - Price level with highest volume
    # Simplify by binning close prices
    poc_price = 0.0
    if len(df) > 0 and cum_vol > 0:
        # Create ~50 bins for price distribution
        min_p, max_p = df["low"].min(), df["high"].max()
        if max_p > min_p:
            bins = pd.cut((df["high"] + df["low"]) / 2, bins=50)
            vol_by_price = df.groupby(bins, observed=False)["volume"].sum()
            poc_interval = vol_by_price.idxmax()
            poc_price = poc_interval.mid if pd.notna(poc_interval) else 0.0
        else:
            poc_price = df["close"].iloc[-1]

    return {
        "vwap": round(float(vwap), 2),
        "poc_price": round(float(poc_price), 2),
    }
