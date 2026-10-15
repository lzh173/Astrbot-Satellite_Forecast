# AstrBot Satellite Forecast

AstrBot satellite forecast plugin. It downloads TLE data from CelesTrak, reads transmitter data from SatNOGS, stores the local catalog in SQLite, and calculates satellite passes for each user's observing location.

## Install

Clone this repository into AstrBot's plugin directory:

```text
AstrBot/data/plugins/astrbot_plugin_satellite_forecast
```

Install the plugin dependencies from `requirements.txt`, then reload the plugin in AstrBot WebUI. The runtime database is stored under AstrBot's data directory and is not stored in the plugin source tree.

## Commands

```text
/q
/q 1 Xiamen
/q 1 PM53
/q 2 metop-c
/q 5 fy2g
/q add metop-c
/q del metop-c
/q pass metop-c
/q set elevation 10
/q 4
/q 6
```

`/q 1` binds a location to the current user. Locations support place names and Maidenhead locators. A user location is shared across that user's private and group conversations on the same platform.

Satellite search accepts names, aliases, descriptions, international designators, and NORAD catalog IDs. Inputs such as `metop-c` and `fy2g` are supported when they exist in the synchronized catalog.

`/q 2` lists followed satellites; `/q 2 metop-c` calculates visible passes directly. `/q 4` shows the bound location, followed satellites, and a next-pass summary. `/q 5 fy2g` performs a fuzzy catalog search.

## LLM tools

The plugin registers tools for resolving a place name or Maidenhead locator, binding and reading the current user's location, fuzzy satellite search, managing followed satellites, and predicting passes using the current user's location.

The `/q ai ...` command sends a prompt to the current AstrBot provider with the plugin's registered tools.

## Configuration

The included `_conf_schema.json` exposes startup synchronization, the daily synchronization interval, a data directory, and CelesTrak/SatNOGS-compatible endpoints. CelesTrak supports `tle` and `json` formats. A compatible mirror can be supplied by changing the endpoint while keeping the corresponding format.

## Data sources

- CelesTrak: active satellite TLE data.
- SatNOGS DB: transmitter records associated with the NORAD catalog ID.
- OpenStreetMap Nominatim: place-name geocoding when a Maidenhead locator is not supplied.

The source parser interfaces are kept separate so a configured mirror can provide CelesTrak-compatible or SatNOGS-compatible payloads later.

## Development

```text
pip install -r requirements.txt pytest pytest-asyncio
pytest -q
```
