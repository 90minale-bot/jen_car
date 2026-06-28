# Release Notes

## v1.0 - Car Deal Report Dashboard

- Promoted the latest fixed dashboard into `app.py`
- Switched the release app to Auto.dev using `AUTODEV_API_KEY`
- Added quota-friendly defaults and visible Auto.dev call estimates
- Added one-hour caching for repeated identical searches
- Added markdown buyer report export
- Kept CSV upload mode for no-API review workflows
- Archived historical dashboard versions under `archive/`
- Updated documentation and devcontainer startup to use `app.py`

## Follow-up fixes

- Added Auto.dev response parsing fallbacks and filter diagnostics for zero-result searches
- Corrected certified-search parameter to use `retailListing.cpo`
