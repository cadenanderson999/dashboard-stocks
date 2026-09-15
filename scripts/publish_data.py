"""Restore live data, optionally refresh it, and stage only public site files."""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import market_data as md

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / '.cache/site-data'
DATASETS = ('stocks.json', 'details.json', 'leaps.json', 'rvol_scan.json', 'earnings_calendar.json')


def mark_failed(path):
    doc = md.read_json(path)
    if not doc or doc.get('is_sample'):
        return
    doc['refresh_status'] = 'failed'
    doc['last_attempt_at'] = datetime.now(timezone.utc).isoformat()
    if path.name == 'stocks.json':
        for row in doc.get('stocks', []):
            row.setdefault('data_quality', {}).setdefault('prices', {}).update(
                stale=True, reason='refresh_failed')
            row.update(rating='Stale', score=None, rs_rank=None, setups=[])
    md.atomic_json(path, doc)


def stage_site():
    stage = ROOT / '_site'
    stage.mkdir(exist_ok=True)
    for pattern in ('*.html', '*.webmanifest', 'sw.js', 'CNAME'):
        for path in ROOT.glob(pattern):
            shutil.copy2(path, stage / path.name)
    for name in ('assets', 'data'):
        shutil.copytree(ROOT / name, stage / name, dirs_exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh', action='store_true')
    parser.add_argument('--mode', choices=['full','quotes','calendar','recovery'], default='full')
    args = parser.parse_args()
    SNAPSHOT.mkdir(parents=True, exist_ok=True)
    for name in DATASETS:
        old = md.read_json(SNAPSHOT / name)
        if old and old.get('is_sample') is False:
            md.atomic_json(ROOT / 'data' / name, old)
    if args.refresh:
        if args.mode in ('full', 'calendar', 'recovery'):
            subprocess.run([sys.executable, str(ROOT/'scripts/refresh_extras.py'), 'calendar'], cwd=ROOT, check=True)
        if args.mode == 'quotes':
            subprocess.run([sys.executable, str(ROOT/'scripts/refresh_extras.py'), 'quotes'], cwd=ROOT, check=True)
        tasks = [
            ('generate_data.py', ('stocks.json', 'details.json')),
            ('generate_leaps.py', ('leaps.json',)),
            ('generate_rvol_scan.py', ('rvol_scan.json',)),
        ]
        if args.mode == 'quotes':
            tasks = []
        elif args.mode == 'calendar':
            tasks = tasks[:1]
        elif args.mode == 'recovery':
            tasks = tasks[:2]
        for script, outputs in tasks:
            result = subprocess.run([sys.executable, str(ROOT / 'scripts' / script)], cwd=ROOT)
            if result.returncode:
                print(f'::warning::{script} could not fully refresh; retaining dated data.')
                for name in outputs:
                    mark_failed(ROOT / 'data' / name)
    stocks = md.read_json(ROOT / 'data/stocks.json')
    if stocks.get('is_sample') is not False or not stocks.get('stocks'):
        print('No valid live stock snapshot. Run a manual refresh before deploying.', file=sys.stderr)
        return 1
    for name in DATASETS:
        path = ROOT / 'data' / name
        doc = md.read_json(path)
        if not doc or doc.get('is_sample') is not False:
            doc = {'is_sample': False, 'generated_at': None,
                   'refresh_status': 'unavailable', 'count': 0,
                   'stocks': {} if name == 'details.json' else [],
                   'candidates': [], 'chains_available': False}
            md.atomic_json(path, doc)
        md.atomic_json(SNAPSHOT / name, doc)
    stage_site()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
