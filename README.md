# Country Dairy — Sales Dashboard

A static dashboard that maps where Country Dairy's customers are and how much
they buy, plus top-customer and product rankings. Built to be published on
**GitHub Pages** and refreshed once a quarter.

## What's here

| File | Purpose |
|------|---------|
| `index.html` | The dashboard (map, stats, charts). Pure static — no build step. |
| `country_dairy_sales.csv` | The quarterly sales export. **Replace this each quarter.** |
| `build_data.py` | Geocodes the CSV and writes `data/*.json`. |
| `data/customers.json` | Per-customer points (lat/lng + cases) for the map. Generated. |
| `data/summary.json` | Totals, top 10 customers, product ranking. Generated. |
| `assets/logo.png` | Country Dairy logo. |

The map uses [Leaflet](https://leafletjs.com/) + Leaflet.heat with free
OpenStreetMap/CARTO tiles (no API key). Customers are placed by **ZIP-code
centroid** via [`pgeocode`](https://pypi.org/project/pgeocode/) — offline and
reproducible, so a rebuild always produces the same map.

## Updating each quarter

1. Export the new sales report and save it over `country_dairy_sales.csv`
   (same column headers as before — `CUST #, DESCRIPTION, … , TOTAL CASES`).
2. Regenerate the data files:
   ```bash
   python3 -m pip install -r requirements.txt   # first time only
   python3 build_data.py
   ```
3. Commit and push:
   ```bash
   git add country_dairy_sales.csv data/
   git commit -m "Sales data: <quarter> refresh"
   git push
   ```
   GitHub Pages redeploys automatically.

The build prints how many customers were mapped. Customers with a missing or
unrecognized ZIP are **still counted** in every total — they just aren't
plotted. The dashboard footer notes the count when that happens.

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
