# app.py
# Car Deal Report Dashboard
# Release baseline: Auto.dev search, ranked results, CSV upload, and report export.
#
# Run locally:
#   streamlit run app.py
#
# Streamlit Cloud secrets:
#   AUTODEV_API_KEY = "your_key_here"
#
# Local .env option:
#   AUTODEV_API_KEY=your_key_here

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, urlparse

import pandas as pd
import requests
import streamlit as st


st.set_page_config(
    page_title="Car Deal Report Dashboard",
    page_icon="car",
    layout="wide",
)


BASE_DIR = Path(__file__).resolve().parent
AUTODEV_ENDPOINT = "https://api.auto.dev/listings"
DEFAULT_ROWS_PER_PAGE = 50
DEFAULT_MAX_PAGES = 2
MAX_ALLOWED_PAGES = 5


# ------------------------------------------------------------
# API / config helpers
# ------------------------------------------------------------
def get_api_key() -> str:
    for name in ("AUTODEV_API_KEY", "AUTO_DEV_API_KEY", "AUTODEV_TOKEN", "AUTO_DEV_TOKEN"):
        try:
            key = st.secrets.get(name, "")
            if key:
                return str(key)
        except Exception:
            pass

        key = os.getenv(name, "")
        if key:
            return str(key)

    return ""


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
# Auto.dev API
# ------------------------------------------------------------
def build_autodev_params(
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
    safe_radius = max(10, min(int(radius), 100))
    safe_rows = max(1, min(int(rows), 100))

    params: dict[str, Any] = {
        "page": 1,
        "limit": safe_rows,
        "sort": "updatedAt.desc",
        "vehicle.make": make,
        "vehicle.model": model,
        "vehicle.year": f"{int(min_year)}-2030",
        "retailListing.price": f"1-{int(max_price)}",
        "retailListing.miles": f"0-{int(max_miles)}",
        "zip": zip_code,
        "distance": safe_radius,
        "includeUnpriced": "false",
        "includes": "total",
    }

    selected_type = car_type.lower() if car_type else "used"
    if selected_type == "new":
        params["retailListing.used"] = "false"
    elif selected_type in {"used", "certified"}:
        params["retailListing.used"] = "true"
        if selected_type == "certified":
            params["retailListing.cpo"] = "true"

    return params


def extract_autodev_listings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Auto.dev returns listings in data, but keep fallbacks for compatible payloads."""
    candidates = [
        payload.get("data"),
        payload.get("records"),
        payload.get("results"),
        payload.get("listings"),
    ]

    data = payload.get("data")
    if isinstance(data, dict):
        candidates.extend([
            data.get("records"),
            data.get("results"),
            data.get("listings"),
            data.get("items"),
        ])

    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]

    return []


def autodev_total(payload: dict[str, Any]) -> Any:
    data = payload.get("data")
    if isinstance(data, dict):
        return first_non_empty(data.get("total"), data.get("count"), payload.get("total"), payload.get("count"))
    return first_non_empty(payload.get("total"), payload.get("count"))


@st.cache_data(ttl=60 * 60, show_spinner=False)
def autodev_search(
    params: dict[str, Any],
    _api_key: str,
    max_pages: int = DEFAULT_MAX_PAGES,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_listings: list[dict[str, Any]] = []
    last_response: dict[str, Any] = {}

    limit = int(params.get("limit", DEFAULT_ROWS_PER_PAGE))
    limit = max(1, min(limit, 100))
    headers = {
        "Authorization": f"Bearer {_api_key}",
        "Content-Type": "application/json",
    }

    for page in range(max_pages):
        page_params = params.copy()
        page_params["limit"] = limit
        page_params["page"] = page + 1

        response = requests.get(
            AUTODEV_ENDPOINT,
            params=page_params,
            headers=headers,
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
        last_response["_request_params"] = {k: v for k, v in page_params.items() if k != "api_key"}
        last_response["_page"] = page + 1

        if response.status_code != 200:
            raise RuntimeError(
                f"Auto.dev API returned HTTP {response.status_code}: {payload}"
            )

        listings = extract_autodev_listings(payload)
        if not listings:
            break

        all_listings.extend(listings)

        # Stop if we received fewer than requested, meaning likely last page.
        if len(listings) < limit:
            break

    return all_listings, last_response


def estimated_api_calls(max_pages: int) -> int:
    """Auto.dev pagination uses one API request per page."""
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
                f"- Distance: {distance_label(row.get('distance_miles'), include_units=True)}",
                f"- Dealer: {clean_text(row.get('dealer'))}",
                f"- Location: {clean_text(row.get('city'))}, {clean_text(row.get('state'))}",
                f"- VIN: {clean_text(row.get('vin'))}",
                f"- Dealer/search link: {clean_text(row.get('url'))}",
                f"- Carfax: {clean_text(row.get('carfax_url'))}",
                f"- Auto.dev source: {clean_text(row.get('source_url'))}",
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


def distance_label(value: Any, include_units: bool = False) -> str:
    """Show a useful label when Auto.dev radius-filtered rows omit per-listing distance."""
    numeric_value = safe_num(value)
    if numeric_value is None:
        return "Within search radius"

    text = number(numeric_value)
    if not text:
        return "Within search radius"
    return f"{text} miles" if include_units else text


def carfax_url_from_vin(vin: Any) -> str:
    vin_text = clean_text(vin).upper()
    if not vin_text:
        return ""
    return f"https://www.carfax.com/VehicleHistory/p/Report.cfx?partner=DVW_1&vin={vin_text}"


def is_provider_listing_url(value: Any) -> bool:
    url = clean_text(value).lower()
    if not url.startswith(("http://", "https://")):
        return False

    host = urlparse(url).netloc.lower()
    provider_hosts = [
        "details.vast.com",
        "vast.com",
        "api.auto.dev",
        "auto.dev",
    ]
    return any(host == provider or host.endswith(f".{provider}") for provider in provider_hosts)


def is_usable_listing_url(value: Any) -> bool:
    url = clean_text(value)
    if not url.startswith(("http://", "https://")):
        return False
    return not is_provider_listing_url(url)


def search_url_for_listing(*values: Any) -> str:
    query = " ".join(clean_text(value) for value in values if clean_text(value))
    if not query:
        return ""
    return f"https://www.google.com/search?q={quote_plus(query)}"


def infer_fuel_type_from_text(*values: Any) -> str:
    text = " ".join(clean_text(value).lower() for value in values if clean_text(value))
    if not text:
        return ""

    if any(term in text for term in ["plug-in hybrid", "plug in hybrid", "phev"]):
        return "Plug-In Hybrid"
    if any(term in text for term in ["hybrid", "gas/electric", "gas electric", "gasoline/electric", "gasoline electric", "hev"]):
        return "Hybrid"
    if "diesel" in text:
        return "Diesel"
    if "electric" in text and not any(term in text for term in ["electric / unleaded", "electric/unleaded"]):
        return "Electric"
    if any(term in text for term in ["premium unleaded", "unleaded", "gasoline", "gas "]):
        return "Gasoline"
    return ""


def extract_fuel_type(row: dict[str, Any], build: dict[str, Any]) -> str:
    """
    Auto.dev vehicle specs may include fuel_type or similar fuel fields.

    In practice, the value may appear under build.fuel_type, row.fuel_type,
    specs.fuel_type, or inventory.fuel_type depending on endpoint/account.
    Hybrid vehicles are often labeled as Hybrid or Electric / Unleaded.
    """
    return first_non_empty(
        build.get("fuel_type"),
        build.get("fuelType"),
        build.get("fuel"),
        build.get("fuelCategory"),
        build.get("fuelCategoryName"),
        row.get("fuel_type"),
        row.get("fuelType"),
        row.get("fuel"),
        row.get("fuelCategory"),
        row.get("fuelCategoryName"),
        get_nested(row, ["specs", "fuel_type"]),
        get_nested(row, ["specs", "fuelType"]),
        get_nested(row, ["specs", "fuel"]),
        get_nested(row, ["inventory", "fuel_type"]),
        get_nested(row, ["inventory", "fuelType"]),
        get_nested(row, ["inventory", "fuel"]),
        get_nested(row, ["retailListing", "fuel_type"]),
        get_nested(row, ["retailListing", "fuelType"]),
        get_nested(row, ["retailListing", "fuel"]),
        build.get("powertrain_type"),
        build.get("powertrainType"),
        row.get("powertrain_type"),
        row.get("powertrainType"),
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
    Identify hybrids using all fields we can get from Auto.dev/dealer feeds.

    Auto.dev inventory is not always labeled
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
    vehicle = row.get("vehicle") if isinstance(row.get("vehicle"), dict) else {}
    retail = row.get("retailListing") if isinstance(row.get("retailListing"), dict) else {}
    history = row.get("history") if isinstance(row.get("history"), dict) else {}
    dealer = row.get("dealer") if isinstance(row.get("dealer"), dict) else {}
    location = row.get("location") if isinstance(row.get("location"), dict) else {}
    retail_location = retail.get("location") if isinstance(retail.get("location"), dict) else {}

    price = safe_num(first_non_empty(retail.get("price"), row.get("price")))

    # Auto.dev normally returns mileage under retailListing.miles, but keep fallbacks for CSV/imported rows.
    mileage = (
        safe_num(retail.get("miles"))
        or safe_num(row.get("miles"))
        or safe_num(row.get("mileage"))
        or safe_num(row.get("odometer"))
    )

    market_value = (
        safe_num(row.get("marketValue"))
        or safe_num(row.get("market_value"))
        or safe_num(retail.get("marketValue"))
        or safe_num(get_nested(row, ["pricing", "marketValue"]))
        or safe_num(get_nested(row, ["valuation", "marketValue"]))
    )

    if market_value is None:
        market_value = estimate_fmv_fallback(
            year=safe_num(first_non_empty(vehicle.get("year"), row.get("year"))),
            price=price,
            mileage=mileage,
        )

    market_discount = None
    if market_value is not None and price is not None:
        market_discount = market_value - price

    city = clean_text(first_non_empty(retail.get("city"), row.get("city")))
    state = clean_text(first_non_empty(retail.get("state"), row.get("state")))

    title = " ".join(
        x for x in [
            clean_text(first_non_empty(vehicle.get("year"), row.get("year"))),
            clean_text(first_non_empty(vehicle.get("make"), row.get("make"))),
            clean_text(first_non_empty(vehicle.get("model"), row.get("model"))),
            clean_text(first_non_empty(vehicle.get("trim"), row.get("trim"))),
        ]
        if x
    )

    raw_listing_url = (
        retail.get("vdp")
        or retail.get("url")
        or row.get("vdp")
        or row.get("url")
        or row.get("@id")
        or ""
    )

    distance = safe_num(first_non_empty(
        retail.get("distance"),
        retail.get("distanceMiles"),
        retail.get("distance_miles"),
        retail.get("distanceToDealer"),
        retail.get("dealerDistance"),
        retail_location.get("distance"),
        retail_location.get("distanceMiles"),
        row.get("distance"),
        row.get("distanceMiles"),
        row.get("distance_miles"),
        row.get("distanceToDealer"),
        row.get("dealerDistance"),
        dealer.get("distance"),
        dealer.get("distanceMiles"),
        location.get("distance"),
        location.get("distanceMiles"),
        row.get("dist"),
    ))

    engine = first_non_empty(vehicle.get("engine"), vehicle.get("engineDescription"), row.get("engine"))
    powertrain_type = first_non_empty(
        vehicle.get("powertrainType"),
        vehicle.get("powertrain_type"),
        vehicle.get("powertrain"),
        vehicle.get("engineType"),
        row.get("powertrainType"),
        row.get("powertrain_type"),
        row.get("powertrain"),
        row.get("engineType"),
    )
    fuel_type = extract_fuel_type(row, vehicle) or infer_fuel_type_from_text(
        engine,
        powertrain_type,
        vehicle.get("trim"),
        row.get("trim"),
        row.get("title"),
    )
    vin = first_non_empty(row.get("vin"), vehicle.get("vin"))
    carfax_url = first_non_empty(
        history.get("carfaxUrl"),
        history.get("carfax_url"),
        row.get("carfaxUrl"),
        row.get("carfax_url"),
        retail.get("carfaxUrl"),
        retail.get("carfax_url"),
        carfax_url_from_vin(vin),
    )
    dealer_name = first_non_empty(retail.get("dealer"), row.get("dealer"))
    buyer_search_url = search_url_for_listing(vin, title, dealer_name, city, state)
    listing_url = clean_text(raw_listing_url) if is_usable_listing_url(raw_listing_url) else ""
    if not listing_url:
        listing_url = buyer_search_url

    item = {
        "title": title,
        "year": safe_num(first_non_empty(vehicle.get("year"), row.get("year"))),
        "make": first_non_empty(vehicle.get("make"), row.get("make")),
        "model": first_non_empty(vehicle.get("model"), row.get("model")),
        "trim": first_non_empty(vehicle.get("trim"), row.get("trim")),
        "price": price,
        "market_value": market_value,
        "market_discount": market_discount,
        "market_comparison": market_comparison(market_discount),
        "deal_rating": deal_rating(market_discount),
        "mileage": mileage,
        "distance_miles": distance,
        "distance_label": distance_label(distance),
        "drivetrain": first_non_empty(vehicle.get("drivetrain"), vehicle.get("driveTrain"), row.get("drivetrain")),
        "body_type": first_non_empty(vehicle.get("bodyType"), vehicle.get("body_type"), row.get("body_type")),
        "fuel_type": fuel_type,
        "powertrain_type": powertrain_type,
        "engine": engine,
        "transmission": first_non_empty(vehicle.get("transmission"), row.get("transmission")),
        "exterior_color": first_non_empty(retail.get("exteriorColor"), row.get("exterior_color")),
        "interior_color": first_non_empty(retail.get("interiorColor"), row.get("interior_color")),
        "dealer": dealer_name,
        "city": city,
        "state": state,
        "vin": vin,
        "stock_no": first_non_empty(retail.get("stockNumber"), row.get("stock_no")),
        "url": listing_url,
        "source_url": clean_text(raw_listing_url),
        "carfax_url": carfax_url,
        "dom": safe_num(first_non_empty(retail.get("daysOnMarket"), row.get("dom"))),
        "car_type": row.get("car_type"),
        "one_owner": first_non_empty(row.get("oneOwner"), history.get("oneOwner"), history.get("ownerCount") == 1),
        "clean_title": first_non_empty(row.get("cleanTitle"), history.get("cleanTitle"), history.get("titleStatus")),
    }

    item["score"] = score_vehicle(item)
    return item


def estimate_fmv_fallback(year: float | None, price: float | None, mileage: float | None) -> float | None:
    """Light fallback only when Auto.dev does not return a market value."""
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
      - AWD / 4WD match: 10 pts
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

    # 5) AWD / 4WD score: 0-10
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
        "source_url": "source_url",
        "dealer_name": "dealer",
        "seller_name": "dealer",
        "dealer_city": "city",
        "dealer_state": "state",
        "distance": "distance_miles",
        "distance_mi": "distance_miles",
        "distance_miles": "distance_miles",
        "distance_to_dealer": "distance_miles",
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
        "fuel_type": "fuel_type",
        "powertrain": "powertrain_type",
        "powertrain_type": "powertrain_type",
        "carfax": "carfax_url",
        "carfax_report": "carfax_url",
        "carfax_link": "carfax_url",
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
        for col in ["make", "model", "trim", "dealer", "city", "state", "vin", "url", "carfax_url", "drivetrain", "fuel_type", "powertrain_type"]:
            if col not in df.columns:
                df[col] = ""

    if "carfax_url" not in df.columns and "vin" in df.columns:
        df["carfax_url"] = df["vin"].apply(carfax_url_from_vin)

    if "title" not in df.columns:
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

    if "url" in df.columns:
        if "source_url" not in df.columns:
            df["source_url"] = df["url"]

        def usable_uploaded_url(row: pd.Series) -> str:
            existing_url = clean_text(row.get("url"))
            if is_usable_listing_url(existing_url):
                return existing_url
            return search_url_for_listing(
                row.get("vin"),
                row.get("title"),
                row.get("dealer"),
                row.get("city"),
                row.get("state"),
            )

        df["url"] = df.apply(usable_uploaded_url, axis=1)

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
            filtered = filtered[filtered["distance_miles"].isna() | (filtered["distance_miles"] <= max_distance)]

        if "year" in filtered.columns and filtered["year"].notna().any():
            min_year_default = int(filtered["year"].min())
            min_year = st.number_input("Minimum year", min_value=1980, value=min_year_default, step=1)
            filtered = filtered[filtered["year"].fillna(0) >= min_year]

        for col, label in [
            ("make", "Make"),
            ("model", "Model"),
            ("trim", "Trim"),
            ("fuel_type", "Exact fuel type from Auto.dev"),
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
                help="Matches Auto.dev fuel values like Hybrid or Electric / Unleaded, plus trim/title text containing hybrid.",
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
        "dealer",
        "city",
        "state",
        "vin",
        "url",
        "carfax_url",
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
            "dealer": "Dealer",
            "city": "City",
            "state": "State",
            "vin": "VIN",
            "url": "Dealer/Search Link",
            "carfax_url": "Carfax",
        }
    )

    for col in ["Price", "Fair Market Value", "Market Gap"]:
        if col in display_df.columns:
            display_df[col] = display_df[col].apply(money)

    if "Mileage" in display_df.columns:
        display_df["Mileage"] = display_df["Mileage"].apply(number)

    if "Distance" in display_df.columns:
        display_df["Distance"] = display_df["Distance"].apply(distance_label)

    if "Year" in display_df.columns:
        display_df["Year"] = display_df["Year"].apply(lambda x: "" if pd.isna(x) else int(x))

    st.dataframe(
        display_df,
        width="stretch",
        hide_index=True,
        column_config={
            "Dealer/Search Link": st.column_config.LinkColumn("Dealer/Search Link"),
            "Carfax": st.column_config.LinkColumn("Carfax"),
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
            st.write(f"**Distance:** {distance_label(best.get('distance_miles'), include_units=True)}")
            st.write(f"**VIN:** `{best.get('vin', '')}`")
            if str(best.get("url", "")).startswith("http"):
                st.link_button("Open Dealer/Search Link", best.get("url"))
            if str(best.get("carfax_url", "")).startswith("http"):
                st.link_button("Open Carfax", best.get("carfax_url"))

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
                    "Factor": "AWD / 4WD Match",
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
        st.header("Search Auto.dev")

        make = st.text_input("Make", value="Toyota")
        model = st.text_input("Model", value="RAV4")
        zip_code = st.text_input("ZIP code", value="60004")
        radius = st.number_input(
            "Radius miles",
            min_value=10,
            max_value=100,
            value=100,
            step=10,
            help="Auto.dev search radius is capped at 100 miles in this app.",
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
            help="Auto.dev listing pages are requested in batches; keep this modest on free tiers.",
        )
        max_pages = st.number_input(
            "Max pages",
            min_value=1,
            max_value=MAX_ALLOWED_PAGES,
            value=DEFAULT_MAX_PAGES,
            step=1,
            help="Each page is one Auto.dev API call. Keep this low until you know the search is useful.",
        )
        car_type = st.selectbox("Car type", ["used", "certified", "new", "any"], index=0)
        fuel_filter = st.selectbox(
            "Fuel type",
            ["Any", "Hybrid", "Gasoline", "Diesel", "Electric", "Premium Unleaded", "Electric / Unleaded"],
            index=0,
            help="Uses Auto.dev fuel fields where available. Hybrid also matches Electric / Unleaded and trim/title text containing hybrid.",
        )

        require_awd = st.checkbox("AWD / 4WD only", value=False)

        call_estimate = estimated_api_calls(int(max_pages))
        st.info(
            f"This search will use up to {call_estimate} Auto.dev API call"
            f"{'' if call_estimate == 1 else 's'}. Re-running the same search is cached for 1 hour."
        )

        search_clicked = st.button("Build report", type="primary")

        st.divider()
        uploaded = st.file_uploader("Or upload an existing CSV", type=["csv"])

    if not api_key:
        st.warning(
            "Auto.dev API key is not configured. Add AUTODEV_API_KEY in Streamlit secrets "
            "or as a local environment variable. AUTO_DEV_API_KEY, AUTODEV_TOKEN, and AUTO_DEV_TOKEN also work. "
            "You can still upload a CSV."
        )

    if "results_df" not in st.session_state:
        st.session_state["results_df"] = pd.DataFrame()

    if uploaded is not None:
        uploaded_df = pd.read_csv(uploaded)
        st.session_state["results_df"] = normalize_uploaded_csv(uploaded_df)
        st.success(f"Loaded uploaded CSV with {len(st.session_state['results_df'])} rows.")

    if search_clicked:
        if not api_key:
            st.error("Missing Auto.dev API key.")
        elif not make.strip() or not model.strip() or not zip_code.strip():
            st.error("Make, model, and ZIP code are required.")
        else:
            params = build_autodev_params(
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

            with st.spinner("Searching Auto.dev..."):
                try:
                    listings, raw_response = autodev_search(
                        params=params,
                        _api_key=api_key,
                        max_pages=int(max_pages),
                    )

                    raw_listing_count = len(listings)
                    filter_counts: list[tuple[str, int]] = [("Auto.dev raw listings", raw_listing_count)]
                    records = [extract_listing(x) for x in listings]
                    df = pd.DataFrame(records)
                    filter_counts.append(("Normalized rows", len(df)))

                    # Enforce filters locally too.
                    # This fixes cases where the API returns rows above max mileage/price
                    # or where Auto.dev/account settings return rows outside requested filters.
                    if not df.empty:
                        for col in ["price", "mileage", "year", "distance_miles"]:
                            if col in df.columns:
                                df[col] = pd.to_numeric(df[col], errors="coerce")

                        if "price" in df.columns:
                            df = df[df["price"].fillna(10**12) <= int(max_price)]
                            filter_counts.append(("After max price", len(df)))

                        if "mileage" in df.columns:
                            df = df[df["mileage"].fillna(10**12) <= int(max_miles)]
                            filter_counts.append(("After max miles", len(df)))

                        if "year" in df.columns:
                            df = df[df["year"].fillna(0) >= int(min_year)]
                            filter_counts.append(("After min year", len(df)))

                        if "distance_miles" in df.columns:
                            known_distance = df["distance_miles"].notna().sum()
                            if known_distance:
                                df = df[df["distance_miles"].isna() | (df["distance_miles"] <= int(radius))]
                                filter_counts.append(("After distance", len(df)))
                            else:
                                filter_counts.append(("After distance (not provided by Auto.dev)", len(df)))

                        before_fuel_filter_count = len(df)
                        df = filter_by_fuel_type(df, fuel_filter)
                        filter_counts.append(("After fuel filter", len(df)))

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
                        filter_counts.append(("After AWD / 4WD only", len(df)))

                    st.session_state["results_df"] = df

                    if df.empty:
                        total_text = autodev_total(raw_response)
                        if raw_listing_count:
                            st.warning("Auto.dev returned listings, but local filters removed them.")
                        else:
                            st.warning("Auto.dev returned no listings for this request. Try widening make/model/year/price/mileage/radius.")
                        if total_text not in (None, ""):
                            st.caption(f"Auto.dev reported total/count: {total_text}")
                        with st.expander("Filter diagnostics"):
                            st.table(pd.DataFrame(filter_counts, columns=["Step", "Rows"]))
                        with st.expander("Raw Auto.dev response"):
                            st.json(raw_response)
                    else:
                        st.success(f"Found {len(df)} listings.")

                except Exception as e:
                    st.error(f"Search failed: {e}")

    df = st.session_state["results_df"]

    if df.empty:
        st.info("Enter search criteria in the sidebar and click **Build report**, or upload a CSV.")
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
