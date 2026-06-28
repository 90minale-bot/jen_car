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
DEFAULT_ZIP = "17601"
DEFAULT_RADIUS = 50
MAX_RADIUS = 100
DEFAULT_ROWS_PER_MODEL = 50
DEFAULT_MAX_PRICE = 29000
DEFAULT_MAX_MILES = 40000
DEFAULT_MIN_YEAR_FILTER = 2022
DEFAULT_MAX_YEAR_FILTER = 2026


@dataclass(frozen=True)
class VehicleConfig:
    key: str
    make: str
    model: str
    min_year: int
    max_year: int
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
        min_year=2022,
        max_year=2026,
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
        min_year=2022,
        max_year=2026,
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
        min_year=2022,
        max_year=2026,
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
        min_year=2022,
        max_year=2026,
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
        min_year=2025,
        max_year=2026,
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


def deep_get(d: Dict[str, Any], *path: str) -> Any:
    """Safely read nested Marketcheck fields."""
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


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
    """Extract a true market-value field when MarketCheck provides one.

    Important: do NOT use MSRP, list price, or current price as market value.
    Some dealer feeds set MSRP/list price equal to asking price on used cars,
    which makes every Price Gap show $0. If no true valuation field is present,
    the app calculates a comparable-market fallback after all listings load.
    """
    price_stats = item.get("price_stats") if isinstance(item.get("price_stats"), dict) else {}
    stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
    pricing = item.get("pricing") if isinstance(item.get("pricing"), dict) else {}
    price_analysis = item.get("price_analysis") if isinstance(item.get("price_analysis"), dict) else {}
    valuation = item.get("valuation") if isinstance(item.get("valuation"), dict) else {}

    return safe_number(first_nonempty(
        item.get("market_value"),
        item.get("base_market_value"),
        item.get("fair_market_value"),
        item.get("predicted_market_price"),
        item.get("predicted_price"),
        item.get("marketcheck_price"),
        pricing.get("market_value"),
        pricing.get("fair_market_value"),
        pricing.get("predicted_market_price"),
        price_analysis.get("market_value"),
        price_analysis.get("market_average"),
        price_analysis.get("average_price"),
        price_analysis.get("avg_price"),
        valuation.get("market_value"),
        valuation.get("marketcheck_price"),
        price_stats.get("mean"),
        price_stats.get("average"),
        stats.get("price_mean"),
        stats.get("price_avg"),
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
    build = item.get("build") if isinstance(item.get("build"), dict) else {}
    text = " ".join(str(first_nonempty(item.get(k), build.get(k), "")) for k in ["drivetrain", "drive_train", "trim", "version", "series"] )
    text += " " + str(first_nonempty(build.get("drivetrain"), build.get("drive_train"), build.get("body_type"), ""))
    return any(token in text.upper() for token in ["AWD", "4WD", "ALL WHEEL", "FOUR WHEEL"])


def build_carfax_url(vin: str) -> str:
    if not vin:
        return ""
    return f"https://www.carfax.com/VehicleHistory/p/Report.cfx?partner=DVW_1&vin={vin}"


def google_photos_url(query: str) -> str:
    return f"https://www.google.com/search?tbm=isch&q={quote_plus(query)}"


def marketcheck_search(vehicle: VehicleConfig, zip_code: str, radius: int, rows: int, max_price: int, max_miles: int, year_max_filter: int) -> List[Dict[str, Any]]:
    """Search MarketCheck using the same parameter style as the working Jen/QX60 dashboard.

    Important fixes from the first SUV version:
    - MarketCheck rejected comma-separated year lists such as year=2022,2023,2024.
      Use year_min/year_max instead.
    - Your working dashboard limits radius to 100 miles. Keep that here too.
    - MarketCheck commonly caps rows at 50 per request.
    """
    api_key = get_api_key()
    if not api_key:
        return []

    safe_radius = max(10, min(int(radius), 100))
    safe_rows = max(1, min(int(rows), 50))
    safe_year_min = max(int(vehicle.min_year), DEFAULT_MIN_YEAR_FILTER)
    safe_year_max = min(int(vehicle.max_year), int(year_max_filter), DEFAULT_MAX_YEAR_FILTER)
    if safe_year_max < safe_year_min:
        return []

    params: Dict[str, Any] = {
        "api_key": api_key,
        "car_type": "used",
        "make": vehicle.make,
        "model": vehicle.model,
        "zip": zip_code,
        "radius": safe_radius,
        "year_min": safe_year_min,
        "year_max": safe_year_max,
        "price_max": int(max_price),
        "miles_max": int(max_miles),
        "mileage_max": int(max_miles),
        "stats": "price,miles",
        "rows": safe_rows,
        "start": 0,
    }

    response = requests.get(MARKETCHECK_ENDPOINT, params=params, timeout=30)

    if response.status_code != 200:
        try:
            payload = response.json()
        except Exception:
            payload = response.text[:1000]
        raise RuntimeError(f"MarketCheck API returned HTTP {response.status_code}: {payload}")

    data = response.json()
    listings = data.get("listings", []) or data.get("results", []) or []

    # Local year cleanup in case the API ignores year_max for an account.
    cleaned = []
    for item in listings:
        build = item.get("build") if isinstance(item.get("build"), dict) else {}
        year = safe_number(first_nonempty(item.get("year"), build.get("year"), item.get("model_year")))
        if year is None or safe_year_min <= int(year) <= safe_year_max:
            cleaned.append(item)

    return cleaned


def parse_year_from_text(*values: Any) -> Optional[float]:
    for value in values:
        text = str(value or "")
        match = re.search(r"\b(20[0-3][0-9]|19[8-9][0-9])\b", text)
        if match:
            return float(match.group(1))
    return None



def same_money(a: Any, b: Any) -> bool:
    """Return True when two money values are effectively the same."""
    x = safe_number(a)
    y = safe_number(b)
    if x is None or y is None:
        return False
    try:
        return abs(float(x) - float(y)) < 1
    except Exception:
        return False


def apply_comparable_market_values(df: pd.DataFrame) -> pd.DataFrame:
    """Fill missing or suspicious market values using returned comparable listings.

    MarketCheck active-search listings sometimes do not include a true per-listing FMV.
    When that happens, we estimate market value from the actual returned inventory:
      1) same vehicle + year + trim median, when there are at least 3 comps
      2) same vehicle + year median, when there are at least 3 comps
      3) same vehicle median

    This avoids the bad $0-gap behavior caused by treating MSRP/list price as FMV.
    """
    if df.empty or "Price" not in df.columns:
        return df

    out = df.copy()
    out["Price"] = pd.to_numeric(out["Price"], errors="coerce")
    out["Market Value"] = pd.to_numeric(out.get("Market Value", np.nan), errors="coerce")

    # If almost every non-null market value equals asking price, treat the feed as not providing true FMV.
    non_null = out.dropna(subset=["Price", "Market Value"])
    suspicious_equal_rate = 0.0
    if len(non_null):
        suspicious_equal_rate = (non_null.apply(lambda r: same_money(r["Price"], r["Market Value"]), axis=1)).mean()
    force_comp_fallback = suspicious_equal_rate >= 0.80

    def group_median(keys: List[str]) -> pd.Series:
        usable_keys = [k for k in keys if k in out.columns]
        if not usable_keys:
            return pd.Series(np.nan, index=out.index)
        counts = out.groupby(usable_keys)["Price"].transform("count")
        medians = out.groupby(usable_keys)["Price"].transform("median")
        return medians.where(counts >= 3)

    trim_comp = group_median(["Vehicle", "Year", "Trim"])
    year_comp = group_median(["Vehicle", "Year"])
    model_comp = group_median(["Vehicle"])
    fallback = trim_comp.fillna(year_comp).fillna(model_comp)

    if "Market Value Source" not in out.columns:
        out["Market Value Source"] = "MarketCheck"

    need_fallback = out["Market Value"].isna() | force_comp_fallback | out.apply(lambda r: same_money(r.get("Price"), r.get("Market Value")), axis=1)
    can_fill = need_fallback & fallback.notna()
    out.loc[can_fill, "Market Value"] = fallback.loc[can_fill]
    out.loc[can_fill, "Market Value Source"] = "Comparable median"
    out.loc[~can_fill & out["Market Value"].notna(), "Market Value Source"] = "MarketCheck"

    out["Price Gap"] = out["Market Value"] - out["Price"]
    return out

def normalize_listings(raw: List[Dict[str, Any]], vehicle: VehicleConfig) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for item in raw:
        build = item.get("build") if isinstance(item.get("build"), dict) else {}
        dealer = item.get("dealer") if isinstance(item.get("dealer"), dict) else {}
        inventory_type = item.get("inventory_type") if isinstance(item.get("inventory_type"), dict) else {}

        vin = str(first_nonempty(item.get("vin"), build.get("vin"), ""))
        price = safe_number(first_nonempty(
            item.get("price"), item.get("asking_price"), item.get("list_price"),
            item.get("sale_price"), item.get("current_price"),
            deep_get(item, "inventory", "price"), deep_get(item, "pricing", "price"),
            deep_get(item, "price_details", "price"), deep_get(item, "price_details", "list_price"),
        ))
        market_value = extract_market_value(item)
        miles = safe_number(first_nonempty(
            item.get("miles"), item.get("mileage"), item.get("odometer"),
            inventory_type.get("miles"), deep_get(item, "inventory", "miles"),
            deep_get(item, "inventory", "mileage"),
        ))
        distance = safe_number(first_nonempty(item.get("distance"), item.get("dist"), dealer.get("distance"), deep_get(item, "dealer", "dist")))
        city_mpg = safe_number(first_nonempty(item.get("city_mpg"), build.get("city_mpg"), build.get("city_miles")))
        hwy_mpg = safe_number(first_nonempty(item.get("highway_mpg"), item.get("hwy_mpg"), build.get("highway_mpg"), build.get("highway_miles")))
        year = safe_number(first_nonempty(item.get("year"), build.get("year"), item.get("model_year"), deep_get(item, "inventory", "year")))
        if year is None:
            year = parse_year_from_text(
                item.get("heading"), item.get("title"), item.get("vehicle_title"),
                item.get("name"), build.get("name"), extract_listing_url(item)
            )
        trim = str(first_nonempty(item.get("trim"), item.get("version"), build.get("trim"), build.get("version"), build.get("series"), build.get("style"), ""))
        listing_url = extract_listing_url(item)
        photo_url = extract_photo_url(item)

        rows.append({
            "Vehicle": f"{vehicle.make} {vehicle.model}",
            "Year": year,
            "Make": first_nonempty(item.get("make"), build.get("make"), vehicle.make),
            "Model": first_nonempty(item.get("model"), build.get("model"), vehicle.model),
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
            "Dealer": first_nonempty(dealer.get("name"), item.get("dealer_name"), item.get("seller_name"), ""),
            "City": first_nonempty(dealer.get("city"), item.get("dealer_city"), ""),
            "State": first_nonempty(dealer.get("state"), item.get("dealer_state"), ""),
            "VIN": vin,
            "Listing": listing_url,
            "Photo": photo_url,
            "CARFAX": build_carfax_url(vin),
        })
    df = pd.DataFrame(rows)
    return apply_comparable_market_values(df)


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
        "AWD/4WD", "One Owner", "Clean Title", "Market Value Source", "Dealer", "City", "State", "Listing", "CARFAX", "Photo",
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


def render_vehicle_section(vehicle: VehicleConfig, zip_code: str, radius: int, rows: int, max_price: int, max_miles: int, year_max_filter: int) -> pd.DataFrame:
    radius = max(10, min(int(radius), MAX_RADIUS))
    st.subheader(f"{vehicle.make} {vehicle.model}")
    st.caption(f"{vehicle.category} • {vehicle.mpg_note} • Years {max(vehicle.min_year, DEFAULT_MIN_YEAR_FILTER)}–{min(vehicle.max_year, year_max_filter, DEFAULT_MAX_YEAR_FILTER)}")

    left, right = st.columns([2, 1])
    with left:
        for note in vehicle.notes:
            st.write(f"• {note}")
    with right:
        st.link_button("Pictures", google_photos_url(vehicle.photo_query))
        st.link_button("Search on Cars.com", f"https://www.cars.com/shopping/results/?makes[]={quote_plus(vehicle.make.lower())}&models[]={quote_plus(vehicle.make.lower() + '-' + vehicle.model.lower().replace(' ', '_'))}&maximum_distance={radius}&zip={quote_plus(zip_code)}")

    try:
        raw = marketcheck_search(vehicle, zip_code, radius, rows, max_price, max_miles, year_max_filter)
        df = add_score(normalize_listings(raw, vehicle), vehicle)
        if not df.empty:
            if "Price" in df.columns:
                df = df[pd.to_numeric(df["Price"], errors="coerce").fillna(10**12) <= int(max_price)]
            if "Miles" in df.columns:
                df = df[pd.to_numeric(df["Miles"], errors="coerce").fillna(10**12) <= int(max_miles)]
            if "Year" in df.columns:
                df = df[pd.to_numeric(df["Year"], errors="coerce").fillna(0).between(DEFAULT_MIN_YEAR_FILTER, int(year_max_filter))]
        if not raw:
            st.warning("Marketcheck returned no raw listings for this model.")
        display_table(df)
        return df
    except requests.HTTPError as exc:
        st.error(f"Marketcheck API error for {vehicle.make} {vehicle.model}: {exc}")
    except Exception as exc:
        st.error(f"Could not load {vehicle.make} {vehicle.model}: {exc}")
    return pd.DataFrame()



def show_scoring_weights() -> None:
    """Show the scoring weights used to rank listings."""
    with st.expander("Scoring weights used for ranking", expanded=True):
        st.markdown(
            """
            The score is a **0–100 style ranking**. Higher is better. It is meant to prioritize vehicles
            that look like the best value, but it does **not** replace checking the listing, CARFAX,
            title history, accident history, service records, or getting a pre-purchase inspection.
            """
        )

        weights = pd.DataFrame(
            [
                {
                    "Factor": "Value vs market / price",
                    "Max Points": 40,
                    "How it helps": "Bigger discount versus MarketCheck market value. If the feed does not provide true FMV, the app uses returned comparable-listing medians instead of forcing $0 gaps.",
                },
                {
                    "Factor": "Mileage",
                    "Max Points": 20,
                    "How it helps": "Lower mileage gets more points.",
                },
                {
                    "Factor": "One owner",
                    "Max Points": 10,
                    "How it helps": "Listings marked one-owner receive bonus points.",
                },
                {
                    "Factor": "Clean title",
                    "Max Points": 8,
                    "How it helps": "Listings marked clean title receive bonus points; salvage/rebuilt/branded/lemon language is not rewarded.",
                },
                {
                    "Factor": "AWD / 4WD",
                    "Max Points": 8,
                    "How it helps": "AWD or 4WD vehicles receive bonus points.",
                },
                {
                    "Factor": "Distance",
                    "Max Points": 7,
                    "How it helps": "Closer listings rank higher.",
                },
                {
                    "Factor": "Highway MPG",
                    "Max Points": 5,
                    "How it helps": "Better highway MPG gets more points when the API provides MPG data.",
                },
                {
                    "Factor": "Preferred trim keywords",
                    "Max Points": 2,
                    "How it helps": "Small bonus for trims we flagged as desirable for each model.",
                },
            ]
        )

        st.dataframe(weights, use_container_width=True, hide_index=True)
        st.caption("Total possible points: 100")


def main() -> None:
    st.set_page_config(page_title="Sister SUV Search", layout="wide")
    st.title("Sister SUV Search: Reliable, Good Gas Mileage — v7")
    st.write(
        "This page searches your SUV shortlist and ranks the best used listings by value, miles, ownership/title signals, distance, AWD, and MPG. v7 adds a year filter so you can cap the newest model year included."
    )

    show_scoring_weights()

    with st.sidebar:
        st.header("Search Settings")
        zip_code = st.text_input("ZIP", DEFAULT_ZIP)
        radius = st.number_input("Search range miles", min_value=10, max_value=MAX_RADIUS, value=DEFAULT_RADIUS, step=10, help="MarketCheck API maximum search range is 100 miles. Default is 50.")
        max_price = st.number_input(
            "Max cost",
            min_value=0,
            max_value=40000,
            value=DEFAULT_MAX_PRICE,
            step=1000,
            help="Default max cost is $26,000. You can raise it up to $40,000.",
        )
        max_miles = st.number_input(
            "Max mileage",
            min_value=0,
            max_value=100000,
            value=DEFAULT_MAX_MILES,
            step=5000,
            help="Default max mileage is 40,000 miles. You can raise it up to 100,000 miles.",
        )
        year_max_filter = st.slider(
            "Newest model year to include",
            min_value=DEFAULT_MIN_YEAR_FILTER,
            max_value=DEFAULT_MAX_YEAR_FILTER,
            value=DEFAULT_MAX_YEAR_FILTER,
            step=1,
            help="Default includes 2022–2026. Slide left to remove newer model years, for example set to 2024 to exclude 2025 and 2026.",
        )
        rows = st.slider("Listings to pull per model", 10, 50, DEFAULT_ROWS_PER_MODEL, step=10, help="MarketCheck commonly caps rows at 50 per request.")
        st.markdown("---")
        st.caption("Scoring: price vs market value, miles, one-owner, clean title, distance, AWD, MPG, and useful trims. Range defaults to 50 miles and maxes at 100. Cost defaults to $26k and mileage defaults to 40k max. Year filter defaults to 2022–2026; slide left to eliminate newer years.")

    all_results: List[pd.DataFrame] = []

    for vehicle in VEHICLES:
        with st.container(border=True):
            df = render_vehicle_section(vehicle, zip_code, radius, rows, max_price, max_miles, year_max_filter)
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
