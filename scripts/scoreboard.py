#!/usr/bin/env python3
"""scoreboard.py: log every post, its views and what it paid, then learn what works.

  python3 -I scoreboard.py add --platform tiktok --url https://www.tiktok.com/@page/video/1 \\
      --streamer IShowSpeed --hook "Speed vs the alligator" --clip clips/01.mp4 --campaign "none"
  python3 -I scoreboard.py update https://www.tiktok.com/@page/video/1 --views 48210 --likes 3100 --paid 0
  python3 -I scoreboard.py report                  # by platform, streamer, hook style, campaign
  python3 -I scoreboard.py report --by streamer --json

The ledger is a plain CSV (default scoreboard.csv in the current folder) you can open anywhere.
Views are whatever you read off the platform; nothing here logs in or scrapes. The report ends with
"next" rules: they are patterns in your own numbers, not predictions.
"""
import argparse
import csv
import json
import os
import re
import statistics
import sys
from datetime import datetime, timezone

FIELDS = ['posted_utc', 'platform', 'url', 'clip', 'streamer', 'hook', 'hook_style', 'length_s', 'campaign',
          'views', 'likes', 'comments', 'shares', 'paid_usd', 'status', 'updated_utc', 'notes']


def hook_style(hook):
    h = (hook or '').strip()
    hl = h.lower()
    if re.match(r'^(pov|when)\b', hl):
        return 'pov/when'
    if '?' in h:
        return 'question'
    if re.search(r'\b(tried to|tries to|attempts?)\b', hl):
        return 'tried-to'
    if re.search(r'\d', h):
        return 'number'
    if h.isupper() or re.search(r'\b[A-Z]{4,}\b', h):
        return 'caps'
    if re.search(r'\.\.\.|…', h):
        return 'cliffhanger'
    return 'statement'


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


def save(path, rows):
    with open(path, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, '') for k in FIELDS})


def now():
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def group_stats(rows, key):
    groups = {}
    for r in rows:
        groups.setdefault(r.get(key) or '(none)', []).append(r)
    out = []
    for k, rs in groups.items():
        views = [num(r['views']) for r in rs if num(r.get('views')) is not None]
        paid = sum(num(r.get('paid_usd')) or 0 for r in rs)
        out.append({key: k, 'posts': len(rs), 'median_views': statistics.median(views) if views else None,
                    'mean_views': round(statistics.mean(views)) if views else None, 'total_views': int(sum(views)),
                    'paid_usd': round(paid, 2), 'hit_rate_10k': round(sum(1 for v in views if v >= 10000) / len(views), 2) if views else None})
    return sorted(out, key=lambda g: -(g['median_views'] or 0))


def lessons(rows):
    out = []
    for key in ('streamer', 'platform', 'hook_style'):
        g = [x for x in group_stats(rows, key) if x['posts'] >= 3 and x['median_views']]
        if len(g) >= 2 and g[-1]['median_views']:
            ratio = g[0]['median_views'] / g[-1]['median_views']
            if ratio >= 1.5:
                out.append(f'{key} "{g[0][key]}" has {ratio:.1f}x the median views of "{g[-1][key]}" '
                           f'({g[0]["posts"]} vs {g[-1]["posts"]} posts): make more of the first')
    lens = [(num(r.get('length_s')), num(r.get('views'))) for r in rows]
    lens = [(l, v) for l, v in lens if l and v is not None]
    if len(lens) >= 6:
        short = [v for l, v in lens if l <= 30]
        long_ = [v for l, v in lens if l > 30]
        if short and long_:
            out.append(f'clips <=30 s: median {statistics.median(short):,.0f} views; >30 s: {statistics.median(long_):,.0f}')
    rejected = [r for r in rows if (r.get('status') or '').lower() == 'rejected']
    if rejected:
        out.append(f'{len(rejected)} campaign rejection(s): read the notes column and add each reason to your clip rules')
    if not out:
        out.append('not enough posts yet: log at least 3 per streamer/platform/hook style to compare')
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--file', default='scoreboard.csv')
    sub = ap.add_subparsers(dest='cmd')
    s = sub.add_parser('add', help='log a new post')
    s.add_argument('--platform', required=True, choices=('tiktok', 'shorts', 'reels', 'x', 'other'))
    s.add_argument('--url', required=True)
    s.add_argument('--clip', default='')
    s.add_argument('--streamer', default='')
    s.add_argument('--hook', default='')
    s.add_argument('--length', type=float, help='clip length in seconds')
    s.add_argument('--campaign', default='')
    s.add_argument('--posted', default='', help='ISO time (default now)')
    s.add_argument('--notes', default='')
    u = sub.add_parser('update', help='update the numbers of a logged post (by URL)')
    u.add_argument('url')
    for f in ('views', 'likes', 'comments', 'shares'):
        u.add_argument(f'--{f}', type=int)
    u.add_argument('--paid', type=float, help='USD actually paid out for this post')
    u.add_argument('--status', choices=('posted', 'submitted', 'approved', 'rejected', 'paid', 'removed'))
    u.add_argument('--notes')
    r = sub.add_parser('report', help='what is working')
    r.add_argument('--by', choices=('platform', 'streamer', 'hook_style', 'campaign'), action='append')
    r.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    rows = load(a.file)
    if a.cmd == 'add':
        if any(x['url'] == a.url for x in rows):
            print(f'already logged: {a.url}', file=sys.stderr)
            return 1
        rows.append({'posted_utc': a.posted or now(), 'platform': a.platform, 'url': a.url, 'clip': a.clip,
                     'streamer': a.streamer, 'hook': a.hook, 'hook_style': hook_style(a.hook),
                     'length_s': a.length or '', 'campaign': a.campaign, 'status': 'posted', 'updated_utc': now(),
                     'notes': a.notes})
        save(a.file, rows)
        print(f'logged #{len(rows)} ({hook_style(a.hook)} hook) -> {a.file}')
        return 0
    if a.cmd == 'update':
        for x in rows:
            if x['url'] == a.url:
                for f in ('views', 'likes', 'comments', 'shares'):
                    if getattr(a, f) is not None:
                        x[f] = getattr(a, f)
                if a.paid is not None:
                    x['paid_usd'] = a.paid
                if a.status:
                    x['status'] = a.status
                if a.notes:
                    x['notes'] = a.notes
                x['updated_utc'] = now()
                save(a.file, rows)
                print(f'updated {a.url}')
                return 0
        print(f'not found: {a.url}', file=sys.stderr)
        return 1
    if a.cmd == 'report':
        keys = a.by or ['platform', 'streamer', 'hook_style', 'campaign']
        rep = {'posts': len(rows), 'total_views': int(sum(num(x.get('views')) or 0 for x in rows)),
               'paid_usd': round(sum(num(x.get('paid_usd')) or 0 for x in rows), 2),
               'groups': {k: group_stats(rows, k) for k in keys},
               'top': sorted(rows, key=lambda x: -(num(x.get('views')) or 0))[:5], 'next': lessons(rows)}
        if a.json:
            print(json.dumps(rep, indent=2))
            return 0
        print(f'{rep["posts"]} posts, {rep["total_views"]:,} views, ${rep["paid_usd"]:,.2f} paid out')
        for k, gs in rep['groups'].items():
            print(f'\nby {k}:')
            for g in gs:
                mv = f'{g["median_views"]:,.0f}' if g['median_views'] is not None else '-'
                print(f'  {g[k][:28]:<28} {g["posts"]:>4} posts  median {mv:>9}  paid ${g["paid_usd"]:,.2f}')
        print('\ntop posts:')
        for x in rep['top']:
            print(f'  {num(x.get("views")) or 0:>10,.0f}  {x["platform"]:<7} {x["hook"][:60]}')
        print('\nnext:')
        for l in rep['next']:
            print(f'  - {l}')
        return 0
    ap.print_help()
    return 2


if __name__ == '__main__':
    sys.exit(main())
