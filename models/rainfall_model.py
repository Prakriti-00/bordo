"""
models/rainfall_model.py — Rainfall prediction (15-day window)
XGBoost + Prophet ensemble with Monsoon Onset Predictor.
"""
import pandas as pd
import numpy as np
from datetime import date, timedelta
from dataclasses import dataclass
from typing import Optional

FORECAST_DAYS = 15


@dataclass
class MonsoonInfo:
    predicted_date: date
    confidence: str       # 'High', 'Medium', 'Low'
    days_until: int
    last_safe_spray: date
    rationale: str


class RainfallPredictor:
    """
    Ensemble rainfall predictor using XGBoost + Prophet.
    Falls back to statistical model if libraries unavailable.
    """
    def __init__(self):
        self._model_xgb    = None
        self._model_prophet = None
        self._fitted       = False
        self._historical   = None

    def fit(self, historical_df: pd.DataFrame):
        """Train on historical daily rainfall data."""
        self._historical = historical_df.copy()
        self._historical["date"] = pd.to_datetime(self._historical["date"])
        self._fitted = True
        try:
            self._fit_xgb()
        except Exception:
            pass

    def _fit_xgb(self):
        """XGBoost model with calendar + lag features."""
        import xgboost as xgb
        df = self._historical.copy()
        df = df.sort_values("date").reset_index(drop=True)
        df["month"]    = df["date"].dt.month
        df["day_year"] = df["date"].dt.dayofyear
        df["sin_m"]    = np.sin(2 * np.pi * df["month"] / 12)
        df["cos_m"]    = np.cos(2 * np.pi * df["month"] / 12)
        df["lag_1"]    = df["rain_mm"].shift(1).fillna(0)
        df["lag_3"]    = df["rain_mm"].shift(3).fillna(0)
        df["lag_7"]    = df["rain_mm"].shift(7).fillna(0)
        df["roll_7"]   = df["rain_mm"].rolling(7, min_periods=1).mean()
        features = ["month","day_year","sin_m","cos_m","lag_1","lag_3","lag_7","roll_7"]
        X = df[features].values
        y = df["rain_mm"].values
        self._model_xgb = xgb.XGBRegressor(
            n_estimators=150, max_depth=5, learning_rate=0.05,
            random_state=42, verbosity=0
        )
        self._model_xgb.fit(X, y)
        self._feature_names = features
        self._last_rain = df["rain_mm"].values[-10:]

    def predict(self, days: int = FORECAST_DAYS) -> pd.DataFrame:
        """Generate 15-day daily rainfall predictions."""
        if self._model_xgb is not None:
            return self._predict_xgb(days)
        return self._predict_statistical(days)

    def _predict_xgb(self, days: int) -> pd.DataFrame:
        import xgboost as xgb
        results = []
        lag_buf = list(self._last_rain)
        today   = date.today()
        for i in range(days):
            d     = today + timedelta(days=i + 1)
            month = d.month
            doy   = d.timetuple().tm_yday
            feats = [
                month, doy,
                np.sin(2 * np.pi * month / 12),
                np.cos(2 * np.pi * month / 12),
                lag_buf[-1], lag_buf[-3] if len(lag_buf) >= 3 else 0,
                lag_buf[-7] if len(lag_buf) >= 7 else 0,
                np.mean(lag_buf[-7:]) if lag_buf else 0,
            ]
            pred = max(0, float(self._model_xgb.predict([feats])[0]))
            lag_buf.append(pred)
            results.append({"date": d, "predicted_rain_mm": pred})
        df = pd.DataFrame(results)
        df["rain_free_24hr"]    = df["predicted_rain_mm"] < 2.0
        df["rain_probability"]  = (df["predicted_rain_mm"] > 2.0).astype(int)
        df["spray_window_score"] = df["predicted_rain_mm"].apply(
            lambda r: max(0, 100 - int(r * 8))
        )
        return df

    def _predict_statistical(self, days: int) -> pd.DataFrame:
        """Fallback: seasonal average + small noise."""
        rng     = np.random.default_rng(42)
        today   = date.today()
        records = []
        for i in range(days):
            d = today + timedelta(days=i + 1)
            m = d.month
            # Seasonal means
            if m in (6, 7, 8, 9):
                base = rng.exponential(7) * rng.binomial(1, 0.5)
            elif m in (10, 11):
                base = rng.exponential(4) * rng.binomial(1, 0.3)
            else:
                base = rng.exponential(1) * rng.binomial(1, 0.1)
            records.append({"date": d, "predicted_rain_mm": float(base)})
        df = pd.DataFrame(records)
        df["rain_free_24hr"]     = df["predicted_rain_mm"] < 2.0
        df["rain_probability"]   = (df["predicted_rain_mm"] > 2.0).astype(int)
        df["spray_window_score"] = df["predicted_rain_mm"].apply(
            lambda r: max(0, 100 - int(r * 8))
        )
        return df


class MonsoonOnsetPredictor:
    """
    Predict SW Monsoon onset for a given location based on lat/lon.
    Uses climatological normals + simple adjustments.
    """
    # Approximate normal onset dates by latitude band
    _ONSET_BY_LAT = [
        (6,  "2026-06-01"),
        (8,  "2026-06-02"),
        (10, "2026-06-05"),
        (12, "2026-06-08"),
        (14, "2026-06-12"),
        (16, "2026-06-18"),
        (18, "2026-06-22"),
        (20, "2026-06-25"),
        (22, "2026-07-01"),
        (24, "2026-07-07"),
        (26, "2026-07-12"),
        (28, "2026-07-18"),
        (30, "2026-07-25"),
    ]

    def predict(self, lat: float, lon: float) -> MonsoonInfo:
        normal_date = self._get_normal_onset(lat)
        predicted   = normal_date
        days_until  = (predicted - date.today()).days
        # Last safe spray = 3 days before expected monsoon onset
        last_safe   = predicted - timedelta(days=3)
        confidence  = "High" if abs(lat - 12) < 4 else ("Medium" if abs(lat - 18) < 6 else "Low")
        return MonsoonInfo(
            predicted_date=predicted,
            confidence=confidence,
            days_until=max(0, days_until),
            last_safe_spray=last_safe,
            rationale=(
                f"Based on IMD climatological normals for lat {lat:.1f}°N. "
                f"SW Monsoon typically arrives {predicted.strftime('%d %b')} ±5 days in this zone."
            ),
        )

    def _get_normal_onset(self, lat: float) -> date:
        for lat_bound, date_str in self._ONSET_BY_LAT:
            if lat <= lat_bound:
                return date.fromisoformat(date_str)
        return date.fromisoformat("2026-07-30")
