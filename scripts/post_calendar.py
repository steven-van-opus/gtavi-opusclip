#!/usr/bin/env python3
"""post_calendar.py: a posting calendar (CSV) for a batch of clips across TikTok, Shorts, Reels and X.

  python3 -I post_calendar.py --clips clips/clips.json --start 2026-11-20 --tz America/New_York \\
      --per-day tiktok=3,shorts=2,reels=2,x=4 --window 09:00-23:00 --out calendar.csv

Clips are taken best first (clips.json from opusclip_api.py download keeps OpusClip's order), every
clip goes to every platform once, and each platform's posts are spread evenly over the posting
window with platforms staggered so they don't all land at once. The utc column can go straight into
opusclip_api.py schedule --at (OpusClip's scheduler is a Pro feature), or you post by hand.
Per-day numbers are checked against OpusClip's scheduler limits (TikTok 15/day, YouTube about 15/day,
Instagram 40/day, X 150/day) and each platform's own rules still apply.
"""
import argparse
import csv
import glob
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None

SCHEDULER_DAILY_CAP = {'tiktok': 15, 'shorts': 15, 'reels': 40, 'x': 150}
STAGGER_MIN = {'tiktok': 0, 'shorts': 20, 'reels': 40, 'x': 10}


def parse_per_day(text):
    out = {}
    for part in text.split(','):
        if part.strip():
            k, v = part.split('=')
            k = k.strip().lower()
            if k not in SCHEDULER_DAILY_CAP:
                raise SystemExit(f'unknown platform {k!r}; use tiktok, shorts, reels, x')
            out[k] = int(v)
    return out


def load_clips(path=None, folder=None):
    if path:
        d = json.load(open(path))
        items = d if isinstance(d, list) else d.get('clips', [])
        return [{'file': c.get('file') or c.get('path') or '', 'title': c.get('title') or '',
                 'projectId': c.get('projectId') or '', 'clipId': c.get('clipId') or '',
                 'credit': c.get('credit') or ''} for c in items]
    files = sorted(glob.glob(os.path.join(folder, '*.mp4')))
    return [{'file': os.path.basename(f), 'title': os.path.splitext(os.path.basename(f))[0], 'projectId': '',
             'clipId': '', 'credit': ''} for f in files]


def slots_for(n, window_start, window_end, stagger):
    if n <= 0:
        return []
    span = (datetime.combine(date.today(), window_end) - datetime.combine(date.today(), window_start)).seconds / 60
    step = span / n
    return [int(stagger + step * i + step / 2) for i in range(n)]


def build(clips, start, days, per_day, tz, window, credit_default=''):
    w0, w1 = (time.fromisoformat(x) for x in window.split('-'))
    zone = ZoneInfo(tz) if (ZoneInfo and tz) else timezone.utc
    warnings = [f'{p}: {n}/day is over OpusClip\'s scheduler limit of {SCHEDULER_DAILY_CAP[p]}/day'
                for p, n in per_day.items() if n > SCHEDULER_DAILY_CAP[p]]
    queue = {p: list(range(len(clips))) for p in per_day}
    rows = []
    for d in range(days):
        day = start + timedelta(days=d)
        for plat, n in per_day.items():
            for minute in slots_for(n, w0, w1, STAGGER_MIN.get(plat, 0)):
                if not queue[plat]:
                    break
                k = queue[plat].pop(0)
                c = clips[k]
                local = datetime.combine(day, w0, tzinfo=zone) + timedelta(minutes=minute)
                rows.append({'date': local.date().isoformat(), 'local_time': local.strftime('%H:%M'), 'tz': tz,
                             'utc': local.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'), 'platform': plat,
                             'clip_file': c['file'], 'clip_title': c['title'], 'project_id': c['projectId'],
                             'clip_id': c['clipId'], 'credit': c['credit'] or credit_default,
                             'caption_ref': f'copy_kit.py --hook "{c["title"][:60]}"', 'status': 'planned'})
    left = {p: len(q) for p, q in queue.items() if q}
    if left:
        warnings.append('not every clip fits in the window: ' + ', '.join(f'{p} {n} left' for p, n in left.items()))
    rows.sort(key=lambda r: (r['utc'], r['platform']))
    return rows, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument('--clips', help='clips.json (opusclip_api.py download writes one)')
    src.add_argument('--clips-dir', help='a folder of .mp4 clips (best first by file name)')
    ap.add_argument('--start', default=date.today().isoformat(), help='first day, YYYY-MM-DD')
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--per-day', default='tiktok=3,shorts=2,reels=2,x=4')
    ap.add_argument('--window', default='09:00-23:00', help='local posting window HH:MM-HH:MM')
    ap.add_argument('--tz', default='UTC', help='IANA time zone of your audience, e.g. America/New_York')
    ap.add_argument('--credit', default='', help='credit line for clips that have none, e.g. "IShowSpeed (YouTube)"')
    ap.add_argument('--out', default='calendar.csv')
    a = ap.parse_args(argv)
    clips = load_clips(a.clips, a.clips_dir)
    if not clips:
        print('no clips found', file=sys.stderr)
        return 2
    rows, warnings = build(clips, date.fromisoformat(a.start), a.days, parse_per_day(a.per_day), a.tz, a.window, a.credit)
    with open(a.out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ['date'])
        w.writeheader()
        w.writerows(rows)
    print(f'{len(rows)} posts over {a.days} day(s) -> {a.out}')
    for r in rows[:8]:
        print(f'  {r["date"]} {r["local_time"]} {r["platform"]:<7} {r["clip_title"][:50]}')
    if len(rows) > 8:
        print(f'  ... {len(rows) - 8} more')
    for x in warnings:
        print(f'! {x}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
