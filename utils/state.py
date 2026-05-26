"""utils/state.py — Farm state persistence and spray log."""
import json, os
from datetime import date, datetime

def get_state_file(username):
    return os.path.join(
        os.path.dirname(__file__),
        f".farm_state_{username}.json"
    )

def default_state() -> dict:
    return {
        "district":       "chikmagalur",
        "custom_lat":     None,
        "custom_lon":     None,
        "custom_name":    None,
        "selected_crops": ["areca"],
        "last_spray":     {},
        "cumulative_rain":{},
        "spray_log":      [],   
        "phone":          "",
    }


def load_state(username) -> dict:
    try:
        with open(get_state_file(username)) as f:
            return {**default_state(), **json.load(f)}
    except Exception:
        return default_state()


def save_state(username, state: dict):
    try:
        with open(get_state_file(username), "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception:
        pass


def log_spray(username, state: dict, crop_key: str, spray_date: date,
              rain_reset_mm: float = 0.0) -> dict:
    state["last_spray"][crop_key]      = str(spray_date)
    state["cumulative_rain"][crop_key] = rain_reset_mm
    entry = {
        "crop": crop_key, "date": str(spray_date),
        "logged_at": datetime.now().isoformat(),
        "rain_at_spray": rain_reset_mm,
    }
    state.setdefault("spray_log", []).append(entry)
    save_state(username, state)
    return state


def update_rain(username, state: dict, rain_mm: float) -> dict:
    for crop in state.get("selected_crops", []):
        state["cumulative_rain"][crop] = \
            state["cumulative_rain"].get(crop, 0) + rain_mm
    save_state(username, state)
    return state


def get_spray_history(state: dict, crop_key: str = None) -> list:
    log = state.get("spray_log", [])
    if crop_key:
        log = [e for e in log if e["crop"] == crop_key]
    return sorted(log, key=lambda x: x["date"], reverse=True)
