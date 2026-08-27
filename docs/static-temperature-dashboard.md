# Static Berlin temperature dashboard

The dashboard combines the current 25-hour forecast, compact PLR summary,
individual historical-year trajectories, PLR names, and simplified Berlin
planning-area boundaries. It is designed first for local iteration and later
for an ordinary static website.

## Local preview

Prerequisites:

- PostgreSQL contains a complete 25-point forecast.
- `bash scripts/bootstrap_database.sh` has installed the feature objects.
- For all 31 historical lines, run
  `python -m src.historical_temperature_trajectories` first. The dashboard
  still displays forecasts and medians when only the compact snapshot exists.

Export and serve the dashboard:

```bash
uv run --env-file .env \
  python -m src.export_temperature_dashboard --serve
```

Open <http://127.0.0.1:8765/>. Stop the preview with `Ctrl-C`.

After updating dashboard code, reload the page once. The stylesheet and script
carry an asset-version query so browsers and static hosts do not reuse an older
interactive layer after a corrected export is uploaded.

The command writes generated files beneath:

```text
web/temperature-dashboard/data/
```

Those files are excluded from Git. Edit `index.html`, `styles.css`, or `app.js`
and reload the browser to iterate. Run the exporter again after the current
complete forecast changes.

Do not open `index.html` directly with a `file://` URL: browsers restrict JSON
requests from local files. The included local HTTP server behaves like the
eventual static host.

## Interaction

- Hover over a PLR to update the detail panel.
- Click a PLR or a search result to pin it before moving across other areas.
- Press `Esc` to unpin the selected PLR and resume hover exploration.
- Search by PLR name or eight-digit ID.
- Change map shading between highest forecast temperature, the highest
  apparent-minus-forecast temperature difference, and residents aged 65 or
  older.
- The apparent-minus-forecast map mode shows coordinated forecast-temperature
  and forecast-apparent-temperature charts on the right. Both compare the
  current forecast with the PLR's historical median and 31 individual years.
- Hover over either chart to inspect the forecast, median, and historical
  range at one Berlin-local hour.
- Use arrow keys to move between map areas and `Enter` or `Space` to pin one.

## Export a standalone publish folder

Choose a directory outside the repository when preparing a website upload:

```bash
uv run --env-file .env \
  python -m src.export_temperature_dashboard \
    --output-dir /tmp/berlin-temperature-dashboard
```

The output is complete:

```text
berlin-temperature-dashboard/
├── index.html
├── styles.css
├── app.js
└── data/
    ├── map.json
    └── areas/
        ├── 01100101.json
        └── ... one file per PLR
```

Upload the directory unchanged using the website's normal FTP, SSH, or static
deployment workflow. No PostgreSQL port, password, Python process, Dagster
instance, or server-side module belongs on the website.

The frontend deliberately has no external runtime dependency. If integrating
it into an existing page rather than publishing it as a standalone page, the
lowest-risk first iteration is an `iframe` pointing to this directory. Direct
HTML integration is also possible later by copying the dashboard `<main>`,
loading its stylesheet and script, and preserving the relative `data/` paths.

## Export behavior and checks

- PostGIS applies topology-preserving boundary simplification in meters before
  export. The default is 20 metres; use `--simplify-meters` to benchmark another
  value.
- The map export requires exactly one summary and polygon per installed PLR.
- Each detail requires exactly 25 temperature and apparent-temperature
  forecast and median points.
- If any historical detail exists, every PLR must contain all 31 years and 25
  points per year for both indicators. Partial histories fail the export
  rather than produce a misleading chart.
- Files are replaced only after complete JSON serialization.
- Human-readable names appear in presentation files; PLR IDs remain the lookup
  and filename key.

Run the focused database-free tests:

```bash
uv run python -m pytest tests/test_temperature_dashboard.py
```

Then run a real export and inspect the generated footprint:

```bash
du -sh web/temperature-dashboard/data
find web/temperature-dashboard/data/areas -type f | wc -l
```

The second command should report 542 detail files.
