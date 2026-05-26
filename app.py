"""
app.py — Bordeaux Spray Advisor
Streamlit dashboard for Areca Nut, Black Pepper, Cardamom & Coffee.
Changes v2:
  • "Agriculturist" replaces "Agronomist" for Dr. Prashant Raysad
  • 15-day rainfall prediction window (was 7)
  • All-India district selection (750+ districts, all states/UTs)
  • Location by: district name / postal PIN / GPS coordinates (Google Maps link)
Run: streamlit run app.py
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from datetime import date, timedelta, datetime
import sys, os
from utils.state import load_state
from db import init_db, get_user
init_db()
from db import add_user

#add user here - add_user("username")
add_user("prakriti")
add_user("prashant")

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "username" not in st.session_state:
    st.session_state.username = None

if not st.session_state.logged_in:
    st.title("Login")

    username = st.text_input("Username")
    password = st.text_input("Password", type="password")

    if st.button("Login"):
        user = get_user(username)

        if user and password == "admin123":
            st.session_state.logged_in = True
            st.session_state.username = username
            st.session_state.farm_state = load_state(username)
            st.success("Login successful")
            st.rerun()
        else:
            st.error("Invalid username or password")

    st.stop()

if st.session_state.logged_in:
    if st.button("Logout"):
        st.session_state.logged_in = False
        st.session_state.username = None
        st.rerun()

sys.path.insert(0, os.path.dirname(__file__))

from config import CROPS, DISTRICTS, BM_PREPARATION, FORECAST_DAYS, EXPERT
from data.fetcher import (
    fetch_forecast, fetch_historical,
    aggregate_forecast_daily, get_cumulative_rain_since,
    _synthetic_forecast, _synthetic_historical,
)
from models.rainfall_model import RainfallPredictor, MonsoonOnsetPredictor
from engine.decision import make_spray_decision, compute_protection_status, generate_sms_alert
from utils.state import load_state, save_state, log_spray, update_rain, get_spray_history

# ── PAGE CONFIG ────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Bordeaux Spray Advisor",
    page_icon="🌿",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .main { background: #F8FBF8; }
  .stApp { font-family: 'Segoe UI', sans-serif; }
  .metric-card {
    background:white; border-radius:12px; padding:16px;
    box-shadow:0 2px 8px rgba(0,0,0,0.08); margin:4px 0;
  }
  .alert-expired  { background:#FFEBEE; border-left:5px solid #C62828; border-radius:8px; padding:12px; }
  .alert-urgent   { background:#FFF3E0; border-left:5px solid #E65100; border-radius:8px; padding:12px; }
  .alert-alert    { background:#FFFDE7; border-left:5px solid #F9A825; border-radius:8px; padding:12px; }
  .alert-monitor  { background:#E8F5E9; border-left:5px solid #2E7D32; border-radius:8px; padding:12px; }
  .alert-safe     { background:#E3F2FD; border-left:5px solid #1565C0; border-radius:8px; padding:12px; }
  .spray-go       { background:#E8F5E9; border-radius:12px; padding:16px; border:2px solid #2E7D32; }
  .spray-wait     { background:#FFF8E1; border-radius:12px; padding:16px; border:2px solid #F9A825; }
  .spray-urgent   { background:#FFEBEE; border-radius:12px; padding:16px; border:2px solid #C62828; }
  .window-best    { background:#E8F5E9; border-radius:8px; padding:10px; margin:4px 0; }
  .window-ok      { background:#F3F4F6; border-radius:8px; padding:10px; margin:4px 0; }
  .window-bad     { background:#FEF2F2; border-radius:8px; padding:8px; margin:4px 0; opacity:0.7; }
  .section-header { font-size:1.1rem; font-weight:700; color:#1F4E79; margin:12px 0 6px 0; }
  h1 { color:#1F4E79 !important; }
  h2 { color:#2E75B6 !important; }
  h3 { color:#333 !important; }
  .expert-badge {
    background:linear-gradient(135deg,#1F4E79,#2E75B6);
    color:white; border-radius:8px; padding:10px 16px;
    font-size:0.85rem; margin:8px 0;
  }
</style>
""", unsafe_allow_html=True)

# ── SESSION STATE ──────────────────────────────────────────────────────
if "farm_state" not in st.session_state:
    st.session_state.farm_state = load_state(st.session_state.username)
if "predictor"     not in st.session_state: st.session_state.predictor     = None
if "forecast_daily" not in st.session_state: st.session_state.forecast_daily = None
if "monsoon_info"  not in st.session_state: st.session_state.monsoon_info  = None

# ── HELPER: build state list grouped by state ─────────────────────────
@st.cache_data
def get_state_groups():
    groups = {}
    for key, val in DISTRICTS.items():
        s = val["state"]
        groups.setdefault(s, []).append((key, val["display"]))
    return dict(sorted(groups.items()))

@st.cache_data
def get_all_district_options():
    """Return sorted list of (key, label) for selectbox."""
    opts = []
    for state_name, items in get_state_groups().items():
        for key, disp in sorted(items, key=lambda x: x[1]):
            opts.append((key, f"{state_name} — {disp}"))
    return opts

# ── GEOCODE BY PIN ─────────────────────────────────────────────────────
def geocode_by_pin(pin: str):
    """Look up lat/lon from Indian postal PIN via nominatim (free)."""
    try:
        import urllib.request, json
        url = f"https://nominatim.openstreetmap.org/search?postalcode={pin}&country=India&format=json&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "BordeauxAdvisor/2.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name","")
    except Exception:
        pass
    return None, None, None

def geocode_by_name(name: str):
    """Look up lat/lon from place name in India via nominatim."""
    try:
        import urllib.request, json
        query = urllib.request.quote(f"{name}, India")
        url = f"https://nominatim.openstreetmap.org/search?q={query}&format=json&limit=1"
        req = urllib.request.Request(url, headers={"User-Agent": "BordeauxAdvisor/2.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"]), data[0].get("display_name","")
    except Exception:
        pass
    return None, None, None

def extract_coords_from_maps_url(url: str):
    """Extract lat/lon from a Google Maps URL."""
    import re
    # Handle formats like @12.34,56.78 or ll=12.34,56.78 or q=12.34,56.78
    patterns = [
        r'@(-?\d+\.\d+),(-?\d+\.\d+)',
        r'll=(-?\d+\.\d+),(-?\d+\.\d+)',
        r'q=(-?\d+\.\d+),(-?\d+\.\d+)',
        r'\/(-?\d+\.\d+),(-?\d+\.\d+)',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return float(m.group(1)), float(m.group(2))
    return None, None

# ══ SIDEBAR ═══════════════════════════════════════════════════════════
with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/thumb/4/41/Flag_of_India.svg/40px-Flag_of_India.svg.png",
        width=40
    )
    st.markdown("## 🌿 Bordeaux Spray Advisor")
    st.caption("Areca · Black Pepper · Cardamom · Coffee")

    # ── EXPERT BADGE ────────────────────────────────────────────────
    st.markdown(f"""
    <div class='expert-badge'>
      👨‍🌾 <strong>{EXPERT['name']}</strong><br>
      <span style='opacity:0.85'>{EXPERT['designation']} · {EXPERT['org']}</span>
    </div>
    """, unsafe_allow_html=True)

    st.divider()

    # ══ LOCATION SELECTION ══════════════════════════════════════════
    st.markdown("### 📍 Location")
    location_method = st.radio(
        "Select location by",
        ["🗺 District (All India)", "🔢 Postal PIN", "📝 Place Name", "🌐 Google Maps URL"],
        horizontal=False, label_visibility="collapsed"
    )

    state    = st.session_state.farm_state
    custom_lat, custom_lon, custom_name = None, None, None

    if location_method == "🗺 District (All India)":
        # ── Grouped state filter ──
        state_names = sorted(set(v["state"] for v in DISTRICTS.values()))
        sel_state   = st.selectbox("Filter by State / UT", ["All States & UTs"] + state_names,
                                   key="state_filter")

        if sel_state == "All States & UTs":
            all_opts = get_all_district_options()
        else:
            all_opts = [(k, f"{v['display']}") for k, v in DISTRICTS.items() if v["state"] == sel_state]
            all_opts = sorted(all_opts, key=lambda x: x[1])

        keys    = [o[0] for o in all_opts]
        labels  = [o[1] for o in all_opts]
        cur_key = state.get("district", "chikmagalur")
        idx     = keys.index(cur_key) if cur_key in keys else 0

        sel_idx = st.selectbox(
            f"District ({len(keys)} available)",
            range(len(labels)),
            format_func=lambda i: labels[i],
            index=idx,
            key="district_sel"
        )
        sel_district = keys[sel_idx]
        if sel_district != state.get("district"):
            state["district"]        = sel_district
            state["custom_lat"]      = None
            state["custom_lon"]      = None
            state["custom_name"]     = None
            st.session_state.forecast_daily = None
            save_state(st.session_state.username, state)

        d_info = DISTRICTS[sel_district]
        st.caption(f"📌 {d_info['display']} · {d_info['state']}")
        st.caption(f"🌐 {d_info['lat']:.3f}°N, {d_info['lon']:.3f}°E")
        maps_link = f"https://www.google.com/maps?q={d_info['lat']},{d_info['lon']}"
        st.markdown(f"[📍 View on Google Maps]({maps_link})", unsafe_allow_html=False)

    elif location_method == "🔢 Postal PIN":
        pin_input = st.text_input("Enter 6-digit PIN Code", max_chars=6, placeholder="e.g. 577101")
        if st.button("🔍 Find Location", key="pin_btn") and len(pin_input) == 6:
            with st.spinner("Looking up PIN..."):
                lat, lon, display = geocode_by_pin(pin_input)
            if lat:
                custom_lat, custom_lon, custom_name = lat, lon, display
                state["custom_lat"]  = lat
                state["custom_lon"]  = lon
                state["custom_name"] = display
                st.session_state.forecast_daily = None
                save_state(st.session_state.username, state)
                st.success(f"Found: {display[:60]}")
                st.caption(f"🌐 {lat:.4f}°N, {lon:.4f}°E")
            else:
                st.error("PIN not found. Try a place name instead.")
        elif state.get("custom_lat") and state.get("custom_name"):
            custom_lat  = state["custom_lat"]
            custom_lon  = state["custom_lon"]
            custom_name = state["custom_name"]
            st.caption(f"📌 {custom_name[:60]}")

    elif location_method == "📝 Place Name":
        place_input = st.text_input(
            "Enter village / town / taluk / city",
            placeholder="e.g. Chikmagalur, Wayanad, Coorg..."
        )
        if st.button("🔍 Search", key="name_btn") and place_input.strip():
            with st.spinner("Searching..."):
                lat, lon, display = geocode_by_name(place_input.strip())
            if lat:
                custom_lat, custom_lon, custom_name = lat, lon, display
                state["custom_lat"]  = lat
                state["custom_lon"]  = lon
                state["custom_name"] = display
                st.session_state.forecast_daily = None
                save_state(st.session_state.username, state)
                st.success(f"Found: {display[:60]}")
                st.caption(f"🌐 {lat:.4f}°N, {lon:.4f}°E")
                maps_link = f"https://www.google.com/maps?q={lat},{lon}"
                st.markdown(f"[📍 Verify on Google Maps]({maps_link})")
            else:
                st.error("Location not found. Try a more specific name.")
        elif state.get("custom_lat") and state.get("custom_name"):
            custom_lat  = state["custom_lat"]
            custom_lon  = state["custom_lon"]
            custom_name = state["custom_name"]
            st.caption(f"📌 {custom_name[:60]}")

    elif location_method == "🌐 Google Maps URL":
        maps_url = st.text_input(
            "Paste Google Maps link or coordinates",
            placeholder="https://www.google.com/maps/@12.34,75.78... or 12.34, 75.78"
        )
        if st.button("📍 Use This Location", key="maps_btn") and maps_url.strip():
            lat, lon = None, None
            # Try direct coords first (12.34, 75.78 format)
            import re
            coord_match = re.match(r'^\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\s*$', maps_url.strip())
            if coord_match:
                lat = float(coord_match.group(1))
                lon = float(coord_match.group(2))
                custom_name = f"Custom location ({lat:.4f}, {lon:.4f})"
            else:
                lat, lon = extract_coords_from_maps_url(maps_url)
                if lat:
                    custom_name = f"Google Maps pin ({lat:.4f}, {lon:.4f})"
            if lat:
                custom_lat, custom_lon = lat, lon
                state["custom_lat"]  = lat
                state["custom_lon"]  = lon
                state["custom_name"] = custom_name
                st.session_state.forecast_daily = None
                save_state(st.session_state.username, state)
                st.success(f"Location set: {custom_name}")
            else:
                st.error("Could not extract coordinates. Paste the full Google Maps URL or 'lat, lon'.")
        elif state.get("custom_lat"):
            custom_lat  = state["custom_lat"]
            custom_lon  = state["custom_lon"]
            custom_name = state.get("custom_name","")
            st.caption(f"📌 {custom_name}")

    # ── Active lat/lon ───────────────────────────────────────────────
    if custom_lat and custom_lon:
        active_lat  = custom_lat
        active_lon  = custom_lon
        active_name = custom_name or "Custom Location"
    else:
        d = DISTRICTS.get(state.get("district","chikmagalur"), DISTRICTS["chikmagalur"])
        active_lat  = d["lat"]
        active_lon  = d["lon"]
        active_name = f"{d['display']}, {d['state']}"

    st.divider()

    # ── CROP SELECTION ───────────────────────────────────────────────
    st.markdown("### 🌿 Crops")
    crop_sel = st.multiselect(
        "Select crops on your farm",
        options=list(CROPS.keys()),
        default=state.get("selected_crops", ["areca"]),
        format_func=lambda k: f"{CROPS[k]['emoji']} {CROPS[k]['label']}",
    )
    if set(crop_sel) != set(state.get("selected_crops", [])):
        state["selected_crops"] = crop_sel
        save_state(st.session_state.username, state)

    st.divider()

    # ── LAST SPRAY DATES ─────────────────────────────────────────────
    st.markdown("### 📅 Last Spray Dates")
    for crop_key in crop_sel:
        c = CROPS[crop_key]
        default_date = date.today() - timedelta(days=25)
        stored = state.get("last_spray", {}).get(crop_key)
        if stored:
            try:
                default_date = date.fromisoformat(stored)
            except Exception:
                pass
        chosen = st.date_input(
            f"{c['emoji']} {c['label']}",
            value=default_date,
            max_value=date.today(),
            key=f"date_{crop_key}",
        )
        state.setdefault("last_spray", {})[crop_key] = str(chosen)
    save_state(st.session_state.username, state)

    st.divider()

    # ── FETCH BUTTON ─────────────────────────────────────────────────
    if st.button("🔄 Fetch 15-Day Forecast", use_container_width=True, type="primary"):
        with st.spinner(f"Fetching 15-day forecast for {active_name}..."):
            try:
                hourly = fetch_forecast(active_lat, active_lon, days=FORECAST_DAYS)
                st.session_state.forecast_daily = aggregate_forecast_daily(hourly)
                hist   = fetch_historical(active_lat, active_lon)
                pred   = RainfallPredictor()
                pred.fit(hist)
                st.session_state.predictor     = pred
                monsoon_model = MonsoonOnsetPredictor()
                st.session_state.monsoon_info  = monsoon_model.predict(active_lat, active_lon)
                st.success(f"✅ 15-day forecast loaded for {active_name}")
            except Exception as e:
                st.warning(f"Using offline data. ({str(e)[:60]})")
                hourly = _synthetic_forecast(FORECAST_DAYS)
                st.session_state.forecast_daily = aggregate_forecast_daily(hourly)

# ═══════════════════════════════════════════════════════════════════════
# MAIN CONTENT — 5 TABS
# ═══════════════════════════════════════════════════════════════════════
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🌿 Spray Dashboard",
    "🌧 15-Day Rainfall",
    "🌦 Monsoon Onset",
    "📋 Spray Log",
    "🧪 BM Preparation",
])

forecast_df = st.session_state.forecast_daily
if forecast_df is None:
    with st.spinner("Loading initial forecast..."):
        hourly = _synthetic_forecast(FORECAST_DAYS)
        forecast_df = aggregate_forecast_daily(hourly)
        st.session_state.forecast_daily = forecast_df

# ── TAB 1: SPRAY DASHBOARD ─────────────────────────────────────────────
with tab1:
    st.header(f"🌿 Bordeaux Spray Dashboard")
    st.caption(f"📍 {active_name}  |  🔬 {EXPERT['name']}, {EXPERT['designation']}  |  📅 15-Day Window")

    if not crop_sel:
        st.info("Select at least one crop from the sidebar.")
    else:
        for crop_key in crop_sel:
            c = CROPS[crop_key]
            last_spray_str = state.get("last_spray", {}).get(crop_key)
            try:
                last_spray = date.fromisoformat(last_spray_str) if last_spray_str else date.today() - timedelta(days=25)
            except Exception:
                last_spray = date.today() - timedelta(days=25)

            cum_rain = state.get("cumulative_rain", {}).get(crop_key, 0.0)
            status   = compute_protection_status(crop_key, last_spray, cum_rain)

            # Build prediction df from ML predictor if available
            if st.session_state.predictor:
                pred_df = st.session_state.predictor.predict(days=FORECAST_DAYS)
            else:
                pred_df = forecast_df[["date","rain_mm"]].rename(
                    columns={"rain_mm": "predicted_rain_mm"}
                ).copy()
                pred_df["rain_free_24hr"]     = pred_df["predicted_rain_mm"] < 2.0
                pred_df["spray_window_score"] = pred_df["predicted_rain_mm"].apply(
                    lambda r: max(0, 100 - int(r * 8))
                )

            decision = make_spray_decision(crop_key, last_spray, cum_rain, pred_df)

            # ── CROP CARD ──
            alert_class = f"alert-{status.alert_level}"
            st.markdown(f"""
            <div class='{alert_class}'>
            <b>{c['emoji']} {c['label']}</b> &nbsp;|&nbsp; Disease: {c['disease']} &nbsp;|&nbsp;
            Last spray: {last_spray.strftime('%d %b %Y')} ({status.days_elapsed} days ago)
            </div>
            """, unsafe_allow_html=True)

            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Days Elapsed", f"{status.days_elapsed} / 45",
                          delta=f"{status.days_remaining} days left",
                          delta_color="inverse")
            with col2:
                st.metric("Rain Since Spray", f"{status.rain_since_mm:.0f} mm",
                          delta=f"{status.rain_remaining_mm:.0f} mm to trigger",
                          delta_color="inverse")
            with col3:
                icon = {"expired":"🔴","urgent":"🟠","alert":"🟡","monitor":"🟢","safe":"🔵"}.get(status.alert_level,"⚪")
                st.metric("Protection Status", f"{icon} {status.alert_level.title()}")
            with col4:
                if decision.best_window_date:
                    days_to_window = (decision.best_window_date - date.today()).days
                    st.metric("Best Spray Window",
                              decision.best_window_date.strftime("%d %b"),
                              delta=f"Score {decision.best_window_score}/100")
                else:
                    st.metric("Best Window", "No window", delta="Monitor daily")

            # Progress bars
            sub1, sub2 = st.columns(2)
            with sub1:
                st.progress(status.pct_days / 100, text=f"Day tracker {status.pct_days}%")
            with sub2:
                st.progress(status.pct_rain / 100, text=f"Rain tracker {status.pct_rain}%")

            # Action summary
            spray_class = "spray-urgent" if status.alert_level in ("expired","urgent") else \
                          "spray-wait"   if status.alert_level == "alert" else "spray-go"
            st.markdown(f"<div class='{spray_class}'>{decision.action_summary}</div>",
                        unsafe_allow_html=True)

            # 15-day window table
            with st.expander(f"📅 15-Day Spray Windows — {c['label']}", expanded=False):
                rows = []
                for w in decision.windows_15day:
                    rows.append({
                        "Date":     w["date"].strftime("%a %d %b"),
                        "Rain (mm)":f"{w['rain_mm']:.1f}",
                        "Rain-Free": "✅ Yes" if w["rain_free"] else "🌧 No",
                        "Score":    f"{w['score']}/100",
                        "Quality":  w["quality"],
                    })
                wdf = pd.DataFrame(rows)
                st.dataframe(wdf, hide_index=True, use_container_width=True)

            # SMS preview
            with st.expander("📱 SMS / WhatsApp Alert Preview"):
                col_en, col_kn = st.columns(2)
                with col_en:
                    st.markdown("**English**")
                    st.code(decision.sms_text, language=None)
                with col_kn:
                    st.markdown("**ಕನ್ನಡ (Kannada)**")
                    st.code(decision.sms_kannada, language=None)

            # Log spray button
            cola, colb = st.columns([1, 4])
            with cola:
                if st.button(f"✅ Log Spray Today — {c['label']}", key=f"log_{crop_key}"):
                    state = log_spray(
                        st.session_state.username,
                        state,
                        crop_key,
                        date.today(),
                        cum_rain
                    )
                    st.session_state.farm_state = state
                    st.success(f"Spray logged for {c['label']} on {date.today().strftime('%d %b %Y')}")
                    st.rerun()

            st.divider()

# ── TAB 2: 15-DAY RAINFALL CHART ──────────────────────────────────────
with tab2:
    st.header(f"🌧 15-Day Rainfall Forecast — {active_name}")
    st.caption("Green bars = rain-free (safe to spray). Blue = light rain. Red = heavy rain.")

    if forecast_df is not None and len(forecast_df) > 0:
        colors = []
        for _, row in forecast_df.iterrows():
            r = row["rain_mm"]
            colors.append(
                "#2E7D32" if r < 2 else
                "#1565C0" if r < 10 else
                "#C62828"
            )

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=[str(d.strftime("%d %b")) for d in forecast_df["date"]],
            y=forecast_df["rain_mm"],
            marker_color=colors,
            name="Rainfall (mm)",
            hovertemplate="<b>%{x}</b><br>Rain: %{y:.1f} mm<extra></extra>",
        ))
        fig.add_hline(y=2, line_dash="dash", line_color="#F9A825",
                      annotation_text="2mm threshold (spray safety line)", annotation_position="top right")
        fig.update_layout(
            title=f"15-Day Rainfall Forecast — {active_name}",
            xaxis_title="Date",
            yaxis_title="Rainfall (mm)",
            height=420,
            plot_bgcolor="#F8FBF8",
            paper_bgcolor="#F8FBF8",
            font=dict(family="Segoe UI"),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Summary table
        st.subheader("📊 Daily Summary")
        summary_rows = []
        for _, row in forecast_df.iterrows():
            d = row["date"]
            r = row["rain_mm"]
            summary_rows.append({
                "Date":      d.strftime("%a %d %b"),
                "Rain (mm)": f"{r:.1f}",
                "Status":    "✅ Spray Safe"  if r < 2  else
                             "🟡 Light Rain"  if r < 10 else
                             "🔴 Heavy Rain",
                "Temp (°C)": f"{row.get('avg_temp', 0):.0f}",
                "Humidity %":f"{row.get('avg_humidity', 0):.0f}",
            })
        st.dataframe(pd.DataFrame(summary_rows), hide_index=True, use_container_width=True)
    else:
        st.info("Click '🔄 Fetch 15-Day Forecast' in the sidebar to load data.")

# ── TAB 3: MONSOON ONSET ───────────────────────────────────────────────
with tab3:
    st.header("🌦 SW Monsoon Onset Prediction")

    monsoon_info = st.session_state.monsoon_info
    if monsoon_info is None:
        from models.rainfall_model import MonsoonOnsetPredictor
        monsoon_info = MonsoonOnsetPredictor().predict(active_lat, active_lon)
        st.session_state.monsoon_info = monsoon_info

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Predicted Onset", monsoon_info.predicted_date.strftime("%d %b %Y"),
                  delta=f"In {monsoon_info.days_until} days" if monsoon_info.days_until > 0 else "Arrived")
    with col2:
        st.metric("Last Safe Spray Before Monsoon",
                  monsoon_info.last_safe_spray.strftime("%d %b %Y"),
                  delta=f"In {(monsoon_info.last_safe_spray - date.today()).days} days")
    with col3:
        conf_color = {"High":"🟢","Medium":"🟡","Low":"🟠"}.get(monsoon_info.confidence,"⚪")
        st.metric("Prediction Confidence", f"{conf_color} {monsoon_info.confidence}")

    st.info(f"ℹ️ {monsoon_info.rationale}")

    # Timeline visualisation
    today       = date.today()
    onset       = monsoon_info.predicted_date
    last_safe   = monsoon_info.last_safe_spray
    days_range  = max(30, (onset - today).days + 10)
    dates       = [today + timedelta(days=i) for i in range(days_range)]
    zones       = []
    for d in dates:
        if d < last_safe:
            zones.append(("Safe Spray Zone", "#2E7D32"))
        elif d < onset:
            zones.append(("Caution Zone", "#F9A825"))
        else:
            zones.append(("Monsoon Active", "#1565C0"))

    fig2 = go.Figure()
    # Draw colored zone bands
    prev_z = None
    band_start = dates[0]
    for i, (d, (zone, col)) in enumerate(zip(dates, zones)):
        if zone != prev_z or i == len(dates) - 1:
            if prev_z is not None:
                fig2.add_vrect(
                    x0=str(band_start), x1=str(d),
                    fillcolor=col, opacity=0.2,
                    line_width=0, annotation_text=prev_z,
                    annotation_position="top left",
                )
            band_start = d
            prev_z = zone

    fig2.add_vline(
    x=today.isoformat(),
    line_color="black",
    line_dash="solid"
)
    fig2.add_vline(
    x=last_safe.isoformat(),
    line_color="#E65100",
    line_dash="dash"
)
    fig2.add_vline(
    x=onset.isoformat(),
    line_color="#1565C0",
    line_dash="dash"
)
    fig2.update_layout(
        title="Monsoon Onset Timeline",
        xaxis=dict(type="date"),
        yaxis=dict(visible=False),
        height=200,
        showlegend=False,
        plot_bgcolor="#F8FBF8",
        paper_bgcolor="#F8FBF8",
    )
    st.plotly_chart(fig2, use_container_width=True)

# ── TAB 4: SPRAY LOG ───────────────────────────────────────────────────
with tab4:
    st.header("📋 Spray Log & Manual Rain Entry")

    col_log, col_rain = st.columns(2)

    with col_log:
        st.subheader("Spray History")
        if crop_sel:
            all_logs = []
            for ck in crop_sel:
                for entry in get_spray_history(state, ck):
                    all_logs.append({
                        "Crop":         CROPS[ck]["label"],
                        "Date":         entry["date"],
                        "Rain at spray":f"{entry.get('rain_at_spray',0):.0f} mm",
                        "Logged at":    entry.get("logged_at","")[:16],
                    })
            if all_logs:
                st.dataframe(pd.DataFrame(all_logs), hide_index=True, use_container_width=True)
            else:
                st.info("No sprays logged yet. Use 'Log Spray Today' in the Dashboard tab.")

    with col_rain:
        st.subheader("Manual Rain Entry")
        st.caption("Add observed rainfall to your rain tracker (e.g. from rain gauge).")
        rain_input = st.number_input("Rainfall today (mm)", min_value=0.0, max_value=500.0, step=0.5)
        if st.button("💧 Add Rain Entry"):
            state = update_rain(
                st.session_state.username,
                state,
                rain_input
            )
            st.session_state.farm_state = state
            st.success(f"Added {rain_input} mm to all crop trackers.")
            st.rerun()
        st.divider()
        st.subheader("Current Rain Totals")
        for ck in crop_sel:
            total = state.get("cumulative_rain", {}).get(ck, 0)
            from config import RAIN_THRESHOLD_MM
            st.metric(
                f"{CROPS[ck]['emoji']} {CROPS[ck]['label']}",
                f"{total:.0f} mm",
                delta=f"{RAIN_THRESHOLD_MM - total:.0f} mm to trigger"
            )

# ── TAB 5: BM PREPARATION ──────────────────────────────────────────────
with tab5:
    st.header("🧪 Bordeaux Mixture Preparation Guide")
    bm = BM_PREPARATION
    expert = bm["expert"]
    st.markdown(f"""
    <div style='background:linear-gradient(135deg,#1F4E79,#2E75B6);color:white;
    border-radius:10px;padding:14px 20px;margin-bottom:16px;'>
    👨‍🌾 <strong>{expert['name']}</strong> · <em>{expert['designation']}</em> · {expert['org']}
    </div>
    """, unsafe_allow_html=True)

    st.info(f"📌 **Chemistry Note:** {bm['chemistry_note']}")

    for s in bm["steps"]:
        with st.expander(f"Step {s['step']}: {s['title']}", expanded=(s['step'] <= 3)):
            st.write(s["detail"])

    st.divider()
    st.subheader("📐 Quantity Calculator")
    litres = st.number_input("Spray volume needed (litres)", min_value=10, max_value=5000,
                              value=100, step=10)
    for crop_key in crop_sel:
        c = CROPS[crop_key]
        pct = c["bm_pct"]
        cuso4_kg  = litres * pct / 100
        lime_kg   = litres * pct / 100
        water_lit = litres
        st.markdown(f"""
        **{c['emoji']} {c['label']}** ({c['bm_ratio']} Bordeaux):
        - CuSO₄ (copper sulphate): **{cuso4_kg:.2f} kg**
        - Ca(OH)₂ (lime): **{lime_kg:.2f} kg**
        - Water: **{water_lit} litres** (split into two 50-litre lots)
        """)

    st.divider()
    st.subheader("⚠️ Safety & Legal Notes")
    st.warning("""
    • Wear gloves, eye protection and mask when preparing Bordeaux mixture.  
    • Copper is harmful to aquatic life — do not spray near water bodies or ponds.  
    • Do not spray on water-stressed plants — risk of copper toxicity.  
    • Pre-harvest interval (PHI): 7 days for most crops. Check local guidelines.  
    • Dispose of excess mixture safely — do not pour into drains or water sources.
    """)
