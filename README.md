# Country Dairy — Sales Dashboard

A static dashboard that maps where Country Dairy's customers are and how much
they buy, plus top-customer and product rankings. Built to be published on
**GitHub Pages** and refreshed once a quarter.

## What's here

| File | Purpose |
|------|---------|
| `index.html` | The dashboard (map, stats, charts). Pure static — no build step. |
|  | Map controls: multi-select **Products** filter, a **Top 3 customers** show/hide slider, and a **Heatmap / Sized dots / Per capita** view toggle. |
| `country_dairy_sales.csv` | The quarterly sales export. **Replace this each quarter.** |
| `build_data.py` | Geocodes the CSV and writes `data/*.json`. |
| `data/customers.json` | Per-customer points (lat/lng + cases) for the map. Generated. |
| `data/summary.json` | Totals, rankings, and per-ZIP population for the per-capita layer. Generated. |
| `zcta_pop.json` | Cached ZIP→population table (~33k ZIPs). Committed so rebuilds are offline; refresh with `--refresh-pop`. |
| `assets/logo.png` | Country Dairy logo. |

The **Per capita** view normalizes volume to population: one circle per ZIP,
colored by cases per 1,000 residents (market penetration) and sized by
population. Population comes from
[Ro-Data's US Census ZIP summaries](https://github.com/Ro-Data/Ro-Census-Summaries-By-Zipcode)
(2020 Census / ACS, public domain).

The map uses [Leaflet](https://leafletjs.com/) + Leaflet.heat with free
OpenStreetMap/CARTO tiles (no API key). Customers are geocoded to their
**street address** via the free [US Census batch geocoder](https://geocoding.geo.census.gov/)
(no key, needs internet). Any address the Census can't match falls back to its
**ZIP-code centroid** via [`pgeocode`](https://pypi.org/project/pgeocode/)
(offline) — those points are marked "(approx.)" in the map popup. Run with
`--zip-only` to skip street geocoding entirely (fully offline).

## Updating each quarter

1. Export the new sales report and save it over `country_dairy_sales.csv`
   (same column headers as before — `CUST #, DESCRIPTION, … , TOTAL CASES`).
2. Regenerate the data files, passing the sales-period date (shown in the
   header as "Sales Data from MM-DD-YYYY"):
   ```bash
   python3 -m pip install -r requirements.txt   # first time only
   python3 build_data.py --as-of 2026-06-30     # e.g. quarter close
   ```
   If you omit `--as-of`, it falls back to the CSV file's last-modified date.
3. Commit and push:
   ```bash
   git add country_dairy_sales.csv data/
   git commit -m "Sales data: <quarter> refresh"
   git push
   ```
   GitHub Pages redeploys automatically.

The build prints how many customers were mapped. Customers with a missing or
unrecognized ZIP are **still counted** in every total — they just aren't
plotted.

## Previewing locally

The page loads `data/*.json` with `fetch`, so it must be served over http
(opening the file directly won't work):

```bash
python3 -m http.server 8000
# then open http://localhost:8000
```

## Publishing on GitHub Pages

Push this folder to a repo, then in **Settings → Pages** set the source to the
`main` branch, root (`/`). The site goes live at
`https://<user>.github.io/<repo>/`.

## Planned additions

The "More metrics" section has placeholder cards for visualizations that need
more than one quarter of data or extra fields:

- **Quarter-over-quarter growth** — trend once several exports are archived.
- **Product mix by region** — leading products per route/county.
- **Route density & efficiency** — cases and stops per delivery route.

`build_data.py` already parses the `ROUTE` column and per-product columns, so
those are the natural next builds.
