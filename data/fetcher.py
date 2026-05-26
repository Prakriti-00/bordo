"""
data/fetcher.py — Rainfall data fetching
Open-Meteo API (free, no key) with 15-day forecast + synthetic fallback.
"""
import pandas as pd
import numpy as np
from datetime import date, datetime, timedelta
import json, os, hashlib

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")
os.makedirs(CACHE_DIR, exist_ok=True)

FORECAST_DAYS = 15   # updated from 7


def fetch_forecast(lat: float, lon: float, days: int = FORECAST_DAYS) -> pd.DataFrame:
    """
    Fetch hourly rainfall forecast from Open-Meteo (free, no API key).
    Falls back to synthetic data if unavailable.
    Returns DataFrame: [time, rain_mm, temp_c, humidity_pct, wind_kph].
    """
    try:
        import urllib.request
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}"
            f"&hourly=precipitation,temperature_2m,relative_humidity_2m,wind_speed_10m"
            f"&forecast_days={days}"
            f"&timezone=Asia%2FKolkata"
        )
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read())
        hourly = data["hourly"]
        df = pd.DataFrame({
            "time":         pd.to_datetime(hourly["time"]),
            "rain_mm":      hourly["precipitation"],
            "temp_c":       hourly["temperature_2m"],
            "humidity_pct": hourly["relative_humidity_2m"],
            "wind_kph":     hourly["wind_speed_10m"],
        })
        return df
    except Exception:
        return _synthetic_forecast(days)


def fetch_historical(lat: float, lon: float,
                     start: str = "2020-01-01",
                     end: str   = None) -> pd.DataFrame:
    """
    Fetch historical daily rainfall from Open-Meteo archive.
    Returns DataFrame: [date, rain_mm].
    """
    if end is None:
        end = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
    try:
        import urllib.request
        url = (
            f"https://archive-api.open-meteo.com/v1/archive"
            f"?latitude={lat}&longitude={lon}"
            f"&daily=precipitation_sum"
            f"&start_date={start}&end_date={end}"
            f"&timezone=Asia%2FKolkata"
        )
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
        daily = data["daily"]
        df = pd.DataFrame({
            "date":    pd.to_datetime(daily["time"]),
            "rain_mm": [x or 0 for x in daily["precipitation_sum"]],
        })
        return df
    except Exception:
        return _synthetic_historical(start, end)


def aggregate_forecast_daily(hourly_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate hourly forecast to daily totals."""
    df = hourly_df.copy()
    df["date"] = df["time"].dt.date
    daily = df.groupby("date").agg(
        rain_mm=("rain_mm", "sum"),
        max_rain_hr=("rain_mm", "max"),
        avg_temp=("temp_c", "mean"),
        avg_humidity=("humidity_pct", "mean"),
    ).reset_index()
    daily["date"] = pd.to_datetime(daily["date"])
    daily["rain_free_24hr"] = daily["rain_mm"] < 2.0
    return daily


def get_cumulative_rain_since(historical_df: pd.DataFrame, since_date) -> float:
    """Total rainfall (mm) from since_date to today."""
    if isinstance(since_date, str):
        since_date = pd.to_datetime(since_date)
    elif isinstance(since_date, date) and not isinstance(since_date, datetime):
        since_date = pd.to_datetime(since_date)
    mask = historical_df["date"] >= since_date
    return float(historical_df.loc[mask, "rain_mm"].sum())


# ── SYNTHETIC FALLBACK ──────────────────────────────────────────────

def _synthetic_forecast(days: int = FORECAST_DAYS) -> pd.DataFrame:
    """Generate realistic synthetic hourly forecast when API unavailable."""
    rng   = np.random.default_rng(42)
    hours = days * 24
    times = [datetime.now().replace(minute=0, second=0, microsecond=0)
             + timedelta(hours=i) for i in range(hours)]
    # Realistic monsoon-season pattern
    base_rain = np.zeros(hours)
    for i in range(hours):
        hour_of_day = times[i].hour
        if 15 <= hour_of_day <= 20:   # afternoon convective showers
            base_rain[i] = rng.exponential(1.5) * rng.binomial(1, 0.3)
        elif 6 <= hour_of_day <= 9:
            base_rain[i] = rng.exponential(0.5) * rng.binomial(1, 0.15)
    return pd.DataFrame({
        "time":         times,
        "rain_mm":      base_rain,
        "temp_c":       25 + 5 * np.sin(np.arange(hours) * 2 * np.pi / 24) + rng.normal(0, 1, hours),
        "humidity_pct": 70 + 15 * rng.random(hours),
        "wind_kph":     5  + 8  * rng.random(hours),
    })


def _synthetic_historical(start: str, end: str) -> pd.DataFrame:
    """Generate 5 years of synthetic historical daily rainfall."""
    dates = pd.date_range(start=start, end=end, freq="D")
    rng   = np.random.default_rng(99)
    rain  = np.zeros(len(dates))
    for i, d in enumerate(dates):
        m = d.month
        if m in (6, 7, 8, 9):           # SW Monsoon
            rain[i] = rng.exponential(8) * rng.binomial(1, 0.55)
        elif m in (10, 11):              # NE Monsoon (SE India)
            rain[i] = rng.exponential(4) * rng.binomial(1, 0.30)
        else:                            # Dry season
            rain[i] = rng.exponential(1) * rng.binomial(1, 0.08)
    return pd.DataFrame({"date": dates, "rain_mm": rain})
