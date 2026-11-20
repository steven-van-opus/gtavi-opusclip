#!/usr/bin/env python3
"""timeline.py: keep a scan's times pointing at the same moments in the VOD as YouTube serves it now.

While YouTube still serves a finished stream as 2-second fragments, ytfrag.py downloads those, and a scan of them
uses their timeline. Once YouTube has processed the VOD into whole files, the same moment can sit at another time:
IShowSpeed's GTA 6 launch VOD (qSyo5d-dVzA) plays everything about 144 s later than its fragments did. Frame sheets,
cuts and OpusClip ranges taken from such a scan have to add that shift, or every window lands early.

  python3 -I timeline.py vodwatch/speed                    # the shift for a vodwatch scan folder (measured once)
  python3 -I timeline.py --plan vodwatch/speed/plan.json   # also move the plan's windows onto it

The shift (VOD time now = scan time + shift) is found like this:
  scan audio from yt-dlp (a processed VOD), ffmpeg (a local file), Twitch or Kick: 0, nothing is asked.
  scan audio from ytfrag.py, or a run.json with no record of it (vodwatch 1.0.0 used ytfrag.py for YouTube):
    yt-dlp --print protocol first. While the VOD is still fragments the scan's times are right: 0 for now.
    Once it is processed: about 60 s of audio format 139 is fetched at two moments 10+ minutes apart (yt-dlp
    --download-sections) and cross-correlated with the scan's own audio.m4a (loudness envelopes, 10 ms steps).
    Both must match clearly (r >= 0.75, well clear of the next-best match) and agree within 1 s; a probe that
    doesn't match (silence, the edge of the scan) is replaced by the next moment, up to 4 fetches. The mean is
    kept in run.json as "timeline_offset" and reused from then on.
  A weak or disagreeing match stops with the numbers: scan the processed VOD again into a new folder (yt-dlp
  then fetches the processed audio, so the times match it), or pass --timeline-offset SECONDS to frame_sheet.py,
  cut_windows.py or plan_jobs.py.
vodwatch.py run, frame_sheet.py --moments and cut_windows.py --plan do this by themselves; plan_jobs.py uses a
shift already kept in run.json. Read-only: yt-dlp --print and two short audio sections, deleted afterwards.
"""
import argparse
import datetime
import json
import math
import operator
import os
import shutil
import subprocess
import sys
import tempfile
from array import array

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from cut_windows import youtube_id  # noqa: E402
from ytfrag import FRAGMENTED, Source  # noqa: E402

PROBE_SECONDS = 60.0         # VOD audio fetched per measurement
MAX_SHIFT = 900.0            # how far the match is searched for, both ways
SECOND_POINT_AFTER = 600.0   # a second measurement this far from the first catches a shift that drifts
MIN_R = 0.75                 # the same audio scores 0.97-0.99 here; unrelated audio stays near or below 0.5
MIN_MARGIN = 0.15            # ... and the best match has to stand clear of the next-best one (3 s or more away)
MAX_SPREAD = 1.0             # the two measurements must agree this closely
SR, RATE = 4000, 100         # decode rate; envelope steps per second (10 ms)
SCAN_FROM_FRAGMENTS = ('fragments', 'unknown')
FINAL = ('none', 'measured', 'given')   # a plan in one of these states is on the VOD's timeline for good


def hms(sec):
    sec = int(round(sec or 0))
    return f'{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def load_run(scan_dir):
    p = os.path.join(scan_dir or '.', 'run.json')
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        return json.load(f)


def save_run(scan_dir, run):
    p = os.path.join(scan_dir, 'run.json')
    with open(p + '.tmp', 'w') as w:
        json.dump(run, w, indent=2)
    os.replace(p + '.tmp', p)


def basis(run):
    """Which timeline the scan's audio is in: 'fragments' (ytfrag.py), 'processed' (yt-dlp from a processed YouTube
    VOD), 'file', 'other' (Twitch, Kick and other links), 'unknown' (no record; vodwatch 1.0.0 always used
    ytfrag.py for YouTube), or None without a run.json (not a vodwatch scan folder)."""
    if run is None:
        return None
    a = run.get('audio') or {}
    if a.get('via') == 'ytfrag.py':
        return 'fragments'
    if a.get('via') == 'yt-dlp':
        return 'processed' if a.get('protocol') else 'other'
    if a.get('via') == 'ffmpeg':
        return 'file'
    kind = (run.get('source') or {}).get('kind')
    return 'file' if kind == 'file' else 'other' if kind in ('twitch', 'kick', 'url') else 'unknown'


def cached(run):
    rec = (run or {}).get('timeline_offset')
    return float(rec['seconds']) if isinstance(rec, dict) and rec.get('seconds') is not None else None


def known(scan_dir, source, run=None):
    """(seconds, state, basis) from what the scan folder already records; never asks YouTube. state: 'none' (nothing
    can be off), 'measured' (run.json has the shift) or 'unchecked' (a fragment scan nobody has compared yet)."""
    run = load_run(scan_dir) if run is None else run
    b = basis(run)
    if b not in SCAN_FROM_FRAGMENTS or not youtube_id(source or ''):
        return 0.0, 'none', b
    c = cached(run)
    return (c, 'measured', b) if c is not None else (0.0, 'unchecked', b)


def resolve(scan_dir, source, at, run=None):
    """(seconds, state): what to add to this scan's times to reach the same moments in `source` as YouTube serves it
    now. state 'none' / 'measured' as in known(), or 'pending': the VOD is still fragments, so 0 for now. Asks yt-dlp
    and measures (once) only for a fragment scan; `at` are scan times to measure around; `run` is the folder's
    run.json when the caller already holds it (it is updated and saved)."""
    vid = youtube_id(source or '')
    if run is None:
        run = load_run(scan_dir)
    b = basis(run)
    if vid and b is None:
        print(f'note: {scan_dir} has no run.json, so this scan\'s timeline can\'t be checked. If its audio came from '
              'ytfrag.py and YouTube has processed the VOD since, the windows land early: pass --timeline-offset '
              'SECONDS.', file=sys.stderr)
    if not vid or b not in SCAN_FROM_FRAGMENTS:
        return 0.0, 'none'
    c = cached(run)
    if c is not None:
        return c, 'measured'
    proto = Source(vid).protocol('139/ba')
    if proto == FRAGMENTED:
        print(f'{vid}: YouTube still serves it as 2-second fragments, the timeline this scan used', file=sys.stderr)
        return 0.0, 'pending'
    audio = os.path.join(scan_dir, 'audio.m4a')
    if not (os.path.isfile(audio) and os.path.getsize(audio) > 0):
        raise SystemExit(f'{vid} is a processed VOD now (protocol {proto}), but {scan_dir} was scanned from its '
                         '2-second fragments and has no audio.m4a left to measure the shift between the two timelines '
                         'against. Scan the VOD again into a new folder, or pass --timeline-offset SECONDS.')
    print(f'{vid}: YouTube has processed this VOD since the scan, which used its 2-second fragments; measuring the '
          f'shift against {audio}', file=sys.stderr)
    rec = measure(vid, audio, at)
    run['timeline_offset'] = rec
    save_run(scan_dir, run)
    print(f'{vid}: VOD time now = scan time + {rec["seconds"]:.2f} s (kept in run.json)', file=sys.stderr)
    return rec['seconds'], 'measured'


def good(m):
    return m['shift'] is not None and m['r'] >= MIN_R and m['margin'] >= MIN_MARGIN


def fetch_probe(vid, a, b, tmp):
    """About a minute of the VOD's audio as YouTube serves it now (format 139, AAC 48k)."""
    base = os.path.join(tmp, 'probe')
    cmd = ['yt-dlp', '--no-warnings', '--no-playlist', '-f', '139/ba', '--download-sections', f'*{a:.2f}-{b:.2f}',
           '-o', base + '.%(ext)s', f'https://www.youtube.com/watch?v={vid}']
    print('+ ' + ' '.join(cmd), file=sys.stderr, flush=True)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        raise SystemExit(f'yt-dlp did not fetch {hms(a)}-{hms(b)} of {vid} within 15 minutes; run the same command again')
    got = sorted(f for f in os.listdir(tmp) if f.startswith('probe.') and not f.endswith(('.part', '.ytdl'))
                 and '.part-Frag' not in f)
    if r.returncode != 0 or not got:
        err = (r.stderr.strip().splitlines() or ['no file written'])[-1]
        raise SystemExit(f'yt-dlp could not fetch {hms(a)}-{hms(b)} of {vid} to measure the timeline shift: {err}')
    return os.path.join(tmp, got[0])


def envelope(path, start=0.0, dur=None):
    """Loudness envelope of an audio (or video) file: log(1 + mean |sample|) per 10 ms, mono at 4 kHz."""
    cmd = ['ffmpeg', '-loglevel', 'error', '-nostdin']
    if start > 0:
        cmd += ['-ss', f'{start:.3f}']
    if dur:
        cmd += ['-t', f'{dur:.3f}']
    cmd += ['-i', path, '-vn', '-ac', '1', '-ar', str(SR), '-f', 's16le', '-c:a', 'pcm_s16le', '-']
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f'ffmpeg could not decode {path}: {r.stderr.decode("utf-8", "replace").strip()[-300:]}')
    pcm = array('h')
    pcm.frombytes(r.stdout[:len(r.stdout) // 2 * 2])
    if sys.byteorder == 'big':
        pcm.byteswap()
    n = SR // RATE
    return [math.log1p(sum(map(abs, pcm[i:i + n])) / n) for i in range(0, len(pcm) - n + 1, n)]


def pearson(p, s, lags):
    """Pearson r of p against s[k:k+len(p)] for every k in lags (prefix sums keep it linear in len(s))."""
    m = len(p)
    mean = sum(p) / m
    pc = [x - mean for x in p]
    norm = math.sqrt(sum(x * x for x in pc))
    c1, c2 = [0.0], [0.0]
    for v in s:
        c1.append(c1[-1] + v)
        c2.append(c2[-1] + v * v)
    out = {}
    for k in lags:
        tot = c1[k + m] - c1[k]
        var = c2[k + m] - c2[k] - tot * tot / m
        out[k] = sum(map(operator.mul, pc, s[k:k + m])) / (norm * math.sqrt(var)) if var > 1e-9 and norm > 0 else 0.0
    return out


def blocks(x, f=10):
    return [sum(x[i:i + f]) / f for i in range(0, len(x) - f + 1, f)]


def locate(probe, audio, a):
    """Where `probe` (VOD audio from VOD time a) sits in the scan's audio: shift = a - its scan time, the r of the
    match, and its margin over the next-best match 3 s or more away. Coarse at 100 ms, then 10 ms, then a parabola."""
    p = envelope(probe)
    out = {'at': round(a, 2), 'shift': None, 'r': 0.0, 'margin': 0.0}
    if len(p) < 20 * RATE:
        return out
    s0 = max(0.0, a - MAX_SHIFT)
    s = envelope(audio, s0, len(p) / RATE + 2 * MAX_SHIFT)
    if len(s) < len(p) + RATE:
        return out
    pb, sb = blocks(p), blocks(s)
    coarse = pearson(pb, sb, range(len(sb) - len(pb) + 1))
    k = max(coarse, key=coarse.get)
    rest = max((v for j, v in coarse.items() if abs(j - k) > 30), default=0.0)
    lo, hi = max(0, 10 * k - 20), min(len(s) - len(p), 10 * k + 20)
    fine = pearson(p, s, range(lo, hi + 1))
    j = max(fine, key=fine.get)
    d = 0.0
    if lo < j < hi and fine[j - 1] - 2 * fine[j] + fine[j + 1] < 0:
        d = 0.5 * (fine[j - 1] - fine[j + 1]) / (fine[j - 1] - 2 * fine[j] + fine[j + 1])
    out.update(shift=round(a - (s0 + (j + d) / RATE), 3), r=round(fine[j], 3), margin=round(coarse[k] - rest, 3))
    return out


def measure(vid, audio, at, max_probes=4):
    """The shift between the scan's audio and the VOD as YouTube serves it now. `at` are scan times in order of
    preference (the best moments first). The first clear match is confirmed at a second point 10 minutes or more
    away when the moments allow one; a probe that doesn't match (silence, or the edge of the scan) is replaced by the
    next moment, up to max_probes fetches."""
    pts = [float(t) for t in at if t is not None]
    if not pts:
        raise SystemExit('no moment to measure the timeline shift at (no moments or windows given)')
    tmp = tempfile.mkdtemp(prefix='timeline_')
    found, ok = [], []
    try:
        for t in pts:
            if len(found) >= max_probes or len(ok) == 2:
                break
            if ok and abs(t - ok[0]['scan_at']) < SECOND_POINT_AFTER:
                continue
            a = max(0.0, t - PROBE_SECONDS / 2)
            probe = fetch_probe(vid, a, a + PROBE_SECONDS, tmp)
            m = dict(locate(probe, audio, a), scan_at=round(t, 2))
            os.remove(probe)
            found.append(m)
            if good(m):
                ok.append(m)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    seen = '; '.join(f'at VOD {hms(m["at"])}: ' + (f'{m["shift"]:+.2f} s, r {m["r"]:.2f}, margin {m["margin"]:.2f}'
                                                    if m['shift'] is not None else 'too little audio')
                     for m in found)
    far = bool(ok) and any(abs(t - ok[0]['scan_at']) >= SECOND_POINT_AFTER for t in pts)
    shifts = [m['shift'] for m in ok]
    if not ok or (far and len(ok) < 2) or max(shifts) - min(shifts) > MAX_SPREAD:
        raise SystemExit(f'{vid}: could not pin down how far the processed VOD has moved from this scan ({seen}). '
                         'Scan the VOD again into a new folder (yt-dlp now fetches the processed audio, so the times '
                         'match it), or pass --timeline-offset SECONDS if you know the shift.')
    return {'seconds': round(sum(shifts) / len(shifts), 2), 'points': found,
            'how': 'VOD time now = scan time + seconds: 60 s of format 139 (yt-dlp --download-sections) '
                   'cross-correlated with audio.m4a',
            'measured': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}


def scan_window(job, plan):
    """A plan job's window in scan time (plan_jobs.py keeps it in scanStart/scanEnd once a shift is applied)."""
    old = float(((plan or {}).get('timeline') or {}).get('offset') or 0.0)
    return float(job.get('scanStart', job['start'] - old)), float(job.get('scanEnd', job['end'] - old))


def scan_mids(plan, jobs=None):
    return [sum(scan_window(j, plan)) / 2 for j in (plan.get('jobs') or [] if jobs is None else jobs)]


def moment_mids(scan_dir):
    p = os.path.join(scan_dir, 'moments.json')
    if not os.path.isfile(p):
        return []
    with open(p) as f:
        d = json.load(f)
    return [(float(m['start']) + float(m['end'])) / 2 for m in (d.get('moments') if isinstance(d, dict) else d) or []]


def rebase_plan(plan, seconds, state):
    """Put every job of a plan.json on VOD time now = scan time + seconds (url ranges, cut windows, titles) and record
    it in plan["timeline"]. Idempotent; jobs already sent to OpusClip are left as they are. Returns how many moved."""
    moved = 0
    for job in plan.get('jobs') or []:
        s0, e0 = scan_window(job, plan)
        if job.get('projectId'):     # already sent: its times stay, but its scan times are kept for the record
            job.setdefault('scanStart', round(s0, 2))
            job.setdefault('scanEnd', round(e0, 2))
            continue
        a, b = round(max(0.0, s0 + seconds), 2), round(e0 + seconds, 2)
        old = f'{hms(job["start"])}-{hms(job["end"])}'
        moved += (a, b) != (job['start'], job['end'])
        job.update(start=a, end=b, scanStart=round(s0, 2), scanEnd=round(e0, 2))
        req = job.get('request') or {}
        cur = req.get('curationPref') or {}
        if job.get('route') == 'url' and 'range' in cur:
            cur['range'] = {'startSec': int(math.floor(a)), 'endSec': int(math.ceil(b))}
        if job.get('cut'):
            job['cut'].update(start=a, end=b)
        attr = req.get('uploadedVideoAttr') or {}
        if str(attr.get('title', '')).endswith(old):
            attr['title'] = attr['title'][:-len(old)] + f'{hms(a)}-{hms(b)}'
    plan['timeline'] = dict(plan.get('timeline') or {}, offset=seconds, state=state)
    return moved


def describe(seconds, state, b):
    if state == 'measured':
        return f'VOD time now = scan time + {seconds:.2f} s (measured; kept in run.json as timeline_offset)'
    if state == 'pending':
        return ('YouTube still serves this VOD as 2-second fragments, the timeline the scan used, so its times are '
                'right for now. Check again once YouTube has processed it.')
    if state == 'given':
        return f'VOD time now = scan time + {seconds:.2f} s (given)'
    if b is None:
        return ('no run.json here, so nothing records which timeline this scan used: if its audio came from ytfrag.py '
                'and YouTube has processed the VOD since, pass --timeline-offset SECONDS to the scripts')
    came = {'processed': 'yt-dlp, from the processed VOD', 'file': 'a local file',
            'other': 'Twitch, Kick or another link'}.get(b, b)
    return f'nothing to shift: this scan\'s audio came from {came}, so its times are the VOD\'s own'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scan_dir', nargs='?', help='vodwatch scan folder (run.json, audio.m4a, moments.json)')
    ap.add_argument('--plan', help='plan.json from plan_jobs.py: move its windows onto the shift (scan folder: the '
                                   "plan's, or the folder it sits in)")
    ap.add_argument('--at', type=float, action='append', help='scan time in seconds to measure around (repeatable; '
                                                              "default: the plan's jobs, else moments.json)")
    a = ap.parse_args(argv)
    plan = None
    if a.plan:
        with open(a.plan) as f:
            plan = json.load(f)
    scan_dir = a.scan_dir or ((plan or {}).get('timeline') or {}).get('scanDir') or (
        os.path.dirname(os.path.abspath(a.plan)) if a.plan else None)
    if not scan_dir:
        ap.error('give a scan folder, or --plan plan.json')
    run = load_run(scan_dir)
    source = (plan or {}).get('source') or ((run or {}).get('source') or {}).get('url')
    if not source:
        ap.error(f'no source: {scan_dir} has no run.json with one, and no --plan was given')
    at = a.at or (scan_mids(plan) if plan else moment_mids(scan_dir))
    seconds, state = resolve(scan_dir, source, at, run=run)
    print(describe(seconds, state, basis(run)))
    if plan is not None:
        moved = rebase_plan(plan, seconds, state)
        with open(a.plan, 'w') as w:
            json.dump(plan, w, indent=2)
        print(f'{a.plan}: timeline state {state}, offset {seconds:.2f} s; {moved} job(s) moved')
    return 0


if __name__ == '__main__':
    sys.exit(main())
