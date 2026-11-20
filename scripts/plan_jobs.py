#!/usr/bin/env python3
"""plan_jobs.py: turn picked moments into OpusClip clip jobs, with a credit estimate, before anything is sent.

  python3 -I plan_jobs.py moments.json --source "https://www.youtube.com/watch?v=VOD" \\
      --source-duration 85542 --ranks 1,2,4,7 --out plan.json

For each moment it pads the window (OpusClip needs context around the payoff), merges windows that
sit close together, and routes every job one of two ways:
  url     OpusClip imports the link itself and processes only curationPref.range
          (sources up to 10 h; YouTube, Twitch and the other documented link sources)
  upload  the window is cut locally first (cut_windows.py --plan plan.json), then uploaded with the
          4-step V2 flow. Used when the source is longer than 10 h, is a Kick link (the API source list
          names Twitch but not Kick), or is a local file.
Credits: 1 per minute of processed video, and every API project costs at least 10 credits, so
merging nearby moments saves credits. Nothing is sent: run opusclip_api.py batch plan.json --yes.
Times: a vodwatch scan made from a YouTube VOD's 2-second fragments keeps their timeline, which the
processed VOD can play later (IShowSpeed's launch VOD: 144 s). When run.json next to moments.json
holds that shift (timeline.py measures it), or --timeline-offset gives it, every window is moved
onto the VOD's timeline (scanStart/scanEnd keep the scan's). plan["timeline"] records the state:
"unchecked" means nobody has compared the scan with the processed VOD yet, so cut_windows.py
--plan checks before it cuts and batch holds back url jobs until timeline.py --plan has run.
"""
import argparse
import json
import os
import re
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import timeline  # noqa: E402
from opusclip_api import (MAX_SOURCE_SECONDS, UsageError, build_project_body, estimate_credits, hms,  # noqa: E402
                          parse_durations, parse_time)

DEFAULT_PROMPT = ("the streamer's biggest live reactions: shock, laughing, screaming, chaos, fails, chases and "
                  "funny NPC moments, each with its setup and payoff")


def load_moments(path):
    d = json.load(open(path))
    items = d.get('moments', []) if isinstance(d, dict) else d
    out = []
    for m in items:
        out.append({'rank': m.get('rank'), 'start': float(parse_time(m['start'])), 'end': float(parse_time(m['end'])),
                    'score': m.get('score'), 'label': m.get('label') or m.get('hook') or ''})
    return out, (d.get('source') if isinstance(d, dict) else {}) or {}


def youtube_id(src):
    if re.fullmatch(r'[A-Za-z0-9_-]{11}', src or ''):
        return src
    u = urllib.parse.urlparse(src or '')
    if 'youtu.be' in u.netloc:
        return u.path.strip('/')[:11]
    if 'youtube.com' in u.netloc:
        q = urllib.parse.parse_qs(u.query)
        if 'v' in q:
            return q['v'][0]
        m = re.search(r'/(live|shorts|embed)/([A-Za-z0-9_-]{11})', u.path)
        if m:
            return m.group(2)
    return None


def source_kind(src):
    if src and os.path.exists(src):
        return 'file'
    if youtube_id(src):
        return 'youtube'
    host = urllib.parse.urlparse(src or '').netloc.lower()
    if 'twitch.tv' in host:
        return 'twitch'
    if 'kick.com' in host:
        return 'kick'
    return 'url'


def windows_for(moments, pad_before, pad_after, merge_gap, max_range, duration=None):
    wins = []
    for m in sorted(moments, key=lambda m: m['start']):
        a = max(0.0, m['start'] - pad_before)
        b = m['end'] + pad_after
        if duration:
            b = min(b, duration)
        if wins and a - wins[-1]['end'] <= merge_gap and (max(b, wins[-1]['end']) - wins[-1]['start']) <= max_range:
            wins[-1]['end'] = max(wins[-1]['end'], b)
            wins[-1]['moments'].append(m['rank'])
        else:
            wins.append({'start': a, 'end': b, 'moments': [m['rank']]})
    return wins


def route_for(kind, duration, forced=None):
    notes = []
    if forced and forced != 'auto':
        return forced, notes
    if kind == 'file':
        return 'upload', ['local source: the window is cut with ffmpeg, then uploaded']
    if kind == 'kick':
        return 'upload', ['Kick: the API source list names Twitch but not Kick, so the window is cut and uploaded']
    if duration and duration > MAX_SOURCE_SECONDS:
        return 'upload', [f'source is {duration / 3600:.1f} h; OpusClip imports up to 10 h, so windows are cut locally and uploaded']
    if not duration:
        notes.append('source length unknown: if it is over 10 h the URL import will fail; pass --source-duration or use --route upload')
    if kind == 'youtube':
        notes.append('YouTube can take 10-30 min (short) to 2-12 h (long streams) after a stream ends before the VOD imports')
    return 'url', notes


def build_plan(moments, source, duration=None, pad_before=60, pad_after=45, merge_gap=120, max_range=1200,
               route='auto', prompt=DEFAULT_PROMPT, template='preset-fancy-Gameplay', genre='Gaming',
               durations='0-30,30-60', model='ClipAnything', lang='en', title_prefix='', out_dir='cuts', credit='',
               shift=0.0):
    """shift: seconds the VOD as served now plays the moments' (scan) times later; every window is moved by it."""
    kind = source_kind(source)
    rt, notes = route_for(kind, duration, route)
    if shift:
        moments = [dict(m, start=m['start'] + shift, end=m['end'] + shift) for m in moments]
    if duration:
        outside = [m for m in moments if m['start'] >= duration]
        if outside:
            notes.append(f'skipped {len(outside)} moment(s) that start after the end of the source '
                         f'({", ".join(str(m["rank"]) for m in outside)})')
        moments = [m for m in moments if m['start'] < duration]
    dur_list, odd = parse_durations(durations)
    if odd:
        notes.append(f'undocumented clip durations: {", ".join(odd)}')
    jobs = []
    for i, w in enumerate(windows_for(moments, pad_before, pad_after, merge_gap, max_range, duration), 1):
        jid = f'job{i:02d}'
        title = f'{title_prefix + " " if title_prefix else ""}{hms(w["start"])}-{hms(w["end"])}'.strip()
        seconds = w['end'] - w['start']
        if rt == 'url':
            req = build_project_body(source, title, template or None, model, genre, dur_list,
                                     prompt if model == 'ClipAnything' else None, None, w['start'], w['end'], lang=lang)
            job = {'id': jid, 'route': 'url', 'start': round(w['start'], 2), 'end': round(w['end'], 2),
                   'moments': w['moments'], 'estimatedCredits': estimate_credits(seconds), 'request': req}
        else:
            req = build_project_body('<uploadId>', title, template or None, model, genre, dur_list,
                                     prompt if model == 'ClipAnything' else None, None, lang=lang)
            job = {'id': jid, 'route': 'upload', 'start': round(w['start'], 2), 'end': round(w['end'], 2),
                   'moments': w['moments'], 'estimatedCredits': estimate_credits(seconds), 'request': req,
                   'cut': {'source': source, 'start': round(w['start'], 2), 'end': round(w['end'], 2),
                           'out': os.path.join(out_dir, f'{jid}_{hms(w["start"]).replace(":", "-")}.mp4')}}
        if shift:
            job.update(scanStart=round(w['start'] - shift, 2), scanEnd=round(w['end'] - shift, 2))
        jobs.append(job)
    total_min = sum(j['end'] - j['start'] for j in jobs) / 60
    return {
        'source': source, 'sourceKind': kind, 'sourceDuration': duration, 'route': rt, 'credit': credit,
        'jobs': jobs,
        'totals': {'jobs': len(jobs), 'minutes': round(total_min, 1),
                   'estimatedCredits': sum(j['estimatedCredits'] for j in jobs)},
        'notes': notes + ['every API project costs at least 10 credits (web submissions have no minimum)',
                          'Pro and Max: 4 projects processing at once, 15 h (900 credits) of API use a month',
                          'nothing has been sent: review, then run opusclip_api.py batch plan.json --yes'],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('moments', help='moments.json from rank_moments.py (or a list of {rank,start,end})')
    ap.add_argument('--source', help='VOD URL, YouTube id or local file (default: the source recorded in moments.json)')
    ap.add_argument('--source-duration', type=float, help='source length in seconds (decides url vs upload)')
    ap.add_argument('--ranks', help='comma-separated ranks to keep, e.g. 1,2,5')
    ap.add_argument('--top', type=int, help='keep the best N moments')
    ap.add_argument('--pad-before', type=float, default=60)
    ap.add_argument('--pad-after', type=float, default=45)
    ap.add_argument('--merge-gap', type=float, default=120, help='merge windows closer than this (seconds)')
    ap.add_argument('--max-range', type=float, default=1200, help='longest merged window (seconds)')
    ap.add_argument('--route', default='auto', choices=('auto', 'url', 'upload'))
    ap.add_argument('--prompt', default=DEFAULT_PROMPT, help='ClipAnything customPrompt')
    ap.add_argument('--template', default='preset-fancy-Gameplay')
    ap.add_argument('--genre', default='Gaming')
    ap.add_argument('--durations', default='0-30,30-60')
    ap.add_argument('--model', default='ClipAnything', choices=('ClipAnything', 'ClipBasic'))
    ap.add_argument('--lang', default='en')
    ap.add_argument('--title-prefix', default='', help='e.g. "IShowSpeed GTA VI stream"')
    ap.add_argument('--credit', default='', help='credit line to carry with the clips, e.g. "IShowSpeed (YouTube)"')
    ap.add_argument('--cut-dir', default='cuts')
    ap.add_argument('--out', default='plan.json')
    ap.add_argument('--timeline-offset', type=float, metavar='SECONDS',
                    help='the VOD as served now plays the moments\' times this much later (default: the shift kept in '
                         'run.json next to moments.json, see timeline.py; 0 = send the scan\'s times as they are)')
    a = ap.parse_args(argv)
    try:
        moments, src_meta = load_moments(a.moments)
        source = a.source or src_meta.get('url') or (src_meta.get('video_id') and
                                                     f'https://www.youtube.com/watch?v={src_meta["video_id"]}')
        if not source:
            raise UsageError('no --source given and none recorded in moments.json')
        if a.ranks:
            keep = {int(x) for x in a.ranks.split(',') if x.strip()}
            moments = [m for m in moments if m['rank'] in keep]
        elif a.top:
            moments = sorted(moments, key=lambda m: m['rank'] or 1e9)[:a.top]
        if not moments:
            raise UsageError('no moments selected')
        scan_dir = os.path.dirname(os.path.abspath(a.moments))
        shift, state, scan = timeline.known(scan_dir, source)   # what run.json already knows; YouTube isn't asked
        if a.timeline_offset is not None:
            shift, state = a.timeline_offset, 'given'
        plan = build_plan(moments, source, a.source_duration, a.pad_before, a.pad_after, a.merge_gap, a.max_range,
                          a.route, a.prompt, a.template, a.genre, a.durations, a.model, a.lang, a.title_prefix,
                          a.cut_dir, a.credit or src_meta.get('label', ''), shift)
        if scan is not None or state == 'given':
            plan['timeline'] = {'scan': scan, 'scanDir': scan_dir, 'offset': shift, 'state': state}
        if state == 'unchecked':
            plan['notes'].insert(0, 'times are from a scan of the VOD\'s 2-second fragments that nobody has compared '
                                    'with the processed VOD yet: cut_windows.py --plan checks before it cuts, and batch '
                                    'holds back url jobs until timeline.py --plan has run (once YouTube has processed '
                                    'the VOD)')
        elif shift:
            plan['notes'].insert(0, f'windows moved to VOD time = scan time + {shift:.2f} s ({state}); '
                                    'scanStart/scanEnd keep the scan\'s times')
    except UsageError as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    with open(a.out, 'w') as w:
        json.dump(plan, w, indent=2)
    print(f'{"job":<6} {"route":<7} {"window":<19} {"min":>5} {"credits":>7}  moments')
    for j in plan['jobs']:
        print(f'{j["id"]:<6} {j["route"]:<7} {hms(j["start"])}-{hms(j["end"])} {(j["end"] - j["start"]) / 60:>5.1f} '
              f'{j["estimatedCredits"]:>7}  {",".join(str(x) for x in j["moments"])}')
    t = plan['totals']
    print(f'\n{t["jobs"]} job(s), {t["minutes"]} min, about {t["estimatedCredits"]} credits. Route: {plan["route"]}.')
    for n in plan['notes']:
        print(f'- {n}')
    print(f'wrote {a.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
