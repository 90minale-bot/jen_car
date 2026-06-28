# app.py
# Car Deal Report Dashboard
# Release baseline: MarketCheck search, ranked results, CSV upload, and report export.
#
# Run locally:
#   streamlit run app.py
#
# Streamlit Cloud secrets:
#   MARKETCHECK_API_KEY = "your_key_here"
#
# Local .env option:
#   MARKETCHECK_API_KEY=your_key_here

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="Car Deal Report Dashboard",
    page_icon="car",
    layout="wide",
)


BASE_DIR = Path(__file__).resolve().parent
MARKETCHECK_ENDPOINT = "https://api.marketcheck.com/v2/search/car/active"
DEFAULT_ROWS_PER_PAGE = 50
DEFAULT_MAX_PAGES = 2
MAX_ALLOWED_PAGES = 5


# ------------------------------------------------------------
# API / config helpers
# ------------------------------------------------------------
def get_api_key() -> str:
    try:
        key = st.secrets.get("MARKETCHECK_API_KEY", "")
        if key:
            return str(key)
    except Exception:
        pass

    return os.getenv("MARKETCHECK_API_KEY", "")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def safe_num(value: Any, default: float | None = None) -> float | None:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def money(value: Any) -> str:
    if pd.isna(value):
        return ""
    try:
        return f"${float(value):,.0f}"
    except Exception:
        return str(value)


def number(value: Any) -> str:
    if pd.isna(value):
        return ""
    try:
        return f"{float(value):,.0f}"
    except Exception:
        return str(value)


# ------------------------------------------------------------
# MarketCheck API
# ------------------------------------------------------------
def build_marketcheck_params(
    api_key: str,
    make: str,
    model: str,
    zip_code: str,
    radius: int,
    min_year: int,
    max_price: int,
    max_miles: int,
    rows: int,
    car_type: str,
    fuel_filter: str = "Any",
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "api_key": api_key,
        "make": make,
        "model": model,
        "zip": zip_code,
        "radius": radius,
        "year_min": min_year,
        "price_max": max_price,
        # MarketCheck commonly supports miles_max, but some examples/accounts use mileage_max.
        # Send both so the API has the best chance of applying the filter.
        "miles_max": max_miles,
        "mileage_max": max_miles,
        "rows": rows,
        "start": 0,
    }

    if car_type and car_type.lower() != "any":
        params["car_type"] = car_type.lower()

    # MarketCheck supports a fuel_type vehicle-spec filter, but hybrid records are
    # inconsistent across dealer feeds. Some hybrids come back as "Hybrid",
    # some as "Electric / Unleaded", and some Toyota hybrids may only show
    # "Unleaded" while the trim/title says Hybrid.
    #
    # IMPORTANT: Do not send an API-level fuel_type filter for Hybrid. Pull the
    # broader result set first, then filter locally with is_hybrid_record().
    # This avoids accidentally filtering out true hybrids before we can inspect
    # trim/title/engine/powertrain text.
    if fuel_filter and fuel_filter.lower() != "any" and fuel_filter.lower() != "hybrid":
        fuel_lookup = {
            "gasoline": "Unleaded,Gasoline,Premium Unleaded",
            "diesel": "Diesel",
            "electric": "Electric",
            "premium unleaded": "Premium Unleaded",
            "electric / unleaded": "Electric / Unleaded",
        }
        params["fuel_type"] = fuel_lookup.get(fuel_filter.lower(), fuel_filter)

    return params


@st.cache_data(ttl=60 * 60, show_spinner=False)
def marketcheck_search(params: dict[str, Any], max_pages: int = DEFAULT_MAX_PAGES) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_listings: list[dict[str, Any]] = []
    last_response: dict[str, Any] = {}

    rows = int(params.get("rows", 50))
    rows = max(1, min(rows, 50))

    for page in range(max_pages):
        page_params = params.copy()
        page_params["rows"] = rows
        page_params["start"] = page * rows

        response = requests.get(
            MARKETCHECK_ENDPOINT,
            params=page_params,
            timeout=30,
        )

        try:
            payload = response.json()
        except Exception:
            payload = {
                "status_code": response.status_code,
                "text": response.text[:1000],
            }

        last_response = payload

        if response.status_code != 200:
            raise RuntimeError(
                f"MarketCheck API returned HTTP {response.status_code}: {payload}"
            )

        listings = payload.get("listings", [])
        if not listings:
            break

        all_listings.extend(listings)

        # Stop if we received fewer than requested, meaning likely last page.
        if len(listings) < rows:
            break

    return all_listings, last_response


def estimated_api_calls(max_pages: int) -> int:
    """MarketCheck pagination uses one API request per page."""
    return max(1, int(max_pages))


def build_report_markdown(df: pd.DataFrame) -> str:
    if df.empty:
        return "# Car Deal Report\n\nNo listings matched the current filters.\n"

    top = df.head(10).copy()
    lines = [
        "# Car Deal Report",
        "",
        f"Generated from {len(df)} filtered listings.",
        "",
        "## Top Picks",
        "",
    ]

    for idx, (_, row) in enumerate(top.iterrows(), start=1):
        title = clean_text(row.get("title")) or "Untitled listing"
        lines.extend(
            [
                f"### {idx}. {title}",
                f"- Score: {row.get('score', 'N/A')}",
                f"- Deal rating: {clean_text(row.get('deal_rating')) or 'N/A'}",
                f"- Price: {money(row.get('price'))}",
                f"- Fair market value: {money(row.get('market_value'))}",
                f"- Market gap: {money(row.get('market_discount'))}",
                f"- Mileage: {number(row.get('mileage'))}",
                f"- Distance: {number(row.get('distance_miles'))} miles",
                f"- Dealer: {clean_text(row.get('dealer'))}",
                f"- Location: {clean_text(row.get('city'))}, {clean_text(row.get('state'))}",
                f"- VIN: {clean_text(row.get('vin'))}",
                f"- Listing: {clean_text(row.get('url'))}",
                "",
            ]
        )

    lines.extend(
        [
            "## Buyer Notes",
            "",
            "- Verify vehicle history, title status, accident history, service records, and open recalls.",
            "- Ask for out-the-door price before visiting the dealer.",
            "- Get a pre-purchase inspection before committing.",
            "- Treat this report as a prioritization tool, not a guarantee of condition or value.",
            "",
        ]
    )
    return "\n".join(lines)


# ------------------------------------------------------------
# Data extraction / scoring
# ------------------------------------------------------------
def get_nested(data: dict[str, Any], path: list[str], default: Any = None) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def first_non_empty(*values: Any) -> str:
    """Return the first non-empty text value from a list of possible API fields."""
    for value in values:
        text = clean_text(value)
        if text:
            return text
    return ""


def extract_fuel_type(row: dict[str, Any], build: dict[str, Any]) -> str:
    """
    MarketCheck vehicle specs use fuel_type.

    In practice, the value may appear under build.fuel_type, row.fuel_type,
    specs.fuel_type, or inventory.fuel_type depending on endpoint/account.
    Hybrid vehicles are often labeled as Hybrid or Electric / Unleaded.
    """
    return first_non_empty(
        build.get("fuel_type"),
        row.get("fuel_type"),
        get_nested(row, ["specs", "fuel_type"]),
        get_nested(row, ["inventory", "fuel_type"]),
        build.get("powertrain_type"),
        row.get("powertrain_type"),
    )


def fuel_search_text_from_record(record: dict[str, Any]) -> str:
    """Build searchable text for fuel filtering from all likely fuel/powertrain indicators."""
    parts = [
        record.get("fuel_type"),
        record.get("powertrain_type"),
        record.get("engine"),
        record.get("title"),
        record.get("trim"),
        record.get("model"),
        record.get("body_type"),
    ]
    return " ".join(clean_text(x).lower() for x in parts if clean_text(x))


def is_hybrid_record(record: dict[str, Any]) -> bool:
    """
    Identify hybrids using all fields we can get from MarketCheck/dealer feeds.

    MarketCheck supports fuel_type, but hybrid inventory is not always labeled
    with the literal word "Hybrid". Common values include:
      - Hybrid
      - Plug-In Hybrid / PHEV / HEV
      - Electric / Unleaded
      - Electric / Premium Unleaded
      - Gas/Electric
    Toyota listings may also show fuel_type as Unleaded while the trim/title
    contains Hybrid, for example "Highlander Hybrid XLE".
    """
    text = fuel_search_text_from_record(record)

    hybrid_patterns = [
        "hybrid",
        "plug-in",
        "plug in",
        "phev",
        "hev",
        "electric / unleaded",
        "electric/unleaded",
        "electric / premium unleaded",
        "electric/premium unleaded",
        "electric and unleaded",
        "gas/electric",
        "gas electric",
        "gasoline/electric",
        "gasoline electric",
    ]

    return any(pattern in text for pattern in hybrid_patterns)


def filter_by_fuel_type(df: pd.DataFrame, fuel_filter: str) -> pd.DataFrame:
    """Apply local fuel filtering; hybrid matching is intentionally broader."""
    if df.empty or not fuel_filter or fuel_filter.lower() == "any":
        return df

    records = df.to_dict("records")
    selected = fuel_filter.lower()

    if selected == "hybrid":
        mask = [is_hybrid_record(r) for r in records]
    else:
        mask = [selected in fuel_search_text_from_record(r) for r in records]

    return df[pd.Series(mask, index=df.index)]


def extract_listing(row: dict[str, Any]) -> dict[str, Any]:
    build = row.get("build") or {}
    dealer = row.get("dealer") or {}

    price = safe_num(row.get("price"))

    # MarketCheck may return mileage as miles, mileage, or odometer depending on endpoint/account.
    mileage = (
        safe_num(row.get("miles"))
        or safe_num(row.get("mileage"))
        or safe_num(row.get("odometer"))
        or safe_num(get_nested(row, ["inventory", "miles"]))
        or safe_num(get_nested(row, ["inventory", "mileage"]))
    )

    market_value = (
        safe_num(row.get("market_value"))
        or safe_num(row.get("predicted_price"))
        or safe_num(row.get("predicted_market_price"))
    )

    if market_value is None:
        market_value = estimate_fmv_fallback(
            year=safe_num(build.get("year")),
            price=price,
            mileage=mileage,
        )

    market_discount = None
    if market_value is not None and price is not None:
        market_discount = market_value - price

    city = clean_text(dealer.get("city"))
    state = clean_text(dealer.get("state"))

    title = " ".join(
        x for x in [
            clean_text(build.get("year")),
            clean_text(build.get("make")),
            clean_text(build.get("model")),
            clean_text(build.get("trim")),
        ]
        if x
    )

    listing_url = (
        row.get("vdp_url")
        or row.get("listing_url")
        or row.get("url")
        or ""
    )

    item = {
        "title": title,
        "year": safe_num(build.get("year")),
        "make": build.get("make"),
        "model": build.get("model"),
        "trim": build.get("trim"),
        "price": price,
        "market_value": market_value,
        "market_discount": market_discount,
        "market_comparison": market_comparison(market_discount),
        "deal_rating": deal_rating(market_discount),
        "mileage": mileage,
        "distance_miles": safe_num(row.get("dist")),
        "drivetrain": build.get("drivetrain"),
        "body_type": build.get("body_type"),
        "fuel_type": extract_fuel_type(row, build),
        "powertrain_type": first_non_empty(build.get("powertrain_type"), row.get("powertrain_type")),
        "engine": build.get("engine"),
        "transmission": build.get("transmission"),
        "exterior_color": row.get("exterior_color"),
        "interior_color": row.get("interior_color"),
        "dealer": dealer.get("name"),
        "city": city,
        "state": state,
        "vin": row.get("vin"),
        "stock_no": row.get("stock_no"),
        "url": listing_url,
        "dom": safe_num(row.get("dom")),
        "car_type": row.get("car_type"),
    }

    item["score"] = score_vehicle(item)
    return item


def estimate_fmv_fallback(year: float | None, price: float | None, mileage: float | None) -> float | None:
    """Light fallback only when MarketCheck does not return a market value."""
    if price is None:
        return None

    year_adj = 0
    if year:
        current_year = 2026
        age = max(0, current_year - int(year))
        year_adj = max(-12000, 6000 - age * 1200)

    mileage_adj = 0
    if mileage:
        mileage_adj = max(-8000, (45000 - mileage) * 0.06)

    return max(5000, price + year_adj + mileage_adj)


def deal_rating(value: Any) -> str:
    if value is None or pd.isna(value):
        return "Unknown"

    value = float(value)

    if value >= 3000:
        return "Great Deal"
    if value >= 1000:
        return "Good Deal"
    if value >= -1000:
        return "Fair Price"
    return "Overpriced"


def market_comparison(value: Any) -> str:
    if value is None or pd.isna(value):
        return "Market value unavailable"

    value = float(value)

    if value > 0:
        return f"${value:,.0f} below market"
    if value < 0:
        return f"${abs(value):,.0f} above market"
    return "At market value"


def score_vehicle(item: dict[str, Any]) -> float:
    """
    Weighted 0-100 vehicle score.

    This version avoids score inflation where many vehicles hit 100.
    It scores each category separately, then combines them:
      - Value vs market: 40 pts
      - Mileage:         20 pts
      - Distance:        15 pts
      - Year/age:        10 pts
      - Drivetrain:      10 pts
      - Price sanity:     5 pts
    """
    price = safe_num(item.get("price"))
    market_value = safe_num(item.get("market_value"))
    market_discount = safe_num(item.get("market_discount"))
    mileage = safe_num(item.get("mileage"))
    distance = safe_num(item.get("distance_miles"))
    year = safe_num(item.get("year"))
    drivetrain = clean_text(item.get("drivetrain")).lower()

    # 1) Value score: 0-40
    # Based on discount percentage, not raw dollars.
    value_score = 20.0
    if price and market_value and market_value > 0:
        discount_pct = (market_value - price) / market_value

        if discount_pct >= 0.12:
            value_score = 40
        elif discount_pct >= 0.08:
            value_score = 36
        elif discount_pct >= 0.05:
            value_score = 32
        elif discount_pct >= 0.02:
            value_score = 27
        elif discount_pct >= -0.02:
            value_score = 22
        elif discount_pct >= -0.05:
            value_score = 14
        else:
            value_score = 6
    elif market_discount is not None:
        if market_discount >= 5000:
            value_score = 36
        elif market_discount >= 3000:
            value_score = 32
        elif market_discount >= 1000:
            value_score = 26
        elif market_discount >= -1000:
            value_score = 20
        else:
            value_score = 10

    # 2) Mileage score: 0-20
    mileage_score = 10.0
    if mileage is not None:
        if mileage <= 20000:
            mileage_score = 20
        elif mileage <= 40000:
            mileage_score = 17
        elif mileage <= 60000:
            mileage_score = 13
        elif mileage <= 80000:
            mileage_score = 9
        elif mileage <= 100000:
            mileage_score = 5
        else:
            mileage_score = 1

    # 3) Distance score: 0-15
    distance_score = 8.0
    if distance is not None:
        if distance <= 25:
            distance_score = 15
        elif distance <= 75:
            distance_score = 13
        elif distance <= 150:
            distance_score = 10
        elif distance <= 300:
            distance_score = 7
        elif distance <= 500:
            distance_score = 4
        else:
            distance_score = 1

    # 4) Year score: 0-10
    year_score = 5.0
    if year is not None:
        if year >= 2026:
            year_score = 10
        elif year >= 2024:
            year_score = 9
        elif year >= 2022:
            year_score = 7
        elif year >= 2020:
            year_score = 5
        elif year >= 2018:
            year_score = 3
        else:
            year_score = 1

    # 5) Drivetrain score: 0-10
    drivetrain_score = 5.0
    if "awd" in drivetrain or "4wd" in drivetrain:
        drivetrain_score = 10
    elif "fwd" in drivetrain:
        drivetrain_score = 5
    elif "rwd" in drivetrain:
        drivetrain_score = 4

    # 6) Price sanity score: 0-5
    price_score = 3.0
    if price is not None:
        if price <= 30000:
            price_score = 5
        elif price <= 40000:
            price_score = 4
        elif price <= 50000:
            price_score = 3
        elif price <= 60000:
            price_score = 2
        else:
            price_score = 1

    total = (
        value_score
        + mileage_score
        + distance_score
        + year_score
        + drivetrain_score
        + price_score
    )

    return round(max(0, min(100, total)), 1)


def normalize_uploaded_csv(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    rename_map = {
        "listing_url": "url",
        "vdp_url": "url",
        "vehicle_url": "url",
        "dealer_name": "dealer",
        "seller_name": "dealer",
        "dealer_city": "city",
        "dealer_state": "state",
        "distance": "distance_miles",
        "miles": "mileage",
        "odometer": "mileage",
        "asking_price": "price",
        "fair_market_value": "market_value",
        "predicted_price": "market_value",
        "predicted_market_price": "market_value",
        "market_price": "market_value",
        "market_gap": "market_discount",
        "price_gap": "market_discount",
        "discount": "market_discount",
        "fuel": "fuel_type",
        "fueltype": "fuel_type",
        "powertrain": "powertrain_type",
    }

    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    for col in [
        "year",
        "price",
        "market_value",
        "market_discount",
        "mileage",
        "distance_miles",
        "score",
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "market_discount" not in df.columns and "market_value" in df.columns and "price" in df.columns:
        df["market_discount"] = df["market_value"] - df["price"]

    if "market_discount" in df.columns:
        df["deal_rating"] = df["market_discount"].apply(deal_rating)
        df["market_comparison"] = df["market_discount"].apply(market_comparison)

    if "score" not in df.columns:
        records = df.to_dict("records")
        df["score"] = [score_vehicle(r) for r in records]

    if "title" not in df.columns:
        for col in ["make", "model", "trim", "dealer", "city", "state", "vin", "url", "drivetrain", "fuel_type", "powertrain_type"]:
            if col not in df.columns:
                df[col] = ""

        df["title"] = df.apply(
            lambda r: " ".join(
                str(x)
                for x in [
                    "" if pd.isna(r.get("year")) else int(r.get("year")),
                    r.get("make", ""),
                    r.get("model", ""),
                    r.get("trim", ""),
                ]
                if str(x).strip()
            ),
            axis=1,
        )

    return df


# ------------------------------------------------------------
# Dashboard display
# ------------------------------------------------------------
def apply_filters(df: pd.DataFrame) -> pd.DataFrame:
    filtered = df.copy()

    with st.sidebar:
        st.header("Filter Results")

        if "price" in filtered.columns and filtered["price"].notna().any():
            max_price_default = int(filtered["price"].max())
            max_price = st.number_input("Max price", min_value=0, value=max_price_default, step=1000)
            filtered = filtered[filtered["price"].fillna(10**12) <= max_price]

        if "mileage" in filtered.columns:
            filtered["mileage"] = pd.to_numeric(filtered["mileage"], errors="coerce")

            if filtered["mileage"].notna().any():
                max_mileage_default = int(filtered["mileage"].dropna().max())
                max_mileage = st.number_input(
                    "Max mileage",
                    min_value=0,
                    value=max_mileage_default,
                    step=5000,
                    help="Filters the mileage column after converting it to a number.",
                )
                filtered = filtered[filtered["mileage"].fillna(10**12) <= int(max_mileage)]

        if "distance_miles" in filtered.columns and filtered["distance_miles"].notna().any():
            max_distance_default = int(filtered["distance_miles"].max())
            max_distance = st.number_input("Max distance miles", min_value=0, value=max_distance_default, step=25)
            filtered = filtered[filtered["distance_miles"].fillna(10**12) <= max_distance]

        if "year" in filtered.columns and filtered["year"].notna().any():
            min_year_default = int(filtered["year"].min())
            min_year = st.number_input("Minimum year", min_value=1980, value=min_year_default, step=1)
            filtered = filtered[filtered["year"].fillna(0) >= min_year]

        for col, label in [
            ("make", "Make"),
            ("model", "Model"),
            ("trim", "Trim"),
            ("drivetrain", "Drivetrain"),
            ("fuel_type", "Exact fuel type from MarketCheck"),
            ("deal_rating", "Deal rating"),
            ("state", "State"),
            ("car_type", "Car type"),
        ]:
            if col in filtered.columns:
                values = sorted([x for x in filtered[col].dropna().astype(str).unique() if x.strip()])
                if values:
                    selected = st.multiselect(label, values, default=values)
                    if selected:
                        filtered = filtered[filtered[col].astype(str).isin(selected)]

        if "fuel_type" in filtered.columns:
            hybrid_only = st.checkbox(
                "Hybrid only",
                value=False,
                help="Matches MarketCheck fuel_type values like Hybrid or Electric / Unleaded, plus trim/title text containing hybrid.",
            )
            if hybrid_only:
                filtered = filter_by_fuel_type(filtered, "Hybrid")

        search_text = st.text_input("Search title/dealer/city/VIN").strip().lower()
        if search_text:
            cols = ["title", "dealer", "city", "state", "vin"]
            mask = pd.Series(False, index=filtered.index)
            for col in cols:
                if col in filtered.columns:
                    mask = mask | filtered[col].astype(str).str.lower().str.contains(search_text, na=False)
            filtered = filtered[mask]

        sort_options = [
            c for c in [
                "score",
                "market_discount",
                "price",
                "mileage",
                "distance_miles",
                "year",
            ]
            if c in filtered.columns
        ]

        if sort_options:
            sort_col = st.selectbox("Sort by", sort_options)
            ascending_default = sort_col in ["price", "mileage", "distance_miles"]
            ascending = st.checkbox("Sort low to high", value=ascending_default)
            filtered = filtered.sort_values(sort_col, ascending=ascending, na_position="last")

    return filtered


def show_results(df: pd.DataFrame) -> None:
    filtered = apply_filters(df)

    st.header("Summary")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Listings", len(filtered))
    c2.metric("Best Score", f"{filtered['score'].max():.1f}" if "score" in filtered.columns and filtered["score"].notna().any() and len(filtered) else "N/A")
    c3.metric("Lowest Price", money(filtered["price"].min()) if "price" in filtered.columns and len(filtered) else "N/A")
    c4.metric("Avg Mileage", number(filtered["mileage"].mean()) if "mileage" in filtered.columns and len(filtered) else "N/A")
    c5.metric("Best Market Gap", money(filtered["market_discount"].max()) if "market_discount" in filtered.columns and len(filtered) else "N/A")

    st.header("Top Matches")

    preferred_cols = [
        "score",
        "deal_rating",
        "title",
        "year",
        "make",
        "model",
        "trim",
        "price",
        "market_value",
        "market_discount",
        "market_comparison",
        "mileage",
        "distance_miles",
        "fuel_type",
        "powertrain_type",
        "drivetrain",
        "dealer",
        "city",
        "state",
        "vin",
        "url",
    ]

    show_cols = [c for c in preferred_cols if c in filtered.columns]
    display_df = filtered[show_cols].copy()

    display_df = display_df.rename(
        columns={
            "score": "Score",
            "deal_rating": "Deal Rating",
            "title": "Vehicle",
            "year": "Year",
            "make": "Make",
            "model": "Model",
            "trim": "Trim",
            "price": "Price",
            "market_value": "Fair Market Value",
            "market_discount": "Market Gap",
            "market_comparison": "Market Comparison",
            "mileage": "Mileage",
            "distance_miles": "Distance",
            "fuel_type": "Fuel Type",
            "powertrain_type": "Powertrain Type",
            "drivetrain": "Drivetrain",
            "dealer": "Dealer",
            "city": "City",
            "state": "State",
            "vin": "VIN",
            "url": "Listing",
        }
    )

    for col in ["Price", "Fair Market Value", "Market Gap"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(money)

    for col in ["Mileage", "Distance"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(number)

    if "Year" in display_df.columns:
        display_df["Year"] = display_df["Year"].apply(lambda x: "" if pd.isna(x) else int(x))

    st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Listing": st.column_config.LinkColumn("Listing"),
        },
    )

    if not filtered.empty:
        st.header("Best Listing Details")
        best = filtered.iloc[0]
        left, right = st.columns([2, 1])

        with left:
            st.subheader(clean_text(best.get("title")))
            st.write(f"**Dealer:** {best.get('dealer', '')}")
            st.write(f"**Location:** {best.get('city', '')}, {best.get('state', '')}")
            st.write(f"**Fuel Type:** {best.get('fuel_type', '')}")
            st.write(f"**Powertrain Type:** {best.get('powertrain_type', '')}")
            st.write(f"**VIN:** `{best.get('vin', '')}`")
            if str(best.get("url", "")).startswith("http"):
                st.link_button("Open Listing", best.get("url"))

        with right:
            st.metric("Score", f"{best.get('score', '')}")
            st.metric("Price", money(best.get("price")))
            st.metric("Fair Market Value", money(best.get("market_value")))
            st.metric("Market Gap", money(best.get("market_discount")))

    st.header("Download Results")
    csv_data = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download filtered CSV",
        data=csv_data,
        file_name="jens_car_filtered_results.csv",
        mime="text/csv",
    )

    report_data = build_report_markdown(filtered).encode("utf-8")
    st.download_button(
        "Download buyer report",
        data=report_data,
        file_name="car_deal_report.md",
        mime="text/markdown",
    )


def show_scoring_methodology() -> None:
    """Display scoring criteria so users understand how the vehicle score is calculated."""
    with st.expander("How the vehicle score is calculated", expanded=True):
        st.markdown(
            """
            The vehicle score is a **0-100 ranking** designed to highlight the best overall value.
            It is not a guarantee of vehicle condition, and users should still inspect the listing,
            vehicle history, title status, and dealer details.
            """
        )

        score_df = pd.DataFrame(
            [
                {
                    "Factor": "Value vs Market",
                    "Weight": "40%",
                    "What helps the score": "Bigger discount compared to Fair Market Value",
                },
                {
                    "Factor": "Mileage",
                    "Weight": "20%",
                    "What helps the score": "Lower mileage",
                },
                {
                    "Factor": "Distance",
                    "Weight": "15%",
                    "What helps the score": "Closer vehicles",
                },
                {
                    "Factor": "Vehicle Year",
                    "Weight": "10%",
                    "What helps the score": "Newer model years",
                },
                {
                    "Factor": "Drivetrain",
                    "Weight": "10%",
                    "What helps the score": "AWD / 4WD",
                },
                {
                    "Factor": "Price",
                    "Weight": "5%",
                    "What helps the score": "Lower purchase price",
                },
            ]
        )

        st.dataframe(
            score_df,
            width="stretch",
            hide_index=True,
        )

        st.markdown(
            """
            **Quick guide:**  
            **90+** = exceptional candidate | **80-89** = strong | **70-79** = worth reviewing |
            **60-69** = average | **below 60** = usually lower priority.
            """
        )


def main() -> None:
    st.title("Car Deal Report Dashboard")
    st.caption("Search once, rank candidates, and export a buyer-ready shortlist without burning unnecessary API calls.")

    show_scoring_methodology()

    api_key = get_api_key()

    with st.sidebar:
        st.header("Search MarketCheck")

        make = st.text_input("Make", value="Infiniti")
        model = st.text_input("Model", value="QX60")
        zip_code = st.text_input("ZIP code", value="60004")
        radius = st.number_input(
            "Radius miles",
            min_value=10,
            max_value=100,
            value=100,
            step=10,
            help="MarketCheck API maximum search radius is 100 miles.",
        )
        min_year = st.number_input("Minimum year", min_value=1980, max_value=2030, value=2022, step=1)
        max_price = st.number_input("Maximum price", min_value=0, value=45000, step=1000)
        max_miles = st.number_input("Maximum miles", min_value=0, value=70000, step=5000)
        rows_per_page = st.number_input(
            "Rows per page",
            min_value=10,
            max_value=50,
            value=DEFAULT_ROWS_PER_PAGE,
            step=10,
            help="MarketCheck commonly caps this at 50 rows per request.",
        )
        max_pages = st.number_input(
            "Max pages",
            min_value=1,
            max_value=MAX_ALLOWED_PAGES,
            value=DEFAULT_MAX_PAGES,
            step=1,
            help="Each page is one MarketCheck API call. Keep this low until you know the search is useful.",
        )
        car_type = st.selectbox("Car type", ["used", "certified", "new", "any"], index=0)
        fuel_filter = st.selectbox(
            "Fuel type",
            ["Any", "Hybrid", "Gasoline", "Diesel", "Electric", "Premium Unleaded", "Electric / Unleaded"],
            index=0,
            help="Uses MarketCheck fuel_type. Hybrid also matches Electric / Unleaded and trim/title text containing hybrid.",
        )

        require_awd = st.checkbox("AWD / 4WD only", value=False)

        call_estimate = estimated_api_calls(int(max_pages))
        st.info(
            f"This search will use up to {call_estimate} MarketCheck API call"
            f"{'' if call_estimate == 1 else 's'}. Re-running the same search is cached for 1 hour."
        )

        search_clicked = st.button("Build report", type="primary")

        st.divider()
        uploaded = st.file_uploader("Or upload an existing CSV", type=["csv"])

    if not api_key:
        st.warning(
            "MarketCheck API key is not configured. Add MARKETCHECK_API_KEY in Streamlit secrets "
            "or as a local environment variable. You can still upload a CSV."
        )

    if "results_df" not in st.session_state:
        st.session_state["results_df"] = pd.DataFrame()

    if uploaded is not None:
        uploaded_df = pd.read_csv(uploaded)
        st.session_state["results_df"] = normalize_uploaded_csv(uploaded_df)
        st.success(f"Loaded uploaded CSV with {len(st.session_state['results_df'])} rows.")

    if search_clicked:
        if not api_key:
            st.error("Missing MarketCheck API key.")
        elif not make.strip() or not model.strip() or not zip_code.strip():
            st.error("Make, model, and ZIP code are required.")
        else:
            params = build_marketcheck_params(
                api_key=api_key,
                make=make.strip(),
                model=model.strip(),
                zip_code=zip_code.strip(),
                radius=int(radius),
                min_year=int(min_year),
                max_price=int(max_price),
                max_miles=int(max_miles),
                rows=int(rows_per_page),
                car_type=car_type,
                fuel_filter=fuel_filter,
            )

            with st.spinner("Searching MarketCheck..."):
                try:
                    listings, raw_response = marketcheck_search(
                        params=params,
                        max_pages=int(max_pages),
                    )

                    records = [extract_listing(x) for x in listings]
                    df = pd.DataFrame(records)

                    # Enforce filters locally too.
                    # This fixes cases where the API returns rows above max mileage/price
                    # or where MarketCheck uses a slightly different parameter name.
                    if not df.empty:
                        for col in ["price", "mileage", "year", "distance_miles"]:
                            if col in df.columns:
                                df[col] = pd.to_numeric(df[col], errors="coerce")

                        if "price" in df.columns:
                            df = df[df["price"].fillna(10**12) <= int(max_price)]

                        if "mileage" in df.columns:
                            df = df[df["mileage"].fillna(10**12) <= int(max_miles)]

                        if "year" in df.columns:
                            df = df[df["year"].fillna(0) >= int(min_year)]

                        if "distance_miles" in df.columns:
                            df = df[df["distance_miles"].fillna(10**12) <= int(radius)]

                        before_fuel_filter_count = len(df)
                        df = filter_by_fuel_type(df, fuel_filter)

                        if fuel_filter.lower() == "hybrid" and before_fuel_filter_count > 0 and df.empty:
                            st.warning(
                                "Hybrid selected, but no hybrid matches were found in the returned listings. "
                                "This can happen if the model does not have a hybrid version in the selected years "
                                "or if dealer feeds do not label the vehicle as hybrid. Try Toyota Highlander, "
                                "Toyota RAV4, Lexus RX, or broaden make/model."
                            )

                    if require_awd and not df.empty and "drivetrain" in df.columns:
                        mask = (
                            df["drivetrain"]
                            .fillna("")
                            .astype(str)
                            .str.lower()
                            .str.contains("awd|4wd|all wheel|four wheel", regex=True)
                        )
                        df = df[mask]

                    st.session_state["results_df"] = df

                    if df.empty:
                        st.warning("No listings returned. Try widening your filters.")
                        with st.expander("Raw MarketCheck response"):
                            st.json(raw_response)
                    else:
                        st.success(f"Found {len(df)} listings.")

                except Exception as e:
                    st.error(f"Search failed: {e}")

    df = st.session_state["results_df"]

    if df.empty:
        st.info("Enter search criteria in the sidebar and click **Search cars**, or upload a CSV.")
        return

    with st.expander("Debug: data preview", expanded=False):
        st.write("Rows:", len(df))
        st.write("Columns:", list(df.columns))
        if "fuel_type" in df.columns:
            st.write("Fuel types found:", sorted([str(x) for x in df["fuel_type"].dropna().unique() if str(x).strip()]))
        if "powertrain_type" in df.columns:
            st.write("Powertrain types found:", sorted([str(x) for x in df["powertrain_type"].dropna().unique() if str(x).strip()]))
        st.dataframe(df.head(5), width="stretch")

    show_results(df)


if __name__ == "__main__":
    main()
