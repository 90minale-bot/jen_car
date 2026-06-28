# Car Deal Report Dashboard

A Streamlit dashboard for building a ranked used-car shortlist from Auto.dev listings.

The app is designed around limited monthly API quotas: search once, cache the result, filter locally, and export a buyer-ready report.

## Features

- Search Auto.dev by make, model, ZIP, radius, year, price, mileage, car type, and fuel type
- Hybrid-friendly filtering that checks fuel, trim, title, engine, and powertrain text
- Local filtering after the API call so returned rows still respect price, mileage, year, distance, and AWD preferences
- 0-100 vehicle scoring based on market gap, mileage, distance, age, and fuel preference
- CSV upload mode for reviewing saved or manually collected listings without using API calls
- One-click CSV export and markdown buyer report export
- One-hour Streamlit cache for repeated identical searches

## Run Locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## API Key

Create an API key at https://www.auto.dev/dashboard/api-keys, then set it in one of these places:

```bash
AUTODEV_API_KEY=your_key_here
```

Or in Streamlit Cloud secrets:

```toml
AUTODEV_API_KEY = "your_key_here"
```

The app also accepts `AUTO_DEV_API_KEY`, `AUTODEV_TOKEN`, or `AUTO_DEV_TOKEN` if you prefer those names.

The app still works without an API key when you upload a CSV.

## Quota-Friendly Workflow

1. Start with the default 2-page search.
2. Review whether the result set is useful.
3. Use filters locally instead of re-running the API search.
4. Download the buyer report or CSV.
5. Increase pages only when a paid or high-intent report needs deeper coverage.

Each Auto.dev page is one API call. The sidebar shows the maximum call count before you build a report.

## Release Structure

- `app.py` is the release entry point.
- `archive/` contains older experiment and version files.
- `requirements.txt` contains the runtime dependencies.

## Disclaimer

Scores are prioritization aids, not guarantees. Always verify listing details, vehicle history, title status, accident history, service records, recalls, taxes/fees, and get a pre-purchase inspection before buying.
