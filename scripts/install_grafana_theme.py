#!/usr/bin/env python3
"""Install the scoped overview stylesheet; re-run after Grafana upgrades."""
import argparse
import hashlib
import re
import shutil
from pathlib import Path


def install(source: Path, grafana_home: Path) -> None:
    index = grafana_home / 'public/views/index.html'
    text = index.read_text()
    if '</head>' not in text:
        raise SystemExit('Grafana index template has no </head>; no files changed.')
    backup = index.with_suffix('.html.aiops-original')
    if not backup.exists():
        shutil.copy2(index, backup)
    assets = grafana_home / 'public/aiops'
    assets.mkdir(parents=True, exist_ok=True)
    version = hashlib.sha256(b''.join((source / f).read_bytes() for f in ('dashboard.css', 'dashboard.js'))).hexdigest()[:12]
    for name in ('dashboard.css', 'dashboard.js'):
        shutil.copyfile(source / name, assets / name)
        (assets / name).chmod(0o644)
    block = f'''<!-- aiops-panel-theme:start -->
<link rel="stylesheet" href="public/aiops/dashboard.css?v={version}">
<script defer src="public/aiops/dashboard.js?v={version}"></script>
<!-- aiops-panel-theme:end -->'''
    text = re.sub(r'\s*<!-- aiops-panel-theme:start -->.*?<!-- aiops-panel-theme:end -->', '', text, flags=re.S)
    index.write_text(text.replace('</head>', f'\n{block}\n</head>', 1))
    print(f'Installed scoped dashboard style {version}. Restart Grafana to reload its template.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1] / 'grafana/theme')
    parser.add_argument('--grafana-home', type=Path, default=Path('/usr/share/grafana'))
    args = parser.parse_args()
    install(args.source, args.grafana_home)
