#!/usr/bin/env python3
"""countdown.py: package OpusClip clips into a ~60 s "5 craziest moments" countdown (1080x1920).

OpusClip does the clipping, the 9:16 reframing and the captions. This script does the packaging:
a cold open (the first seconds of #1 under the title), then #5 down to #1, each cut to the in/out
you chose, with a number header and the streamer credit on screen, loudness-matched and joined.

  python3 -I countdown.py --title "5 CRAZIEST GTA 6 STREAM MOMENTS" \\
      --item "clips/03.mp4|SPEED VS THE ALLIGATOR|IShowSpeed (YouTube)|0:02|0:13" \\
      --item "..." (one --item per entry, #5 first, #1 last) --out countdown.mp4
  add --dry-run to print the plan and every ffmpeg command without rendering anything.

--item is "file|label|credit|in|out"; in/out are optional (seconds or m:ss). Headers are drawn with
render.mjs (Chrome via puppeteer-core; set RENDER_DEPS), then overlaid by ffmpeg, so ffmpeg needs no
drawtext. Outputs: the MP4, plus <out>.json with the order, timings and the credit lines to paste.
"""
import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
W, H = 1080, 1920

OVERLAY = """<!doctype html><html><head><meta charset="utf-8"><style>
html,body{margin:0;width:1080px;height:1920px;background:transparent;overflow:hidden}
body{font-family:Inter,"Avenir Next","Helvetica Neue",Arial,sans-serif}
.hdr{position:absolute;left:0;right:0;top:__TOP__px;display:flex;flex-direction:column;align-items:center;gap:14px}
.num{font-weight:900;font-size:120px;line-height:1;color:#fff;padding:10px 38px 16px;border-radius:28px;
 background:linear-gradient(135deg,#ff3d8b,#ff8a3d);box-shadow:0 10px 0 rgba(0,0,0,.35);transform:rotate(-3deg)}
.label{max-width:960px;text-align:center;font-weight:900;font-size:66px;line-height:1.05;color:#fff;text-transform:uppercase;
 -webkit-text-stroke:10px #000;paint-order:stroke fill;letter-spacing:.5px}
.title{max-width:980px;text-align:center;font-weight:900;font-size:92px;line-height:1.02;color:#fff;text-transform:uppercase;
 -webkit-text-stroke:12px #000;paint-order:stroke fill}
.title em{font-style:normal;color:#ffd23d}
.credit{position:absolute;left:0;right:0;bottom:70px;text-align:center;font-weight:800;font-size:38px;color:#fff;
 -webkit-text-stroke:7px #000;paint-order:stroke fill}
</style></head><body>__BODY__</body></html>"""


def parse_time(v):
    if v in (None, ''):
        return None
    s = str(v).strip()
    if ':' not in s:
        return float(s)
    t = 0.0
    for p in s.split(':'):
        t = t * 60 + float(p)
    return t


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration:stream=codec_type', '-of', 'json', path],
                         capture_output=True, text=True).stdout
    d = json.loads(out or '{}')
    dur = float(d.get('format', {}).get('duration') or 0)
    has_audio = any(s.get('codec_type') == 'audio' for s in d.get('streams', []))
    return dur, has_audio


def parse_item(spec, n):
    parts = spec.split('|')
    if len(parts) < 3:
        raise SystemExit(f'--item needs at least "file|label|credit": {spec!r}')
    f, label, credit = parts[0].strip(), parts[1].strip(), parts[2].strip()
    a = parse_time(parts[3]) if len(parts) > 3 else None
    b = parse_time(parts[4]) if len(parts) > 4 else None
    return {'number': n, 'file': f, 'label': label, 'credit': credit, 'in': a, 'out': b}


def overlay_html(top, number=None, label=None, credit=None, title=None):
    body = '<div class="hdr">'
    if title:
        words = html.escape(title).split()
        # colour the number word(s) for emphasis
        body += '<div class="title">' + ' '.join(f'<em>{w}</em>' if any(c.isdigit() for c in w) else w for w in words) + '</div>'
    if number is not None:
        body += f'<div class="num">#{number}</div>'
    if label:
        body += f'<div class="label">{html.escape(label)}</div>'
    body += '</div>'
    if credit:
        c = credit if credit.startswith('🎥') else '🎥 ' + credit
        body += f'<div class="credit">{html.escape(c)}</div>'
    return OVERLAY.replace('__TOP__', str(int(top))).replace('__BODY__', body)


def render_png(html_path, png_path):
    cmd = ['node', os.path.join(HERE, 'render.mjs'), 'still', '--html', html_path, '--out', png_path,
           '--width', str(W), '--height', str(H), '--transparent']
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)


def segment_cmd(src, a, b, png, out, has_audio, fps):
    cmd = ['ffmpeg', '-loglevel', 'error', '-y']
    if a is not None:
        cmd += ['-ss', f'{a:.3f}']
    if b is not None:
        cmd += ['-to', f'{b:.3f}']
    cmd += ['-i', src, '-loop', '1', '-i', png]
    if not has_audio:
        cmd += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
    v = (f'[0:v]scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,'
         f'setsar=1,fps={fps}[base];[base][1:v]overlay=0:0:shortest=1:format=auto[v]')
    au = '[0:a]' if has_audio else '[2:a]'
    fc = v + f';{au}loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000[a]'
    cmd += ['-filter_complex', fc, '-map', '[v]', '-map', '[a]', '-shortest', '-c:v', 'libx264', '-preset', 'veryfast',
            '-crf', '18', '-pix_fmt', 'yuv420p', '-r', str(fps), '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2', out]
    return cmd


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--item', action='append', required=True, help='"file|label|credit|in|out", #N first, #1 last')
    ap.add_argument('--title', default='5 CRAZIEST GTA 6 STREAM MOMENTS')
    ap.add_argument('--cold-open', type=float, default=2.5, help='seconds of #1 shown first under the title (0 = none)')
    ap.add_argument('--max-item', type=float, default=12.0, help='trim items without an out point to this many seconds')
    ap.add_argument('--header-y', type=float, default=0.27, help='header position as a fraction of the height '
                                                                 '(0.27 sits on the facecam/gameplay seam of a Gameplay layout)')
    ap.add_argument('--fps', type=int, default=30)
    ap.add_argument('--out', default='countdown.mp4')
    ap.add_argument('--keep-temp', action='store_true')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    n = len(a.item)
    items = [parse_item(spec, n - i) for i, spec in enumerate(a.item)]
    for it in items:
        if not a.dry_run and not os.path.isfile(it['file']):
            raise SystemExit(f'no such clip: {it["file"]}')
        dur, has_audio = probe(it['file']) if os.path.isfile(it['file']) else (a.max_item, True)
        it['has_audio'] = has_audio
        it['in'] = it['in'] or 0.0
        it['out'] = it['out'] if it['out'] is not None else min(dur, it['in'] + a.max_item)
        if it['out'] <= it['in']:
            raise SystemExit(f'item #{it["number"]}: out point must be after in point')
    tmp = tempfile.mkdtemp(prefix='countdown_')
    top = int(H * a.header_y) - 150
    segs, cmds, plan = [], [], []
    try:
        jobs = []
        if a.cold_open > 0:
            one = items[-1]
            jobs.append(('cold', one['file'], one['in'], min(one['out'], one['in'] + a.cold_open),
                         overlay_html(top, title=a.title, credit=one['credit']), one['has_audio']))
        for it in items:
            jobs.append((f'n{it["number"]}', it['file'], it['in'], it['out'],
                         overlay_html(top, number=it['number'], label=it['label'], credit=it['credit']), it['has_audio']))
        t = 0.0
        for name, src, s, e, page, has_audio in jobs:
            hp, png, seg = (os.path.join(tmp, f'{name}.html'), os.path.join(tmp, f'{name}.png'),
                            os.path.join(tmp, f'{name}.mp4'))
            with open(hp, 'w') as w:
                w.write(page)
            cmd = segment_cmd(src, s, e, png, seg, has_audio, a.fps)
            cmds.append(cmd)
            plan.append({'segment': name, 'file': src, 'in': round(s, 2), 'out': round(e, 2), 'starts_at': round(t, 2)})
            t += e - s
            if not a.dry_run:
                render_png(hp, png)
                subprocess.run(cmd, check=True)
            segs.append(seg)
        lst = os.path.join(tmp, 'list.txt')
        with open(lst, 'w') as w:
            for s in segs:
                w.write(f"file '{s}'\n")
        concat = ['ffmpeg', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy',
                  '-movflags', '+faststart', a.out]
        cmds.append(concat)
        credits = []
        for it in items:
            c = it['credit'] if it['credit'].startswith('🎥') else '🎥 ' + it['credit']
            if c not in credits:
                credits.append(c)
        meta = {'out': a.out, 'duration_s': round(t, 2), 'title': a.title, 'segments': plan,
                'credits': credits, 'note': 'Clips by OpusClip; countdown packaging by countdown.py.'}
        if a.dry_run:
            for c in cmds:
                print(' '.join(f'"{x}"' if (' ' in x or ';' in x) else x for x in c))
            print(json.dumps(meta, indent=2, ensure_ascii=False))
            return 0
        os.makedirs(os.path.dirname(os.path.abspath(a.out)) or '.', exist_ok=True)
        subprocess.run(concat, check=True)
        with open(os.path.splitext(a.out)[0] + '.json', 'w') as w:
            json.dump(meta, w, indent=2, ensure_ascii=False)
        print(f'wrote {a.out} ({t:.1f}s, {len(segs)} segments). Credits for the caption: {" / ".join(credits)}')
        return 0
    finally:
        if a.keep_temp:
            print(f'temp files kept in {tmp}', file=sys.stderr)
        else:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())
