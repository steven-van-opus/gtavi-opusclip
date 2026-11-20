#!/usr/bin/env python3
"""vodwatch.py: watch an entire stream VOD on your own machine and hand the best moments to OpusClip.

  python3 -I vodwatch.py run "https://www.youtube.com/watch?v=VOD_ID" --clips 8 --out scans/speed
  python3 -I vodwatch.py run "https://www.twitch.tv/videos/123456789" --clips 5 --credit "Kai Cenat (Twitch)"
  python3 -I vodwatch.py run ~/Movies/stream.mp4 --clips 6
  python3 -I vodwatch.py rank scans/speed --clips 10      # re-rank an existing scan
  python3 -I vodwatch.py report scans/speed

What "run" does (every step resumes if you run it again):
  1. resolve   title, channel, length, live status (yt-dlp --print, never -J on a 24 h VOD)
  2. audio     the audio track only. For YouTube it first asks yt-dlp how the VOD is served (asked again on
               every run, since YouTube processes a VOD some time after the stream): ytfrag.py while it is
               still 2-second fragments, yt-dlp -f 139/ba/w once it is processed. Twitch, Kick and other
               links: yt-dlp -f ba/w (never full-quality video). Local files: ffmpeg. Then mono 16 kHz.
  3. scan      loudness per second + whisper.cpp transcript in 20-minute chunks (scan_vod.py)
  4. rank      every 30 s window scored, best moments picked with reasons and brand-safety flags
  5. sheets    a 360p frame sheet per top moment, to check the picture (frame_sheet.py). A scan of
               2-second fragments keeps their timeline, and a processed VOD can play it later
               (IShowSpeed's launch VOD: 144 s): timeline.py measures that shift once and keeps it in
               run.json, and sheets, cuts and the plan add it.
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
import timeline  # noqa: E402
from plan_jobs import source_kind, youtube_id  # noqa: E402
from ytfrag import EXIT_NOT_FRAGMENTED, FRAGMENTED  # noqa: E402

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
    """How YouTube serves the audio a scan downloads: 'http_dash_segments' while the VOD is still 2-second
    fragments (ytfrag.py), 'https' once YouTube has processed it into whole files (yt-dlp). Read-only
    (yt-dlp --print). Asked before every audio download: an answer from an earlier run can be stale."""
    url = f'https://www.youtube.com/watch?v={vid}'
    try:
        out = subprocess.run(['yt-dlp', '--no-warnings', '--no-playlist', '--skip-download', '-f', f'{fmt}/ba',
                              '--print', 'protocol', url], capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        raise SystemExit(f'yt-dlp did not say how {url} is served within 300 s; run the same command again')
    lines = out.stdout.strip().splitlines()
    if out.returncode != 0 or not lines:
        err = (out.stderr.strip().splitlines() or ['no output'])[-1]
        raise SystemExit(f'yt-dlp could not read the audio formats of {url}: {err}')
    return lines[-1].strip()


def audio_selector(meta, fmt='139'):
    """yt-dlp -f for the scan's audio: the audio-only format when the site has one, else the smallest full
    rendition, never full-quality video. On YouTube, format `fmt` (139 = AAC 48k) comes first."""
    return f'{fmt}/ba/w' if meta['kind'] == 'youtube' else 'ba/w'


def audio_line(info):
    """How the audio track was fetched, for report.md (None when this folder has no record of it)."""
    via = (info or {}).get('via')
    if via == 'ytfrag.py':
        return f'ytfrag.py track {info.get("format")} (YouTube still served the VOD as 2-second fragments)'
    if via == 'yt-dlp':
        p = info.get('protocol')
        return f'yt-dlp -f {info.get("format")}' + (f' (processed YouTube VOD, protocol {p})' if p else '')
    if via == 'ffmpeg':
        return 'ffmpeg (local file)'
    return None


def timeline_line(run, plan=None):
    """report.md's "- Timeline:" line: whether this scan's times still match the VOD as YouTube serves it now."""
    rec = run.get('timeline_offset')
    if isinstance(rec, dict) and rec.get('seconds') is not None:
        return (f'VOD time = scan time + {float(rec["seconds"]):.2f} s. YouTube processed the VOD after this scan of its '
                '2-second fragments (measured, kept in run.json); frame sheets, cuts and plan.json use the VOD\'s '
                'times, moments.md keeps the scan\'s.')
    if timeline.basis(run) in timeline.SCAN_FROM_FRAGMENTS and (run.get('source') or {}).get('kind') == 'youtube':
        return ('scanned from 2-second fragments. Once YouTube processes the VOD, the same moments can play later; '
                'frame_sheet.py --moments and cut_windows.py --plan check that before they fetch anything'
                + (', and url jobs wait for timeline.py --plan' if plan and plan.get('route') == 'url' else '') + '.')
    return None


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


FRAGMENT_SCAN_FILES = ('transcript.jsonl', 'loudness.tsv')


def set_aside_fragment_scan(out):
    """Before a scan starts over on a processed VOD's audio: move transcript and loudness rows written in the
    fragment timeline (scan_frags.py during an interrupted ytfrag.py download, or an earlier audio.m4a) out of the
    way, so scan_vod.py doesn't resume on them and mix two timelines. They are renamed, not deleted."""
    moved = []
    for name in FRAGMENT_SCAN_FILES:
        p = os.path.join(out, name)
        if os.path.exists(p):
            stem, ext = os.path.splitext(name)
            os.replace(p, os.path.join(out, f'{stem}.fragment-timeline{ext}'))
            moved.append(name)
    if moved:
        print(f'set aside {" and ".join(moved)} (made from the 2-second fragments, whose timeline the processed VOD '
              f'does not keep) as *.fragment-timeline.*; the scan starts over on the processed audio', file=sys.stderr)
    return moved


def get_audio(meta, out, jobs=16, fmt='139', info=None):
    """OUT/audio.m4a, the mono 16 kHz track the scan reads. `info` (run.json's "audio") records how it was fetched.
    A YouTube VOD's protocol is asked right here, before the download, every time this has to download."""
    info = {} if info is None else info
    before = dict(info)     # how the folder's earlier audio (if any) was fetched
    dest = os.path.join(out, 'audio.m4a')
    if os.path.exists(dest) and os.path.getsize(dest) > 0:
        print(f'audio exists: {dest}', file=sys.stderr)
        return dest
    if meta['kind'] == 'file':
        info.clear()
        info.update(via='ffmpeg')
        return to_compact_audio(meta['url'], dest)
    sel = audio_selector(meta, fmt)
    info.clear()
    if meta['kind'] == 'youtube':
        proto = youtube_protocol(meta['id'], fmt)
        if proto == FRAGMENTED:
            print(f'{meta["id"]}: still served as 2-second fragments: ytfrag.py track {fmt}', file=sys.stderr)
            info.update(protocol=proto, via='ytfrag.py', format=fmt)
            # ytfrag.py assembles into audio.m4a.part and renames it only when it is complete. It asks yt-dlp for
            # the protocol once more before its first request and stops (exit 3) if the VOD was processed since.
            try:
                sh(PY + [os.path.join(HERE, 'ytfrag.py'), 'track', meta['id'], fmt, dest, '--jobs', str(jobs)])
                return dest
            except subprocess.CalledProcessError as e:
                if e.returncode != EXIT_NOT_FRAGMENTED:
                    raise
            proto = youtube_protocol(meta['id'], fmt)   # processed in the meantime: ask again, then yt-dlp
            if proto == FRAGMENTED:
                raise SystemExit(f'ytfrag.py got a whole file for a fragment of {meta["id"]}, but yt-dlp still says '
                                 f'{FRAGMENTED}: YouTube is switching this VOD over; run the same command again later')
            info.clear()
        info['protocol'] = proto
        print(f'{meta["id"]}: processed VOD (protocol {proto}): yt-dlp -f {sel}', file=sys.stderr)
        stale = os.path.abspath(dest + '.raw') + f'.frags{fmt}'
        if os.path.isdir(stale):
            print(f'note: {stale} holds fragments from a run made while the VOD was still served as fragments; '
                  'they cannot be resumed now, so delete that folder to free the space', file=sys.stderr)
        if not (before.get('via') == 'yt-dlp' and before.get('protocol')):   # rows from another timeline
            set_aside_fragment_scan(out)
    info.update(via='yt-dlp', format=sel)
    raw = os.path.join(out, 'audio_src')
    sh(['yt-dlp', '--no-warnings', '--no-playlist', '-f', sel, '-o', raw + '.%(ext)s', meta['url']])
    got = sorted(f for f in os.listdir(out) if f.startswith('audio_src.')
                 and not f.endswith(('.part', '.ytdl')) and '.part-Frag' not in f)
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
    old = os.path.join(out, 'audio.m4a')
    had_audio = os.path.isfile(old) and os.path.getsize(old) > 0
    audio = stage(run, out, 'audio', lambda: get_audio(meta, out, a.jobs, a.audio_format, run.setdefault('audio', {})))
    if not had_audio and run.pop('timeline_offset', None) is not None:   # measured against audio that is gone
        save_run(out, run)
    scan = PY + [os.path.join(HERE, 'scan_vod.py'), audio, out, '--chunk', str(a.chunk), '--threads', str(a.threads)]
    if a.model:
        scan += ['--model', a.model]
    if transcript:
        scan.append('--skip-whisper')
    stage(run, out, 'scan', lambda: sh(scan))
    rank_args(a, out, run, transcript)
    shift, mids = 0.0, timeline.moment_mids(out)
    if meta['kind'] == 'youtube' and mids and (a.sheets or not a.no_plan):
        # a fragment scan of a VOD YouTube has processed since: measure the shift once (kept in run.json), so the
        # sheets fetch the right windows and plan_jobs.py moves the plan onto the VOD's timeline
        shift, _ = timeline.resolve(out, meta['url'], mids, run=run)
        save_run(out, run)
    if a.sheets:
        src = meta['url'] if meta['kind'] != 'file' else meta['url']
        try:
            stage(run, out, 'sheets', lambda: sh(PY + [os.path.join(HERE, 'frame_sheet.py'), '--moments',
                                                      os.path.join(out, 'moments.json'), '--source', src, '--top',
                                                      str(a.sheets), '--out-dir', os.path.join(out, 'sheets'),
                                                      '--timeline-offset', f'{shift:.2f}']))
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
    au = audio_line(run.get('audio'))
    if au:
        lines.insert(4, f'- Audio: {au}')   # right after the credit line
    tl = timeline_line(run, plan)
    if tl:
        lines.insert(5 if au else 4, f'- Timeline: {tl}')
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
        elif (plan.get('timeline') or {}).get('state') in ('unchecked', 'pending'):
            lines.append(f'python3 -I "{HERE}/timeline.py" --plan "{out}/plan.json"   # once YouTube has processed the VOD')
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
    r.add_argument('--jobs', type=int, default=16,
                   help='parallel fragment downloads (YouTube VODs still served as 2-second fragments)')
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
