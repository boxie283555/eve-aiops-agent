"""Branded, bilingual monitoring UI; no Grafana iframe or external assets."""
from pathlib import Path

MONITOR_PAGE = Path(__file__).with_name('monitoring.html').read_text(encoding='utf-8')
