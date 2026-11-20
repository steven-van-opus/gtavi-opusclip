#!/usr/bin/env python3
"""vodwatch.py: watch an entire stream VOD on your own machine and hand the best moments to OpusClip.

  python3 -I vodwatch.py run "https://www.youtube.com/watch?v=VOD_ID" --clips 8 --out scans/speed
  python3 -I vodwatch.py run "https://www.twitch.tv/videos/123456789" --clips 5 --credit "Kai Cenat (Twitch)"
  python3 -I vodwatch.py run ~/Movies/stream.mp4 --clips 6
  python3 -I vodwatch.py rank scans/speed --clips 10      # re-rank an existing scan
  python3 -I vodwatch.py report scans/speed

What "run" does (every step resumes if you run it again):
  1. resolve   title, channel, length, live status (yt-dlp --print, never -J on a 24 h VOD)
  2. audio     the audio track only: ytfrag.py for YouTube post-live VODs, yt-dlp or ffmpeg otherwise
  3. scan      loudness per second + whisper.cpp transcript in 20-minute chunks (scan_vod.py)
  4. rank      every 30 s window scored, best moments picked with reasons and brand-safety flags
  5. sheets    a 360p frame sheet per top moment, to check the picture (frame_sheet.py)
  6. plan      OpusClip jobs with a credit estimate (plan_jobs.py): URL + time range for sources up
               to 10 h, local cut + upload for longer ones. Nothing is sent to OpusClip.
  7. report    report.md: hours scanned, moments found, wall time per step, cost of compute ($0, local)
Then: review moments.md + sheets/, and send the plan with opusclip_api.py batch plan.json --yes
(it uploads the local cuts of "upload" jobs itself), or over MCP: opusclip_submit_project with the
job's URL and range for "url" jobs, and opusclip_create_upload_link plus opusclip_submit_project with
the upload id for "upload" jobs (cut them first with cut_windows.py --plan plan.json).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from plan_jobs import source_kind, youtube_id  # noqa: E402

PY = [sys.executable, '-I']


def sh(cmd, **kw):
    print('+ ' + ' '.join(str(c) for c in cmd), file=sys.stderr, flush=True)
    return subprocess.run(cmd, check=True, **kw)


def hms(sec):
    sec = int(round(sec or 0))
    return f'{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def load_run(out):
    p = os.path.join(out, 'run.json')
    return json.load(open(p)) if os.path.exists(p) else {'stages': {}}


def save_run(out, run):
    with open(os.path.join(out, 'run.json'), 'w') as w:
        json.dump(run, w, indent=2)


def stage(run, out, name, fn):
    t0 = time.time()
    res = fn()
    run['stages'][name] = {'seconds': round(time.time() - t0, 1) + run['stages'].get(name, {}).get('seconds', 0.0)}
    save_run(out, run)
    return res


def resolve(src):
    kind = source_kind(src)
    if kind == 'file':
        out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', src],
                             capture_output=True, text=True).stdout.strip()
        return {'kind': 'file', 'url': os.path.abspath(src), 'id': None, 'title': os.path.basename(src), 'channel': '',
                'duration': float(out) if out else None, 'live_status': 'not_live'}
    vid = youtube_id(src)
    url = f'https://www.youtube.com/watch?v={vid}' if vid else src
    fmt = '%(id)s\t%(title)s\t%(channel,uploader)s\t%(duration)s\t%(live_status)s'
    out = subprocess.run(['yt-dlp', '--no-warnings', '--no-playlist', '--skip-download', '--print', fmt, url],
                         capture_output=True, text=True, timeout=300)
    if out.returncode != 0 or not out.stdout.strip():
        raise SystemExit(f'yt-dlp could not read {url}: {out.stderr.strip()[-300:]}')
    vid2, title, channel, dur, live = (out.stdout.strip().splitlines()[-1].split('\t') + [''] * 5)[:5]
    return {'kind': kind, 'url': url, 'id': vid or None, 'title': title, 'channel': channel,
            'duration': float(dur) if re.fullmatch(r'[\d.]+', dur or '') else None, 'live_status': live}


def youtube_protocol(vid, fmt='139'):
    out = subprocess.run(['yt-dlp', '--no-warnings', '-f', fmt, '--print', '%(protocol)s',
                          f'https://www.youtube.com/watch?v={vid}'], capture_output=True, text=True, timeout=300)
    return out.stdout.strip().splitlines()[-1] if out.stdout.strip() else None


def to_compact_audio(src, dest):
    """src -> mono 16 kHz AAC at 32 kbps. ffmpeg writes dest.part, which becomes dest only when ffmpeg
    succeeds, so an interrupted run never leaves a short audio.m4a that the next run would accept."""
    part = dest + '.part'
    try:
        sh(['ffmpeg', '-loglevel', 'error', '-y', '-i', src, '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'aac',
            '-b:a', '32k', '-f', 'ipod', part])
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise
    os.replace(part, dest)
    return dest


def get_audio(meta, out, jobs=16, fmt='139'):
    dest = os.path.join(out, 'audio.m4a')
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f'audio exists: {dest}', file=sys.stderr)
        return dest
    if meta['kind'] == 'file':
        return to_compact_audio(meta['url'], dest)
    if meta['kind'] == 'youtube' and youtube_protocol(meta['id'], fmt) == 'http_dash_segments':
        # ytfrag.py assembles into audio.m4a.part and renames it only when it is complete
        sh(PY + [os.path.join(HERE, 'ytfrag.py'), 'track', meta['id'], fmt, dest, '--jobs', str(jobs)])
        return dest
    raw = os.path.join(out, 'audio_src')
    sh(['yt-dlp', '--no-warnings', '--no-playlist', '-f', 'bestaudio/best', '-o', raw + '.%(ext)s', meta['url']])
    got = sorted(f for f in os.listdir(out) if f.startswith('audio_src.') and not f.endswith(('.part', '.ytdl')))
    if not got:
        raise SystemExit('yt-dlp downloaded nothing')
    src = os.path.join(out, got[0])
    to_compact_audio(src, dest)
    os.remove(src)          # only after the compact track is complete
    return dest


def youtube_captions(meta, out):
    """YouTube auto-captions as a fast path (no punctuation, so whisper ranks better; use when time matters)."""
    base = os.path.join(out, 'captions')
    subprocess.run(['yt-dlp', '--no-warnings', '--skip-download', '--write-auto-subs', '--write-subs', '--sub-langs', 'en.*,en',
                    '--sub-format', 'vtt', '-o', base, meta['url']], capture_output=True, text=True, timeout=600)
    vtts = sorted(f for f in os.listdir(out) if f.startswith('captions') and f.endswith('.vtt'))
    return os.path.join(out, vtts[0]) if vtts else None


def cmd_run(a):
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    run = load_run(out)
    t_all = time.time()
    meta = stage(run, out, 'resolve', lambda: resolve(a.source))
    if meta.get('live_status') == 'is_live':
        raise SystemExit('this stream is still live: scan the VOD once it ends and has finished processing '
                         '(OpusClip cannot take a live stream either)')
    run['source'] = meta
    plat = {'youtube': 'YouTube', 'twitch': 'Twitch', 'kick': 'Kick'}.get(meta['kind'])
    channel = meta.get('channel') or ''
    run['credit'] = a.credit or (f'{channel} ({plat})' if channel and plat else channel)
    save_run(out, run)
    dur = meta.get('duration')
    print(f'{meta["title"]} | {meta["channel"]} | {hms(dur) if dur else "length unknown"}', file=sys.stderr)
    transcript = None
    if a.captions == 'youtube' and meta['kind'] == 'youtube':
        transcript = stage(run, out, 'captions', lambda: youtube_captions(meta, out))
    audio = stage(run, out, 'audio', lambda: get_audio(meta, out, a.jobs, a.audio_format))
    scan = PY + [os.path.join(HERE, 'scan_vod.py'), audio, out, '--chunk', str(a.chunk), '--threads', str(a.threads)]
    if a.model:
        scan += ['--model', a.model]
    if transcript:
        scan.append('--skip-whisper')
    stage(run, out, 'scan', lambda: sh(scan))
    rank_args(a, out, run, transcript)
    if a.sheets:
        src = meta['url'] if meta['kind'] != 'file' else meta['url']
        try:
            stage(run, out, 'sheets', lambda: sh(PY + [os.path.join(HERE, 'frame_sheet.py'), '--moments',
                                                      os.path.join(out, 'moments.json'), '--source', src, '--top',
                                                      str(a.sheets), '--out-dir', os.path.join(out, 'sheets')]))
        except subprocess.CalledProcessError as e:
            print(f'frame sheets failed ({e}); continuing without them', file=sys.stderr)
    if not a.no_plan:
        plan_args(a, out, run, meta)
    run['wall_seconds'] = round(time.time() - t_all, 1) + run.get('wall_seconds', 0.0)
    save_run(out, run)
    write_report(out, run)
    print(open(os.path.join(out, 'report.md')).read())
    return 0


def rank_args(a, out, run, transcript=None):
    meta = run.get('source', {})
    tr = transcript or os.path.join(out, 'transcript.jsonl')
    cmd = PY + [os.path.join(HERE, 'rank_moments.py'), '--transcript', tr, '--out-dir', out, '--top', str(a.candidates)]
    if os.path.exists(os.path.join(out, 'loudness.tsv')):
        cmd += ['--loudness', os.path.join(out, 'loudness.tsv')]
    if meta.get('id'):
        cmd += ['--video-id', meta['id']]
    elif meta.get('url', '').startswith('http'):
        cmd += ['--source-url', meta['url']]
    if run.get('credit'):
        cmd += ['--label', run['credit']]
    if meta.get('duration'):
        cmd += ['--duration', str(meta['duration'])]
    stage(run, out, 'rank', lambda: sh(cmd))


def plan_args(a, out, run, meta):
    cmd = PY + [os.path.join(HERE, 'plan_jobs.py'), os.path.join(out, 'moments.json'), '--source', meta['url'],
                '--top', str(a.clips), '--out', os.path.join(out, 'plan.json'), '--cut-dir', os.path.join(out, 'cuts')]
    if meta.get('duration'):
        cmd += ['--source-duration', str(meta['duration'])]
    if run.get('credit'):
        cmd += ['--credit', run['credit'], '--title-prefix', run['credit'].split(' (')[0]]
    if a.prompt:
        cmd += ['--prompt', a.prompt]
    stage(run, out, 'plan', lambda: sh(cmd))


def write_report(out, run):
    meta = run.get('source', {})
    m = json.load(open(os.path.join(out, 'moments.json'))) if os.path.exists(os.path.join(out, 'moments.json')) else {}
    plan = json.load(open(os.path.join(out, 'plan.json'))) if os.path.exists(os.path.join(out, 'plan.json')) else None
    cov, stats = m.get('coverage', {}), m.get('stats', {})
    lines = [f'# vodwatch report: {meta.get("title", "")}', '',
             f'- Source: {meta.get("url", "")} (' + ', '.join(x for x in (meta.get("channel"), hms(meta.get("duration")) if meta.get("duration") else None) if x) + ')',
             f'- Credit line for every clip: {run.get("credit") or "(add one)"}',
             f'- Hours scanned: {cov.get("hours", "?")}',
             f'- Windows scored: {stats.get("windows_scored", "?")} (30 s, every 10 s)',
             f'- Moments found: {stats.get("strong_moments", "?")} strong ({stats.get("strong_definition", "")})',
             f'- Candidates kept: {len(m.get("moments", []))} (moments.md)',
             f'- Wall time: {hms(run.get("wall_seconds", 0))} total; ' + ', '.join(
                 f'{k} {hms(v["seconds"])}' for k, v in run.get('stages', {}).items()),
             '- Compute cost: $0 (whisper.cpp, ffmpeg and the ranking ran on this machine; no API was called)', '']
    if plan:
        t = plan['totals']
        lines += [f'## OpusClip plan (not sent)', '',
                  f'{t["jobs"]} job(s), {t["minutes"]} min of source, about {t["estimatedCredits"]} credits, route: {plan["route"]}.', '']
        for j in plan['jobs']:
            lines.append(f'- {j["id"]}: {hms(j["start"])}-{hms(j["end"])}, moments {", ".join(map(str, j["moments"]))}, '
                         f'~{j["estimatedCredits"]} credits')
        lines += ['', 'Send it after you have checked moments.md and sheets/:', '',
                  '```bash']
        if plan['route'] == 'upload':
            lines.append(f'python3 -I "{HERE}/cut_windows.py" --plan "{out}/plan.json"')
        lines += [f'python3 -I "{HERE}/opusclip_api.py" batch "{out}/plan.json" --yes --wait', '```', '']
    lines += ['## Top moments', '']
    for x in m.get('moments', [])[:10]:
        lines.append(f'{x["rank"]}. {x["start_hms"]}-{x["end_hms"]} (score {x["score"]}): {"; ".join(x["reasons"])}'
                     + (f'  FLAGS: {" | ".join(x["flags"])}' if x['flags'] else ''))
    with open(os.path.join(out, 'report.md'), 'w') as w:
        w.write('\n'.join(lines) + '\n')


def cmd_rank(a):
    out = os.path.abspath(a.dir)
    run = load_run(out)
    tr = None
    if not os.path.exists(os.path.join(out, 'transcript.jsonl')):
        vtts = [f for f in os.listdir(out) if f.endswith('.vtt')]
        tr = os.path.join(out, vtts[0]) if vtts else None
    rank_args(a, out, run, tr)
    if run.get('source', {}).get('url') and not a.no_plan:
        plan_args(a, out, run, run['source'])
    write_report(out, run)
    print(open(os.path.join(out, 'report.md')).read())
    return 0


def cmd_report(a):
    out = os.path.abspath(a.dir)
    write_report(out, load_run(out))
    print(open(os.path.join(out, 'report.md')).read())
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd')
    r = sub.add_parser('run', help='scan a whole VOD and plan the clips')
    r.add_argument('source', help='YouTube/Twitch/Kick VOD URL, YouTube id, or a local video file')
    r.add_argument('--clips', type=int, default=8, help='how many clips you want (jobs planned for the best N)')
    r.add_argument('--candidates', type=int, default=20, help='moments kept for review (more than --clips)')
    r.add_argument('--out', default='vodwatch_out')
    r.add_argument('--credit', help='credit line, e.g. "IShowSpeed (YouTube)" (default: channel + platform)')
    r.add_argument('--captions', default='whisper', choices=('whisper', 'youtube'),
                   help='whisper (default, best ranking) or YouTube auto-captions when available (much faster)')
    r.add_argument('--model', help='whisper.cpp model path (default: $WHISPER_MODEL or ~/.cache/whisper.cpp)')
    r.add_argument('--threads', type=int, default=8)
    r.add_argument('--chunk', type=int, default=1200)
    r.add_argument('--jobs', type=int, default=16, help='parallel fragment downloads (YouTube post-live VODs)')
    r.add_argument('--audio-format', default='139', help='YouTube audio format id (139 = 48k AAC)')
    r.add_argument('--sheets', type=int, default=8, help='frame sheets for the top N moments (0 = skip)')
    r.add_argument('--prompt', help='ClipAnything prompt for the plan')
    r.add_argument('--no-plan', action='store_true')
    r.set_defaults(fn=cmd_run)
    k = sub.add_parser('rank', help='re-rank an existing scan folder')
    k.add_argument('dir')
    k.add_argument('--clips', type=int, default=8)
    k.add_argument('--candidates', type=int, default=20)
    k.add_argument('--prompt')
    k.add_argument('--no-plan', action='store_true')
    k.set_defaults(fn=cmd_rank)
    p = sub.add_parser('report', help='rewrite report.md for a scan folder')
    p.add_argument('dir')
    p.set_defaults(fn=cmd_report)
    a = ap.parse_args(argv)
    if not getattr(a, 'fn', None):
        ap.print_help()
        return 2
    for tool in ('ffmpeg', 'ffprobe'):
        if not shutil.which(tool):
            print(f'error: {tool} is not installed', file=sys.stderr)
            return 2
    return a.fn(a)


if __name__ == '__main__':
    sys.exit(main())
