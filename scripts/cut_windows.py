#!/usr/bin/env python3
"""cut_windows.py: cut exact time windows out of a stream VOD (or a local file), ready to upload.

  # one window from a YouTube VOD at 1080p (times as the VOD plays now):
  python3 -I cut_windows.py --source qSyo5d-dVzA --start 4:13:54 --end 4:16:54 --out alligator.mp4
  # the same window from a scan's times, for a VOD that plays them 144 s later now (see timeline.py):
  python3 -I cut_windows.py --source qSyo5d-dVzA --start 4:11:30 --end 4:14:30 --timeline-offset 144 --out alligator.mp4
  # every "upload" job in a plan written by plan_jobs.py (writes job["file"] back into the plan):
  python3 -I cut_windows.py --plan plan.json

How each source is cut:
  YouTube VOD still served  ytfrag.py window (right after a stream YouTube often serves the VOD as
  as 2-second fragments     2-second fragments, which breaks yt-dlp --download-sections; ytfrag fetches
                            only the fragments it needs)
  processed YouTube VOD,    yt-dlp --download-sections "*START-END" --force-keyframes-at-cuts (YouTube:
  Twitch, Kick, other links the same H.264 + AAC renditions ytfrag.py would use first, e.g. 299+140)
  local files               ffmpeg -ss/-to, re-encoded so the cut is frame-exact
For YouTube the route is decided again before every cut, from yt-dlp --print protocol
(http_dash_segments = fragments, https = processed): YouTube processes a VOD into whole files some time
after the stream, and from then on answers every fragment request with the whole file. ytfrag.py checks
once more before its first request; if it finds the VOD processed (exit 3), the cut goes to yt-dlp.
A processed VOD can play a fragment scan's times later (IShowSpeed's launch VOD: 144 s), so --plan
first moves the plan onto the VOD as it is served now: timeline.py measures the shift once from the
scan folder's audio.m4a and keeps it in run.json (a weak match stops; --timeline-offset sets it).
Then the frame rate is checked: OpusClip local uploads need an integer frame rate, so a 59.94 or
29.97 fps cut is re-encoded to 60 or 30 fps.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
from ytfrag import EXIT_NOT_FRAGMENTED, FRAGMENTED  # noqa: E402

YTFRAG = os.path.join(HERE, 'ytfrag.py')
VIDEO_LADDER = {1080: ['299', '137', '298', '136'], 720: ['298', '136', '135'], 480: ['135', '134'], 360: ['134', '133']}
AUDIO_FORMAT = '140'  # YouTube AAC 128k: the audio ytfrag.py muxes into a window, and yt-dlp's first choice too


def parse_time(v):
    s = str(v).strip()
    if ':' not in s:
        return float(s)
    t = 0.0
    for p in s.split(':'):
        t = t * 60 + float(p)
    return t


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


def run(cmd, **kw):
    print('+ ' + ' '.join(cmd if len(' '.join(cmd)) < 300 else cmd[:6] + ['...']), file=sys.stderr, flush=True)
    return subprocess.run(cmd, check=True, **kw)


def probe_fps(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=r_frame_rate',
                          '-of', 'csv=p=0', path], capture_output=True, text=True).stdout.strip()
    return out or None


def fps_value(fps):
    if not fps:
        return None
    if '/' in fps:
        n, d = fps.split('/')
        return float(n) / float(d) if float(d) else None
    return float(fps)


def normalize_fps(path):
    """Re-encode to the nearest integer frame rate when needed. Returns the fps string after."""
    fps = probe_fps(path)
    v = fps_value(fps)
    if v is None or abs(v - round(v)) < 1e-6:
        return fps
    target = int(round(v))
    tmp = path + '.fps.mp4'
    run(['ffmpeg', '-loglevel', 'error', '-y', '-i', path, '-r', str(target), '-c:v', 'libx264', '-preset', 'veryfast',
         '-crf', '17', '-pix_fmt', 'yuv420p', '-c:a', 'copy', '-movflags', '+faststart', tmp])
    os.replace(tmp, path)
    print(f'normalized frame rate {fps} -> {target} fps (OpusClip uploads need an integer frame rate)', file=sys.stderr)
    return f'{target}/1'


def yt_protocol(vid, fmt):
    """yt-dlp's delivery protocol for one YouTube format ('http_dash_segments' while the VOD is still served as
    2-second fragments, 'https' once processed), or None when yt-dlp can't read that format (it may not exist
    for this VOD). Read-only (yt-dlp --print), and asked again for every cut."""
    try:
        out = subprocess.run(['yt-dlp', '--no-warnings', '--no-playlist', '--skip-download', '-f', fmt,
                              '--print', 'protocol', f'https://www.youtube.com/watch?v={vid}'],
                             capture_output=True, text=True, timeout=120)
        lines = out.stdout.strip().splitlines()
        return lines[-1].strip() if out.returncode == 0 and lines else None
    except Exception:
        return None


def cut_youtube(vid, start, end, out, height=1080, audio=True, method='auto'):
    """ytfrag.py while YouTube still serves the VOD as 2-second fragments, yt-dlp once it is processed."""
    ladder = VIDEO_LADDER.get(height, VIDEO_LADDER[1080])
    if method in ('auto', 'ytfrag'):
        for fmt in ladder:
            proto = yt_protocol(vid, fmt) if method == 'auto' else FRAGMENTED
            if proto is None:
                continue    # no such rendition for this VOD: try the next one
            if proto != FRAGMENTED:
                print(f'{vid}: processed VOD (protocol {proto}): cutting with yt-dlp --download-sections', file=sys.stderr)
                break
            cmd = [sys.executable, '-I', YTFRAG, 'window', vid, str(start), str(end), out, '--video', fmt,
                   '--audio', AUDIO_FORMAT if audio else 'none']
            try:
                run(cmd)
                return 'ytfrag', fmt
            except subprocess.CalledProcessError as e:
                if e.returncode == EXIT_NOT_FRAGMENTED:   # processed since the check: every format is, so stop here
                    print(f'{vid}: ytfrag.py found a processed VOD: cutting with yt-dlp --download-sections',
                          file=sys.stderr)
                    break
                continue
    return cut_ytdlp(f'https://www.youtube.com/watch?v={vid}', start, end, out, height, audio, ladder), None


def ytdlp_format(height=1080, audio=True, ladder=None):
    """yt-dlp -f for a window. With a YouTube ladder, its H.264 renditions (with AAC 140) come first, the files
    ytfrag.py would have fetched; then any rendition up to `height`."""
    any_fmt = f'bv*[height<={height}]+ba/b[height<={height}]' if audio else f'bv*[height<={height}]/b[height<={height}]'
    if not ladder:
        return any_fmt
    return '/'.join([f'{v}+{AUDIO_FORMAT}' if audio else v for v in ladder] + [any_fmt])


def cut_ytdlp(url, start, end, out, height=1080, audio=True, ladder=None):
    fmt = ytdlp_format(height, audio, ladder)
    base = os.path.splitext(out)[0]
    run(['yt-dlp', '--no-warnings', '--no-playlist', '-f', fmt, '--download-sections', f'*{start:.2f}-{end:.2f}',
         '--force-keyframes-at-cuts', '--merge-output-format', 'mp4', '-o', base + '.%(ext)s', url])
    if not os.path.exists(out):
        cands = [p for p in os.listdir(os.path.dirname(os.path.abspath(out))) if p.startswith(os.path.basename(base) + '.')
                 and not p.endswith(('.part', '.ytdl')) and '.part-Frag' not in p]
        if not cands:
            raise SystemExit(f'yt-dlp produced no file for {url}')
        os.replace(os.path.join(os.path.dirname(os.path.abspath(out)), cands[0]), out)
    return 'yt-dlp'


def cut_local(path, start, end, out, audio=True):
    cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-ss', f'{start:.3f}', '-to', f'{end:.3f}', '-i', path,
           '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '17', '-pix_fmt', 'yuv420p']
    cmd += ['-c:a', 'aac', '-b:a', '192k'] if audio else ['-an']
    run(cmd + ['-movflags', '+faststart', out])
    return 'ffmpeg'


def cut(source, start, end, out, height=1080, audio=True, method='auto', fix_fps=True):
    if end <= start:
        raise SystemExit(f'bad window {start}-{end}')
    os.makedirs(os.path.dirname(os.path.abspath(out)) or '.', exist_ok=True)
    if os.path.isfile(source):
        how = cut_local(source, start, end, out, audio)
    elif youtube_id(source) and method != 'ytdlp':
        how, _ = cut_youtube(youtube_id(source), start, end, out, height, audio, method)
    else:
        ladder = VIDEO_LADDER.get(height, VIDEO_LADDER[1080]) if youtube_id(source) else None
        how = cut_ytdlp(source if '://' in source else f'https://www.youtube.com/watch?v={source}', start, end, out,
                        height, audio, ladder)
    fps = normalize_fps(out) if fix_fps and shutil.which('ffprobe') else probe_fps(out)
    return {'file': out, 'method': how, 'fps': fps, 'start': start, 'end': end}


def align_plan(plan, path, explicit=None):
    """Before any cut: put the plan's windows on the VOD as YouTube serves it now. A scan of a VOD's 2-second
    fragments keeps their timeline, which a processed VOD can play later; timeline.py measures that shift once
    (from the scan folder's run.json and audio.m4a) and the plan is moved onto it and saved."""
    import timeline
    tl = plan.get('timeline') or {}
    if explicit is not None:
        seconds, state = explicit, 'given'
    elif tl.get('state') in timeline.FINAL:
        return
    else:
        todo = [j for j in plan.get('jobs', []) if j.get('route') == 'upload' and j.get('cut')
                and not (j.get('file') and os.path.isfile(j['file']))]
        if not todo or not youtube_id(plan.get('source') or ''):
            return
        scan_dir = tl.get('scanDir') or os.path.dirname(os.path.abspath(path))
        seconds, state = timeline.resolve(scan_dir, plan.get('source'), timeline.scan_mids(plan, todo))
        if state == 'none' and not tl:
            return      # no vodwatch scan behind this plan: nothing to check or record
    moved = timeline.rebase_plan(plan, seconds, state)
    with open(path, 'w') as w:
        json.dump(plan, w, indent=2)
    if moved:
        print(f'{path}: {moved} job(s) moved to VOD time = scan time + {seconds:.2f} s', file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--plan', help='plan.json from plan_jobs.py: cut every job routed "upload"')
    ap.add_argument('--source', help='YouTube id/URL, Twitch/Kick/other URL, or a local file')
    ap.add_argument('--start', help='seconds or h:mm:ss')
    ap.add_argument('--end', help='seconds or h:mm:ss')
    ap.add_argument('--out', help='output .mp4 (single window)')
    ap.add_argument('--height', type=int, default=1080, choices=sorted(VIDEO_LADDER))
    ap.add_argument('--no-audio', action='store_true')
    ap.add_argument('--method', default='auto', choices=('auto', 'ytfrag', 'ytdlp'),
                    help='force the YouTube method (auto asks yt-dlp before every cut whether the VOD is still served '
                         'as 2-second fragments; ytfrag.py refuses a processed VOD, which then goes to yt-dlp)')
    ap.add_argument('--keep-fps', action='store_true', help='do not normalize fractional frame rates')
    ap.add_argument('--timeline-offset', type=float, metavar='SECONDS',
                    help='the VOD as served now plays the given times this much later: cut start+SECONDS to '
                         'end+SECONDS. With --plan it replaces the shift timeline.py would measure (0 = none)')
    a = ap.parse_args(argv)
    for tool in ('ffmpeg', 'ffprobe'):
        if not shutil.which(tool):
            print(f'error: {tool} is not installed (brew install ffmpeg)', file=sys.stderr)
            return 2
    if a.plan:
        plan = json.load(open(a.plan))
        align_plan(plan, a.plan, a.timeline_offset)
        done = 0
        for job in plan.get('jobs', []):
            c = job.get('cut')
            if job.get('route') != 'upload' or not c:
                continue
            if job.get('file') and os.path.isfile(job['file']):
                print(f'{job["id"]}: already cut -> {job["file"]}', file=sys.stderr)
                continue
            r = cut(c['source'], float(c['start']), float(c['end']), c['out'], a.height, not a.no_audio, a.method,
                    not a.keep_fps)
            job['file'] = r['file']
            job['fps'] = r['fps']
            done += 1
            with open(a.plan, 'w') as w:
                json.dump(plan, w, indent=2)
        print(f'cut {done} window(s); plan updated: {a.plan}')
        return 0
    if not (a.source and a.start is not None and a.end is not None and a.out):
        ap.error('give --plan, or --source --start --end --out')
    shift = a.timeline_offset or 0.0
    r = cut(a.source, parse_time(a.start) + shift, parse_time(a.end) + shift, a.out, a.height, not a.no_audio, a.method,
            not a.keep_fps)
    if shift:
        r['timeline_offset'] = shift
    print(json.dumps(r))
    return 0


if __name__ == '__main__':
    sys.exit(main())
