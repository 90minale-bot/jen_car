"""
Sister SUV Marketcheck Dashboard
--------------------------------
Streamlit page that searches Marketcheck for the SUV list you built:
- Mazda CX-5
- Honda CR-V
- Toyota RAV4
- Toyota Highlander
- Mazda CX-70

Features:
- One streamlined section per vehicle
- Your notes for each vehicle
- Photo/search links per model
- Marketcheck API search
- Value-style scoring/ranking inspired by your prior QX60 dashboard:
  price below market, lower miles, clean title, one owner, closer distance, AWD, and MPG
- Top 10 ranked listings table per model

Setup:
1) pip install streamlit pandas requests numpy
2) Add this to Streamlit secrets:
   MARKETCHECK_API_KEY = "your_marketcheck_key_here"
3) Run locally:
   streamlit run sister_suv_marketcheck_dashboard.py
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import requests
import streamlit as st


MARKETCHECK_ENDPOINT = "https://api.marketcheck.com/v2/search/car/active"
DEFAULT_ZIP = "60004"
DEFAULT_RADIUS = 100
DEFAULT_ROWS_PER_MODEL = 50


@dataclass(frozen=True)
class VehicleConfig:
    key: str
    make: str
    model: str
    years: str
    category: str
    notes: List[str]
    mpg_note: str
    photo_query: str
    preferred_trim_keywords: List[str]


VEHICLES: List[VehicleConfig] = [
    VehicleConfig(
        key="mazda_cx5",
        make="Mazda",
        model="CX-5",
        years="2022,2023,2024",
        category="Compact SUV",
        mpg_note="28–30 mpg highway",
        notes=[
            "Very reliable",
            "Nice interior without luxury-brand pricing",
            "Available AWD",
            "Used 2022–2024 models are excellent values",
            "Possible drawback: infotainment uses a control knob instead of a touchscreen",
        ],
        photo_query="2024 Mazda CX-5 exterior interior",
        preferred_trim_keywords=["touring", "preferred", "premium", "carbon", "select"],
    ),
    VehicleConfig(
        key="honda_crv",
        make="Honda",
        model="CR-V",
        years="2022,2023,2024,2025",
        category="Compact SUV",
        mpg_note="About 30 mpg combined",
        notes=[
            "Outstanding reliability",
            "Holds value well",
            "One of the easiest vehicles to own long-term",
            "Large dealer and service network",
        ],
        photo_query="2024 Honda CR-V exterior interior",
        preferred_trim_keywords=["ex", "ex-l", "sport", "touring"],
    ),
    VehicleConfig(
        key="toyota_rav4",
        make="Toyota",
        model="RAV4",
        years="2022,2023,2024,2025",
        category="Compact SUV",
        mpg_note="30+ mpg highway",
        notes=[
            "Excellent reliability",
            "Huge used market",
            "Lower maintenance costs",
            "Strong resale value",
        ],
        photo_query="2024 Toyota RAV4 exterior interior",
        preferred_trim_keywords=["xle", "xle premium", "limited", "adventure"],
    ),
    VehicleConfig(
        key="toyota_highlander",
        make="Toyota",
        model="Highlander",
        years="2021,2022,2023,2024",
        category="Midsize SUV",
        mpg_note="Around 25 mpg combined",
        notes=[
            "Very reliable",
            "Comfortable ride",
            "Available AWD",
            "Good choice if she needs more room than a compact SUV",
        ],
        photo_query="2024 Toyota Highlander exterior interior",
        preferred_trim_keywords=["xle", "limited", "platinum"],
    ),
    VehicleConfig(
        key="mazda_cx70",
        make="Mazda",
        model="CX-70",
        years="2025,2026",
        category="Midsize SUV",
        mpg_note="Mid-20s mpg",
        notes=[
            "Excellent value",
            "More engaging to drive than most midsize SUVs",
            "Premium-feeling interior",
            "Newer model, so used inventory may be thinner",
        ],
        photo_query="2025 Mazda CX-70 exterior interior",
        preferred_trim_keywords=["preferred", "premium", "premium plus", "s"],
    ),
]


def get_api_key() -> str:
    key = st.secrets.get("MARKETCHECK_API_KEY", "")
    if not key:
        st.error("Missing Streamlit secret: MARKETCHECK_API_KEY")
    return key


def safe_number(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except Exception:
        return None


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def extract_listing_url(item: Dict[str, Any]) -> str:
    media = item.get("media") or {}
    dealer = item.get("dealer") or {}
    return first_nonempty(
        item.get("vdp_url"),
        item.get("source_url"),
        item.get("listing_url"),
        item.get("dealer_vdp_url"),
        media.get("dealer_vdp_url"),
        dealer.get("website"),
        "",
    ) or ""


def extract_photo_url(item: Dict[str, Any]) -> str:
    media = item.get("media") or {}
    links = first_nonempty(media.get("photo_links"), item.get("photo_links"), []) or []
    if isinstance(links, str):
        parts = [p.strip() for p in re.split(r"[,|]", links) if p.strip()]
        return parts[0] if parts else ""
    if isinstance(links, list) and links:
        return str(links[0])
    return ""


def extract_market_value(item: Dict[str, Any]) -> Optional[float]:
    # Marketcheck accounts can expose valuation under different names depending on package/feed.
    return safe_number(first_nonempty(
        item.get("market_value"),
        item.get("base_market_value"),
        item.get("predicted_price"),
        item.get("price_stats", {}).get("mean") if isinstance(item.get("price_stats"), dict) else None,
    ))


def bool_from_any(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "clean", "one owner", "one-owner"}


def is_clean_title(item: Dict[str, Any]) -> bool:
    title = str(first_nonempty(item.get("title_type"), item.get("title"), item.get("vehicle_history", {}).get("title"), "")).lower()
    if "salvage" in title or "rebuilt" in title or "branded" in title or "lemon" in title:
        return False
    if "clean" in title:
        return True
    # When not provided, do not punish too hard in scoring; return False only for explicit positive flag checks.
    return bool_from_any(first_nonempty(item.get("clean_title"), item.get("is_clean_title")))


def is_one_owner(item: Dict[str, Any]) -> bool:
    vh = item.get("vehicle_history") or {}
    return bool_from_any(first_nonempty(item.get("one_owner"), item.get("is_one_owner"), vh.get("one_owner"), vh.get("owner_count") == 1))


def has_awd(item: Dict[str, Any]) -> bool:
    text = " ".join(str(first_nonempty(item.get(k), "")) for k in ["drivetrain", "drive_train", "trim", "version"])
    return any(token in text.upper() for token in ["AWD", "4WD", "ALL WHEEL", "FOUR WHEEL"])


def build_carfax_url(vin: str) -> str:
    if not vin:
        return ""
    return f"https://www.carfax.com/VehicleHistory/p/Report.cfx?partner=DVW_1&vin={vin}"


def google_photos_url(query: str) -> str:
    return f"https://www.google.com/search?tbm=isch&q={quote_plus(query)}"


def marketcheck_search(vehicle: VehicleConfig, zip_code: str, radius: int, rows: int, gas_only: bool) -> List[Dict[str, Any]]:
    api_key = get_api_key()
    if not api_key:
        return []

    params = {
        "api_key": api_key,
        "car_type": "used",
        "make": vehicle.make,
        "model": vehicle.model,
        "year": vehicle.years,
        "zip": zip_code,
        "radius": radius,
        "rows": rows,
        "sort_by": "price",
        "sort_order": "asc",
    }

    # Helps keep this aligned to the original non-hybrid/good gas search.
    # If your account's API is strict on fuel_type capitalization, change to "Gasoline" or remove this.
    if gas_only:
        params["fuel_type"] = "Gasoline"

    response = requests.get(MARKETCHECK_ENDPOINT, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    return data.get("listings", []) or data.get("results", []) or []


def normalize_listings(raw: List[Dict[str, Any]], vehicle: VehicleConfig) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for item in raw:
        vin = str(first_nonempty(item.get("vin"), ""))
        dealer = item.get("dealer") or {}
        price = safe_number(item.get("price"))
        market_value = extract_market_value(item)
        miles = safe_number(first_nonempty(item.get("miles"), item.get("mileage")))
        distance = safe_number(first_nonempty(item.get("distance"), item.get("dist")))
        city_mpg = safe_number(first_nonempty(item.get("city_mpg"), item.get("build", {}).get("city_mpg") if isinstance(item.get("build"), dict) else None))
        hwy_mpg = safe_number(first_nonempty(item.get("highway_mpg"), item.get("hwy_mpg"), item.get("build", {}).get("highway_mpg") if isinstance(item.get("build"), dict) else None))
        trim = str(first_nonempty(item.get("trim"), item.get("version"), item.get("build", {}).get("trim") if isinstance(item.get("build"), dict) else ""))
        listing_url = extract_listing_url(item)
        photo_url = extract_photo_url(item)

        rows.append({
            "Vehicle": f"{vehicle.make} {vehicle.model}",
            "Year": item.get("year"),
            "Make": vehicle.make,
            "Model": vehicle.model,
            "Trim": trim,
            "Price": price,
            "Market Value": market_value,
            "Price Gap": (market_value - price) if price is not None and market_value is not None else np.nan,
            "Miles": miles,
            "Distance": distance,
            "City MPG": city_mpg,
            "Hwy MPG": hwy_mpg,
            "AWD/4WD": has_awd(item),
            "One Owner": is_one_owner(item),
            "Clean Title": is_clean_title(item),
            "Dealer": first_nonempty(dealer.get("name"), item.get("dealer_name"), ""),
            "City": first_nonempty(dealer.get("city"), item.get("dealer_city"), ""),
            "State": first_nonempty(dealer.get("state"), item.get("dealer_state"), ""),
            "VIN": vin,
            "Listing": listing_url,
            "Photo": photo_url,
            "CARFAX": build_carfax_url(vin),
        })
    return pd.DataFrame(rows)


def add_score(df: pd.DataFrame, vehicle: VehicleConfig) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()

    price = pd.to_numeric(out["Price"], errors="coerce")
    market = pd.to_numeric(out["Market Value"], errors="coerce")
    miles = pd.to_numeric(out["Miles"], errors="coerce")
    distance = pd.to_numeric(out["Distance"], errors="coerce")
    hwy_mpg = pd.to_numeric(out["Hwy MPG"], errors="coerce")

    # Value score: biggest driver is discount versus Market Value when available.
    # If market value is missing, fall back to price percentile within that model's results.
    value_pct = (market - price) / market.replace(0, np.nan)
    value_score = (value_pct.clip(-0.20, 0.20).fillna(0) + 0.20) / 0.40 * 40

    if value_score.isna().all() or (value_score.fillna(0) == 20).all():
        price_rank = price.rank(pct=True, ascending=False)  # cheaper = better
        value_score = price_rank.fillna(0.5) * 35

    mileage_score = (1 - miles.rank(pct=True, ascending=True)).fillna(0.5) * 20
    distance_score = (1 - distance.rank(pct=True, ascending=True)).fillna(0.5) * 7
    mpg_score = hwy_mpg.rank(pct=True, ascending=True).fillna(0.5) * 5
    awd_score = out["AWD/4WD"].astype(bool).astype(int) * 8
    one_owner_score = out["One Owner"].astype(bool).astype(int) * 10
    clean_title_score = out["Clean Title"].astype(bool).astype(int) * 8

    trim_text = out["Trim"].fillna("").str.lower()
    preferred_score = trim_text.apply(lambda t: any(k in t for k in vehicle.preferred_trim_keywords)).astype(int) * 2

    out["Score"] = (
        value_score.fillna(0)
        + mileage_score.fillna(0)
        + distance_score.fillna(0)
        + mpg_score.fillna(0)
        + awd_score
        + one_owner_score
        + clean_title_score
        + preferred_score
    ).round(1)

    out["Rank"] = out["Score"].rank(method="first", ascending=False).astype(int)
    out = out.sort_values(["Score", "Price Gap", "Miles"], ascending=[False, False, True])
    out["Rank"] = range(1, len(out) + 1)
    return out


def format_money(value: Any) -> str:
    n = safe_number(value)
    return "" if n is None or math.isnan(n) else f"${n:,.0f}"


def format_miles(value: Any) -> str:
    n = safe_number(value)
    return "" if n is None or math.isnan(n) else f"{n:,.0f}"


def display_table(df: pd.DataFrame) -> None:
    if df.empty:
        st.info("No listings found for this section with the current filters.")
        return

    show = df.head(10).copy()
    cols = [
        "Rank", "Score", "Year", "Trim", "Price", "Market Value", "Price Gap", "Miles", "Distance",
        "AWD/4WD", "One Owner", "Clean Title", "Dealer", "City", "State", "Listing", "CARFAX", "Photo",
    ]
    show = show[[c for c in cols if c in show.columns]]

    for col in ["Price", "Market Value", "Price Gap"]:
        if col in show:
            show[col] = show[col].apply(format_money)
    for col in ["Miles", "Distance"]:
        if col in show:
            show[col] = show[col].apply(format_miles)

    st.dataframe(
        show,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Listing": st.column_config.LinkColumn("Listing"),
            "CARFAX": st.column_config.LinkColumn("CARFAX"),
            "Photo": st.column_config.LinkColumn("Photo"),
            "Score": st.column_config.NumberColumn("Score", format="%.1f"),
        },
    )


def render_vehicle_section(vehicle: VehicleConfig, zip_code: str, radius: int, rows: int, gas_only: bool) -> pd.DataFrame:
    st.subheader(f"{vehicle.make} {vehicle.model}")
    st.caption(f"{vehicle.category} • {vehicle.mpg_note}")

    left, right = st.columns([2, 1])
    with left:
        for note in vehicle.notes:
            st.write(f"• {note}")
    with right:
        st.link_button("Pictures", google_photos_url(vehicle.photo_query))
        st.link_button("Search on Cars.com", f"https://www.cars.com/shopping/results/?makes[]={quote_plus(vehicle.make.lower())}&models[]={quote_plus(vehicle.make.lower() + '-' + vehicle.model.lower().replace(' ', '_'))}&maximum_distance={radius}&zip={quote_plus(zip_code)}")

    try:
        raw = marketcheck_search(vehicle, zip_code, radius, rows, gas_only)
        df = add_score(normalize_listings(raw, vehicle), vehicle)
        display_table(df)
        return df
    except requests.HTTPError as exc:
        st.error(f"Marketcheck API error for {vehicle.make} {vehicle.model}: {exc}")
    except Exception as exc:
        st.error(f"Could not load {vehicle.make} {vehicle.model}: {exc}")
    return pd.DataFrame()


def main() -> None:
    st.set_page_config(page_title="Sister SUV Search", layout="wide")
    st.title("Sister SUV Search: Reliable, Good Gas Mileage, Non-Hybrid")
    st.write(
        "This page searches your SUV shortlist and ranks the best used listings by value, miles, ownership/title signals, distance, AWD, and MPG."
    )

    with st.sidebar:
        st.header("Search Settings")
        zip_code = st.text_input("ZIP", DEFAULT_ZIP)
        radius = st.slider("Radius", 25, 500, DEFAULT_RADIUS, step=25)
        rows = st.slider("Listings to pull per model", 10, 100, DEFAULT_ROWS_PER_MODEL, step=10)
        gas_only = st.checkbox("Gas only / exclude hybrid where possible", value=True)
        st.markdown("---")
        st.caption("Scoring: price vs market value, miles, one-owner, clean title, distance, AWD, MPG, and useful trims.")

    all_results: List[pd.DataFrame] = []

    for vehicle in VEHICLES:
        with st.container(border=True):
            df = render_vehicle_section(vehicle, zip_code, radius, rows, gas_only)
            if not df.empty:
                all_results.append(df)

    if all_results:
        combined = pd.concat(all_results, ignore_index=True)
        combined = combined.sort_values("Score", ascending=False)
        st.header("Overall Top Ranked Listings")
        display_table(combined)
        csv = combined.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download all results CSV",
            data=csv,
            file_name="sister_suv_marketcheck_results.csv",
            mime="text/csv",
        )


if __name__ == "__main__":
    main()
