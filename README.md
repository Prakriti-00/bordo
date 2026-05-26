# 🌿 Bordeaux Spray Advisor v2
**Expert System for Plantation Crops — India**  
*Developed by Dr. Prashant Raysad, Agriculturist · Dhi Insights*

---

## What's New in v2
| Change | Detail |
|---|---|
| **Agriculturist** | Dr. Prashant Raysad's title updated from Agronomist → Agriculturist |
| **15-Day Prediction** | Forecast window extended from 7 days to 15 days |
| **All-India Districts** | 750+ districts across all 28 states + 8 UTs (was 11 districts) |
| **Flexible Location** | Select by district list / 6-digit postal PIN / place name / Google Maps URL or lat-lon |

---

## Quick Start
```bash
pip install -r requirements.txt
streamlit run app.py
```

## Features
1. **Spray Dashboard** — per-crop alert cards, 15-day window table, spray logger  
2. **15-Day Rainfall** — colour-coded bar chart with safety threshold line  
3. **Monsoon Onset** — predicted arrival + last safe spray date  
4. **Spray Log** — full history + manual rain gauge entry  
5. **BM Preparation** — step-by-step chemistry + quantity calculator  

## Location Methods
| Method | How |
|---|---|
| District | Filter by state → select district (750+ options) |
| Postal PIN | Enter any 6-digit India PIN code |
| Place Name | Type village / town / taluk name |
| Google Maps | Paste a Google Maps URL or raw `lat, lon` coordinates |

## Spray Rules (Dr. Prashant Raysad, Agriculturist)
- **24-hour rain-free window** required before spraying (Ca(OH)₂ + CO₂ rainfast reaction)
- **Repeat spray** when **40 inches (1016 mm) cumulative rain** OR **45 days** elapsed — whichever is earlier
- BM is **protective only** — apply before disease onset
- CuSO₄ : Ca(OH)₂ : Water = **1 : 1 : 100** for standard 1% BM
