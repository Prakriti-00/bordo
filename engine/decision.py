"""
engine/decision.py — Bordeaux Spray Decision Engine
3-level alert system with 15-day window scoring.
"""
import pandas as pd
from datetime import date, timedelta
from dataclasses import dataclass
from typing import Optional
from config import RAIN_THRESHOLD_MM, DAY_THRESHOLD, FORECAST_DAYS


@dataclass
class ProtectionStatus:
    alert_level: str      # 'expired','urgent','alert','monitor','safe'
    days_elapsed: int
    days_remaining: int
    pct_days: int
    rain_since_mm: float
    rain_remaining_mm: float
    pct_rain: int
    next_trigger: str     # which trigger fires first


@dataclass
class SprayDecision:
    action_level: str          # 'spray_now','spray_soon','plan','wait','monitor'
    action_summary: str
    best_window_date: Optional[date]
    best_window_score: int
    windows_15day: list         # list of dicts
    sms_text: str
    sms_kannada: str


def compute_protection_status(crop_key: str, last_spray_date: date,
                               cumulative_rain_mm: float) -> ProtectionStatus:
    days_elapsed   = (date.today() - last_spray_date).days
    days_remaining = max(0, DAY_THRESHOLD - days_elapsed)
    pct_days       = min(100, int(days_elapsed / DAY_THRESHOLD * 100))

    rain_remaining = max(0, RAIN_THRESHOLD_MM - cumulative_rain_mm)
    pct_rain       = min(100, int(cumulative_rain_mm / RAIN_THRESHOLD_MM * 100))

    # Which trigger fires first?
    rain_days_at_avg = (rain_remaining / 8) if rain_remaining > 0 else 0  # rough days estimate
    if days_remaining <= rain_days_at_avg:
        next_trigger = f"⏱ Day trigger in {days_remaining} days"
    else:
        next_trigger = f"🌧 Rain trigger after {rain_remaining:.0f} mm more rain"

    if days_elapsed >= DAY_THRESHOLD or cumulative_rain_mm >= RAIN_THRESHOLD_MM:
        level = "expired"
    elif days_elapsed >= DAY_THRESHOLD - 7 or cumulative_rain_mm >= RAIN_THRESHOLD_MM * 0.90:
        level = "urgent"
    elif days_elapsed >= DAY_THRESHOLD - 14 or cumulative_rain_mm >= RAIN_THRESHOLD_MM * 0.75:
        level = "alert"
    elif days_elapsed >= 20 or cumulative_rain_mm >= RAIN_THRESHOLD_MM * 0.5:
        level = "monitor"
    else:
        level = "safe"

    return ProtectionStatus(
        alert_level=level,
        days_elapsed=days_elapsed,
        days_remaining=days_remaining,
        pct_days=pct_days,
        rain_since_mm=cumulative_rain_mm,
        rain_remaining_mm=rain_remaining,
        pct_rain=pct_rain,
        next_trigger=next_trigger,
    )


def make_spray_decision(crop_key: str, last_spray_date: date,
                        cumulative_rain_mm: float,
                        forecast_df: pd.DataFrame) -> SprayDecision:
    """
    Analyse 15-day forecast and compute optimal spray windows.
    Returns full SprayDecision object.
    """
    status = compute_protection_status(crop_key, last_spray_date, cumulative_rain_mm)

    # Score each day in the 15-day window
    windows = []
    for _, row in forecast_df.iterrows():
        rain    = row.get("predicted_rain_mm", 0)
        score   = row.get("spray_window_score", max(0, 100 - int(rain * 8)))
        rf      = bool(row.get("rain_free_24hr", rain < 2.0))
        d       = row["date"]
        d_obj   = d.date() if hasattr(d, "date") else d
        windows.append({
            "date":           d_obj,
            "rain_mm":        round(rain, 1),
            "rain_free":      rf,
            "score":          score,
            "quality":        "🟢 Excellent" if score >= 85 else
                              "🟡 Good"      if score >= 65 else
                              "🟠 Fair"      if score >= 40 else "🔴 Poor",
        })

    # Best window
    good_windows = [w for w in windows if w["score"] >= 65]
    best = max(good_windows, key=lambda w: w["score"]) if good_windows else None

    # Determine action level
    if status.alert_level in ("expired", "urgent"):
        if best:
            action = "spray_soon"
            summary = (
                f"⚠️ Protection {'EXPIRED' if status.alert_level=='expired' else 'CRITICAL'}! "
                f"Best spray window: {best['date'].strftime('%d %b')} (score {best['score']}/100). "
                f"Prepare Bordeaux mixture now."
            )
        else:
            action = "wait"
            summary = (
                "⚠️ Protection critical but no rain-free window in next 15 days. "
                "Monitor daily — spray at next available window."
            )
    elif status.alert_level == "alert":
        action = "plan"
        summary = (
            f"📅 Plan next spray. {status.days_remaining} days left / "
            f"{status.rain_remaining_mm:.0f} mm rain remaining. "
            + (f"Best upcoming window: {best['date'].strftime('%d %b')}." if best else "Watch forecast.")
        )
    else:
        action = "monitor"
        summary = (
            f"✅ Protection adequate ({status.days_elapsed} days, {status.rain_since_mm:.0f} mm). "
            f"Next trigger: {status.next_trigger}."
        )

    # Best window date and score
    bw_date  = best["date"] if best else None
    bw_score = best["score"] if best else 0

    sms_en = _generate_sms_english(crop_key, action, best, status)
    sms_kn = _generate_sms_kannada(crop_key, action, best, status)

    return SprayDecision(
        action_level=action,
        action_summary=summary,
        best_window_date=bw_date,
        best_window_score=bw_score,
        windows_15day=windows,
        sms_text=sms_en,
        sms_kannada=sms_kn,
    )


def _generate_sms_english(crop_key: str, action: str,
                           best: Optional[dict], status: ProtectionStatus) -> str:
    if action in ("spray_soon", "spray_now"):
        date_str = best["date"].strftime("%d %b") if best else "ASAP"
        return (
            f"BORDEAUX ALERT [{crop_key.upper()}]: "
            f"Protection {'expired' if status.alert_level=='expired' else 'critical'}. "
            f"Best spray window: {date_str}. "
            f"Prepare 1% Bordeaux (1:1:100). 24hr rain-free needed."
        )
    elif action == "plan":
        date_str = best["date"].strftime("%d %b") if best else "upcoming"
        return (
            f"BORDEAUX REMINDER [{crop_key.upper()}]: "
            f"Plan next spray. {status.days_remaining} days remaining. "
            f"Upcoming window: {date_str}. Monitor forecast."
        )
    return (
        f"BORDEAUX STATUS [{crop_key.upper()}]: "
        f"Protection OK. {status.days_remaining} days / "
        f"{status.rain_remaining_mm:.0f}mm remaining. No action needed."
    )


def _generate_sms_kannada(crop_key: str, action: str,
                           best: Optional[dict], status: ProtectionStatus) -> str:
    crop_labels = {"areca": "ಅಡಿಕೆ", "pepper": "ಕಾಳು ಮೆಣಸು",
                   "cardamom": "ಏಲಕ್ಕಿ", "coffee": "ಕಾಫಿ"}
    label = crop_labels.get(crop_key, crop_key)
    if action in ("spray_soon", "spray_now"):
        date_str = best["date"].strftime("%d %b") if best else "ಶೀಘ್ರವಾಗಿ"
        return (
            f"ಬೋರ್ಡೋ ಅಲರ್ಟ್ [{label}]: ರಕ್ಷಣೆ "
            f"{'ಮುಗಿದಿದೆ' if status.alert_level=='expired' else 'ತುರ್ತು'}. "
            f"ಸಿಂಪಡಿಸಲು ಉತ್ತಮ ದಿನ: {date_str}. "
            f"1% ಬೋರ್ಡೋ ದ್ರಾವಣ ತಯಾರಿಸಿ."
        )
    elif action == "plan":
        return (
            f"ಬೋರ್ಡೋ ರಿಮೈಂಡರ್ [{label}]: "
            f"ಮುಂದಿನ ಸಿಂಪರಣೆ ಯೋಜಿಸಿ. "
            f"{status.days_remaining} ದಿನ ಬಾಕಿ."
        )
    return (
        f"ಬೋರ್ಡೋ ಸ್ಥಿತಿ [{label}]: ರಕ್ಷಣೆ ಸರಿಯಾಗಿದೆ. "
        f"{status.days_remaining} ದಿನ ಬಾಕಿ."
    )


def generate_sms_alert(decision: SprayDecision, language: str = "en") -> str:
    return decision.sms_kannada if language == "kn" else decision.sms_text
