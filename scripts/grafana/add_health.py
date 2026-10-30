#!/usr/bin/env python3
"""Idempotently add the shared health summary to the operations dashboard."""
import copy
import json
from build_report import ROOT, build

path = ROOT / 'grafana/dashboards/aiops-overview.json'
dashboard = json.loads(path.read_text())
if not any(9001 <= p['id'] <= 9006 for p in dashboard['panels']):
    for panel in dashboard['panels']:
        panel['gridPos']['y'] += 11
        for nested in panel.get('panels', []):
            nested['gridPos']['y'] += 11
else:
    dashboard['panels'] = [p for p in dashboard['panels'] if not 9001 <= p['id'] <= 9006]
health = copy.deepcopy([p for p in build('zh')['panels'] if 9001 <= p['id'] <= 9006])
for panel in health:
    panel['gridPos']['y'] -= 4
    if panel['id'] == 9001:panel['title'] = '网络健康评估分 / Network Health · 100'
    if panel['id'] == 9002:panel['title'] = '评分证据覆盖率 / Evidence Coverage'
dashboard['panels'] = sorted(dashboard['panels'] + health, key=lambda p:(p['gridPos']['y'],p['gridPos']['x']))
path.write_text(json.dumps(dashboard, ensure_ascii=False, indent=2)+'\n')
