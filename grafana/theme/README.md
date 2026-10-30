# Overview panel styling

Scoped to dashboard UID `eve-aiops-overview` and its single-panel view:

- Bold 16px titles on a blue title bar.
- 2px panel borders, rounded corners and extra spacing between cards.
- Dark and light palette support.

Grafana 11.3 does not expose title-bar colors or panel border thickness in dashboard JSON. `install_grafana_theme.py` copies these two static assets into Grafana's public directory and adds a marked link/script block to the index template. It preserves an initial `index.html.aiops-original` backup and can be run repeatedly. No Grafana application bundle or HTML sanitization setting is changed.

Run on the Grafana server from the repository:

```sh
sudo python3 scripts/install_grafana_theme.py
sudo systemctl restart grafana-server
```

Re-run after Grafana package upgrades, which may replace the index template. Check the panel/header `data-testid` selectors after a major Grafana upgrade. `scripts/install_remote.sh` also installs the styling.

Panel order lives in `grafana/dashboards/aiops-overview.json`: Interface View is last and includes Interface Problems, Interface Traffic, Interface Operational State, and Interface Errors and Discards. Existing queries, thresholds, panel IDs, filters and refresh settings are preserved.

To remove the styling, remove the `aiops-panel-theme:start/end` block from `/usr/share/grafana/public/views/index.html` and restart Grafana. This does not change the dashboard layout.
