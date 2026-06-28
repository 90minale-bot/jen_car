"""
Sister SUV Auto.dev Dashboard
-----------------------------
Streamlit page that searches Auto.dev for the SUV list you built:
- Mazda CX-5
- Honda CR-V
- Toyota RAV4
- Toyota Highlander
- Mazda CX-70

Setup:
1) pip install streamlit pandas requests numpy
2) Add this to Streamlit secrets:
   AUTODEV_API_KEY = "your_auto_dev_key_here"
   # AUTO_DEV_API_KEY also works if you prefer that name.
3) Run locally:
   streamlit run sister_suv_autodev_dashboard_v1.py
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
import requests
import streamlit as st


AUTODEV_ENDPOINT = "https://api.auto.dev/listings"
DEFAULT_ZIP = "17601"
DEFAULT_RADIUS = 50
MAX_RADIUS = 100
DEFAULT_ROWS_PER_MODEL = 50
DEFAULT_MAX_PRICE = 28000
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
    """Read Auto.dev API key from Streamlit secrets or local environment."""
    for name in ("AUTODEV_API_KEY", "AUTO_DEV_API_KEY"):
        try:
            key = st.secrets.get(name, "")
            if key:
                return str(key)
        except Exception:
            pass
        key = os.getenv(name, "")
        if key:
            return str(key)
    st.error("Missing Streamlit secret: AUTODEV_API_KEY")
    return ""


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
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def bool_from_any(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "clean", "one owner", "one-owner"}


def has_awd(item: Dict[str, Any]) -> bool:
    vehicle = item.get("vehicle") if isinstance(item.get("vehicle"), dict) else {}
    retail = item.get("retailListing") if isinstance(item.get("retailListing"), dict) else {}
    text = " ".join(
        str(first_nonempty(
            item.get(k),
            vehicle.get(k),
            retail.get(k),
            "",
        ))
        for k in ["drivetrain", "driveTrain", "drive_train", "trim", "engine", "fuel", "description"]
    )
    return any(token in text.upper() for token in ["AWD", "4WD", "ALL WHEEL", "FOUR WHEEL"])


def is_clean_title(item: Dict[str, Any]) -> bool:
    history = item.get("history") if isinstance(item.get("history"), dict) else {}
    title = str(first_nonempty(
        item.get("title_type"),
        item.get("title"),
        history.get("title"),
        history.get("titleStatus"),
        "",
    )).lower()
    if any(bad in title for bad in ["salvage", "rebuilt", "branded", "lemon"]):
        return False
    if "clean" in title:
        return True
    return bool_from_any(first_nonempty(item.get("cleanTitle"), item.get("clean_title"), history.get("cleanTitle")))


def is_one_owner(item: Dict[str, Any]) -> bool:
    history = item.get("history") if isinstance(item.get("history"), dict) else {}
    return bool_from_any(first_nonempty(
        item.get("oneOwner"),
        item.get("one_owner"),
        history.get("oneOwner"),
        history.get("ownerCount") == 1,
    ))


def google_photos_url(query: str) -> str:
    return f"https://www.google.com/search?tbm=isch&q={quote_plus(query)}"


def build_carfax_url(vin: str, provided: str = "") -> str:
    if provided:
        return provided
    if not vin:
        return ""
    return f"https://www.carfax.com/VehicleHistory/p/Report.cfx?vin={vin}"


def autodev_search(vehicle: VehicleConfig, zip_code: str, radius: int, rows: int, max_price: int, max_miles: int, year_max_filter: int) -> List[Dict[str, Any]]:
    api_key = get_api_key()
    if not api_key:
        return []

    safe_radius = max(10, min(int(radius), MAX_RADIUS))
    safe_rows = max(1, min(int(rows), 500))
    safe_year_min = max(int(vehicle.min_year), DEFAULT_MIN_YEAR_FILTER)
    safe_year_max = min(int(vehicle.max_year), int(year_max_filter), DEFAULT_MAX_YEAR_FILTER)
    if safe_year_max < safe_year_min:
        return []

    params: Dict[str, Any] = {
        "page": 1,
        "limit": safe_rows,
        "sort": "updatedAt.desc",
        "vehicle.make": vehicle.make,
        "vehicle.model": vehicle.model,
        "vehicle.year": f"{safe_year_min}-{safe_year_max}",
        "retailListing.price": f"1-{int(max_price)}",
        "retailListing.miles": f"0-{int(max_miles)}",
        "retailListing.used": "true",
        "zip": zip_code,
        "distance": safe_radius,
        "includeUnpriced": "false",
        "includes": "total",
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    response = requests.get(AUTODEV_ENDPOINT, params=params, headers=headers, timeout=30)
    if response.status_code != 200:
        try:
            payload = response.json()
        except Exception:
            payload = response.text[:1000]
        raise RuntimeError(f"Auto.dev API returned HTTP {response.status_code}: {payload}")

    data = response.json()
    listings = data.get("data", [])
    if not isinstance(listings, list):
        return []

    # Local cleanup in case a plan/account ignores a filter.
    cleaned: List[Dict[str, Any]] = []
    for item in listings:
        year = safe_number(first_nonempty(deep_get(item, "vehicle", "year"), item.get("year")))
        price = safe_number(first_nonempty(deep_get(item, "retailListing", "price"), item.get("price")))
        miles = safe_number(first_nonempty(deep_get(item, "retailListing", "miles"), item.get("miles"), item.get("mileage")))
        if year is not None and not (safe_year_min <= int(year) <= safe_year_max):
            continue
        if price is not None and price > int(max_price):
            continue
        if miles is not None and miles > int(max_miles):
            continue
        cleaned.append(item)
    return cleaned


def normalize_listings(raw: List[Dict[str, Any]], vehicle_config: VehicleConfig) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for item in raw:
        vehicle = item.get("vehicle") if isinstance(item.get("vehicle"), dict) else {}
        retail = item.get("retailListing") if isinstance(item.get("retailListing"), dict) else {}
        history = item.get("history") if isinstance(item.get("history"), dict) else {}

        vin = str(first_nonempty(item.get("vin"), vehicle.get("vin"), ""))
        price = safe_number(first_nonempty(retail.get("price"), item.get("price")))
        miles = safe_number(first_nonempty(retail.get("miles"), item.get("miles"), item.get("mileage")))
        year = safe_number(first_nonempty(vehicle.get("year"), item.get("year")))
        trim = str(first_nonempty(vehicle.get("trim"), item.get("trim"), ""))
        listing_url = str(first_nonempty(retail.get("vdp"), retail.get("url"), item.get("vdp"), item.get("url"), item.get("@id"), ""))
        photo_url = str(first_nonempty(retail.get("primaryImage"), retail.get("image"), item.get("primaryImage"), ""))
        carfax = str(first_nonempty(retail.get("carfaxUrl"), item.get("carfaxUrl"), ""))

        # Auto.dev listings are not guaranteed to include MPG or FMV in the default response.
        city_mpg = safe_number(first_nonempty(vehicle.get("cityMpg"), vehicle.get("city_mpg"), item.get("city_mpg")))
        hwy_mpg = safe_number(first_nonempty(vehicle.get("highwayMpg"), vehicle.get("highway_mpg"), item.get("highway_mpg")))
        market_value = safe_number(first_nonempty(
            item.get("marketValue"),
            item.get("market_value"),
            deep_get(item, "retailListing", "marketValue"),
            deep_get(item, "pricing", "marketValue"),
            deep_get(item, "valuation", "marketValue"),
        ))

        rows.append({
            "Vehicle": f"{vehicle_config.make} {vehicle_config.model}",
            "Year": year,
            "Make": first_nonempty(vehicle.get("make"), vehicle_config.make),
            "Model": first_nonempty(vehicle.get("model"), vehicle_config.model),
            "Trim": trim,
            "Price": price,
            "Market Value": market_value,
            "Price Gap": (market_value - price) if price is not None and market_value is not None else np.nan,
            "Miles": miles,
            "Distance": safe_number(first_nonempty(retail.get("distance"), item.get("distance"))),
            "City MPG": city_mpg,
            "Hwy MPG": hwy_mpg,
            "AWD/4WD": has_awd(item),
            "One Owner": is_one_owner(item),
            "Clean Title": is_clean_title(item),
            "Dealer": first_nonempty(retail.get("dealer"), item.get("dealer"), ""),
            "City": first_nonempty(retail.get("city"), item.get("city"), ""),
            "State": first_nonempty(retail.get("state"), item.get("state"), ""),
            "VIN": vin,
            "Listing": listing_url,
            "Photo": photo_url,
            "CARFAX": build_carfax_url(vin, carfax),
        })

    df = pd.DataFrame(rows)
    return apply_comparable_market_values(df)


def same_money(a: Any, b: Any) -> bool:
    x = safe_number(a)
    y = safe_number(b)
    if x is None or y is None:
        return False
    return abs(float(x) - float(y)) < 1


def apply_comparable_market_values(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty or "Price" not in df.columns:
        return df

    out = df.copy()
    out["Price"] = pd.to_numeric(out["Price"], errors="coerce")
    out["Market Value"] = pd.to_numeric(out.get("Market Value", np.nan), errors="coerce")

    non_null = out.dropna(subset=["Price", "Market Value"])
    suspicious_equal_rate = 0.0
    if len(non_null):
        suspicious_equal_rate = non_null.apply(lambda r: same_money(r["Price"], r["Market Value"]), axis=1).mean()
    force_comp_fallback = suspicious_equal_rate >= 0.80

    def group_median(keys: List[str]) -> pd.Series:
        usable = [k for k in keys if k in out.columns]
        if not usable:
            return pd.Series(np.nan, index=out.index)
        counts = out.groupby(usable)["Price"].transform("count")
        medians = out.groupby(usable)["Price"].transform("median")
        return medians.where(counts >= 3)

    trim_comp = group_median(["Vehicle", "Year", "Trim"])
    year_comp = group_median(["Vehicle", "Year"])
    model_comp = group_median(["Vehicle"])
    fallback = trim_comp.fillna(year_comp).fillna(model_comp)

    out["Market Value Source"] = "Auto.dev"
    need_fallback = out["Market Value"].isna() | force_comp_fallback | out.apply(lambda r: same_money(r.get("Price"), r.get("Market Value")), axis=1)
    can_fill = need_fallback & fallback.notna()
    out.loc[can_fill, "Market Value"] = fallback.loc[can_fill]
    out.loc[can_fill, "Market Value Source"] = "Comparable median"
    out["Price Gap"] = out["Market Value"] - out["Price"]
    return out


def add_score(df: pd.DataFrame, vehicle: VehicleConfig) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    price = pd.to_numeric(out["Price"], errors="coerce")
    market = pd.to_numeric(out["Market Value"], errors="coerce")
    miles = pd.to_numeric(out["Miles"], errors="coerce")
    distance = pd.to_numeric(out["Distance"], errors="coerce")
    hwy_mpg = pd.to_numeric(out["Hwy MPG"], errors="coerce")

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
        st.link_button("Search on Cars.com", f"https://www.cars.com/shopping/results/?maximum_distance={radius}&zip={quote_plus(zip_code)}&stock_type=used&makes[]={quote_plus(vehicle.make.lower())}&models[]={quote_plus(vehicle.make.lower() + '-' + vehicle.model.lower().replace(' ', '_'))}")

    try:
        raw = autodev_search(vehicle, zip_code, radius, rows, max_price, max_miles, year_max_filter)
        df = add_score(normalize_listings(raw, vehicle), vehicle)
        if not raw:
            st.warning("Auto.dev returned no raw listings for this model.")
        display_table(df)
        return df
    except Exception as exc:
        st.error(f"Could not load {vehicle.make} {vehicle.model}: {exc}")
    return pd.DataFrame()


def show_scoring_weights() -> None:
    with st.expander("Scoring weights used for ranking", expanded=True):
        st.markdown(
            """
            Higher is better. The score is meant to prioritize likely good values, but still check
            the listing, CARFAX, title history, accident history, service records, and get a pre-purchase inspection.
            """
        )
        weights = pd.DataFrame([
            {"Factor": "Value vs market / price", "Max Points": 40, "How it helps": "Bigger discount versus market/comparable median value. If Auto.dev does not return FMV, the app uses comparable returned-listing medians."},
            {"Factor": "Mileage", "Max Points": 20, "How it helps": "Lower mileage gets more points."},
            {"Factor": "One owner", "Max Points": 10, "How it helps": "Listings marked one-owner receive bonus points when Auto.dev provides the signal."},
            {"Factor": "Clean title", "Max Points": 8, "How it helps": "Clean-title signals receive bonus points; salvage/rebuilt/branded/lemon language is not rewarded."},
            {"Factor": "AWD / 4WD", "Max Points": 8, "How it helps": "AWD or 4WD vehicles receive bonus points."},
            {"Factor": "Distance", "Max Points": 7, "How it helps": "Closer listings rank higher when distance is returned."},
            {"Factor": "Highway MPG", "Max Points": 5, "How it helps": "Better highway MPG gets more points when available."},
            {"Factor": "Preferred trim keywords", "Max Points": 2, "How it helps": "Small bonus for trims flagged as desirable for each model."},
        ])
        st.dataframe(weights, use_container_width=True, hide_index=True)
        st.caption("Total possible points: 100")


def main() -> None:
    st.set_page_config(page_title="Sister SUV Search", layout="wide")
    st.title("Sister SUV Search: Reliable, Good Gas Mileage — Auto.dev v1")
    st.write(
        "This page searches your SUV shortlist using Auto.dev and ranks the best used listings by value, miles, ownership/title signals, distance, AWD, MPG, and useful trims."
    )

    show_scoring_weights()

    with st.sidebar:
        st.header("Search Settings")
        zip_code = st.text_input("ZIP", DEFAULT_ZIP)
        radius = st.number_input("Search range miles", min_value=10, max_value=MAX_RADIUS, value=DEFAULT_RADIUS, step=10)
        max_price = st.number_input("Max cost", min_value=0, max_value=40000, value=DEFAULT_MAX_PRICE, step=1000)
        max_miles = st.number_input("Max mileage", min_value=0, max_value=100000, value=DEFAULT_MAX_MILES, step=5000)
        year_max_filter = st.slider("Newest model year to include", min_value=DEFAULT_MIN_YEAR_FILTER, max_value=DEFAULT_MAX_YEAR_FILTER, value=DEFAULT_MAX_YEAR_FILTER, step=1)
        rows = st.slider("Listings to pull per model", 10, 100, DEFAULT_ROWS_PER_MODEL, step=10)
        st.markdown("---")
        st.caption("Auto.dev filters: vehicle.make, vehicle.model, vehicle.year, retailListing.price, retailListing.miles, zip, distance, and used=true.")

    all_results: List[pd.DataFrame] = []
    for vehicle in VEHICLES:
        with st.container(border=True):
            df = render_vehicle_section(vehicle, zip_code, radius, rows, max_price, max_miles, year_max_filter)
            if not df.empty:
                all_results.append(df)

    if all_results:
        combined = pd.concat(all_results, ignore_index=True).sort_values("Score", ascending=False)
        st.header("Overall Top Ranked Listings")
        display_table(combined)
        csv = combined.to_csv(index=False).encode("utf-8")
        st.download_button("Download all results CSV", data=csv, file_name="sister_suv_autodev_results.csv", mime="text/csv")


if __name__ == "__main__":
    main()
