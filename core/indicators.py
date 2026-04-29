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

    # --- derived fields --------------------------------------------------
    trend = _classify_trend(ema_20, ema_50)
    volatility = _classify_volatility(
        atr, df["atr"]
    )
    volume_surge = _detect_volume_surge(volume, volume_sma20)

    return {
        "timestamp": latest["timestamp"],
        "close": _safe_round(latest["close"]),
        "ema_20": ema_20,
        "ema_50": ema_50,
        "rsi": rsi,
        "bb_upper": bb_upper,
        "bb_mid": bb_mid,
        "bb_lower": bb_lower,
        "macd_line": macd_line,
        "macd_signal": macd_signal,
        "macd_hist": macd_hist,
        "atr": atr,
        "volume": volume,
        "volume_sma20": volume_sma20,
        "trend": trend,
        "volatility": volatility,
        "volume_surge": volume_surge,
    }
