#!/usr/bin/env python3
"""frame_sheet.py: a contact sheet of a moment (a grid of evenly spaced frames) to check it before clipping.

An audio scan can't see the picture. A frame sheet shows in one image whether the window is real
gameplay with the facecam on screen, or a menu, a loading screen, an AFK break, a cutscene, a
spoiler or something you can't post. Claude reads the JPG directly.

  # from a local video (stream time = file time + --offset):
  python3 -I frame_sheet.py --video window.mp4 --out sheet.jpg
  # fetch a 360p, silent window of a VOD first (deleted afterwards), then build the sheet:
  python3 -I frame_sheet.py --source qSyo5d-dVzA --start 4:12:42 --end 4:13:52 --out sheets/01.jpg
  # the top N picks of a rank_moments.py run:
  python3 -I frame_sheet.py --moments scan/moments.json --source qSyo5d-dVzA --top 8 --out-dir scan/sheets

Writes sheet.jpg (cols x rows tiles, read left to right, top to bottom) and sheet.json with the
stream time of every tile. VOD windows come through cut_windows.py, which asks yt-dlp how YouTube
serves the VOD before each window: ytfrag.py while it is still 2-second fragments, yt-dlp
--download-sections (formats 134/133) once YouTube has processed it.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import cut_windows  # noqa: E402


def hms(sec):
    sec = int(round(sec))
    return f'{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return None


def sheet(video, out, start=0.0, length=None, cols=4, rows=3, width=480, offset=0.0):
    """Tile cols*rows frames spread evenly over [start, start+length) of the file."""
    total = duration(video) or 0.0
    length = length or max(0.1, total - start)
    n = cols * rows
    step = length / n
    os.makedirs(os.path.dirname(os.path.abspath(out)) or '.', exist_ok=True)
    tmp = tempfile.mkdtemp(prefix='sheet_')
    try:
        times = []
        for i in range(n):
            t = start + step * (i + 0.5)
            frame = os.path.join(tmp, f'{i:03d}.png')
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', f'{t:.3f}', '-i', video, '-frames:v', '1',
                            '-vf', f'scale={width}:-2', frame], check=True)
            if not os.path.exists(frame):   # past the end: repeat the last frame we have
                prev = os.path.join(tmp, f'{i - 1:03d}.png')
                if os.path.exists(prev):
                    shutil.copy(prev, frame)
            times.append(round(t + offset, 2))
        subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-framerate', '1', '-i', os.path.join(tmp, '%03d.png'),
                        '-vf', f'tile={cols}x{rows}:padding=6:margin=6:color=0x111111', '-frames:v', '1', '-q:v', '3',
                        out], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    meta = {'sheet': out, 'video': video, 'cols': cols, 'rows': rows, 'frames': [
        {'tile': i + 1, 'row': i // cols + 1, 'col': i % cols + 1, 'stream_time': t, 'stream_hms': hms(t)}
        for i, t in enumerate(times)]}
    with open(os.path.splitext(out)[0] + '.json', 'w') as w:
        json.dump(meta, w, indent=2)
    return meta


def sheet_from_source(source, start, end, out, cols=4, rows=3, width=480, keep=False):
    tmpdir = tempfile.mkdtemp(prefix='win_')
    win = os.path.join(tmpdir, 'window.mp4')
    try:
        if os.path.isfile(source):
            cut_windows.cut_local(source, start, end, win, audio=False)
        else:
            cut_windows.cut(source, start, end, win, height=360, audio=False, fix_fps=False)
        meta = sheet(win, out, 0.0, end - start, cols, rows, width, offset=start)
        if keep:
            kept = os.path.splitext(out)[0] + '.mp4'
            shutil.move(win, kept)
            meta['window'] = kept
        return meta
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--video', help='local video file')
    ap.add_argument('--source', help='VOD (YouTube id/URL, Twitch/Kick URL) or local file to fetch the window from')
    ap.add_argument('--start', help='seconds or h:mm:ss')
    ap.add_argument('--end', help='seconds or h:mm:ss')
    ap.add_argument('--moments', help='moments.json from rank_moments.py')
    ap.add_argument('--top', type=int, default=8)
    ap.add_argument('--out', default='sheet.jpg')
    ap.add_argument('--out-dir', default='sheets')
    ap.add_argument('--cols', type=int, default=4)
    ap.add_argument('--rows', type=int, default=3)
    ap.add_argument('--width', type=int, default=480)
    ap.add_argument('--offset', type=float, default=0.0, help='stream time of the first frame of --video')
    ap.add_argument('--keep-window', action='store_true', help='keep the downloaded 360p window next to the sheet')
    a = ap.parse_args(argv)
    if not shutil.which('ffmpeg'):
        print('error: ffmpeg is not installed', file=sys.stderr)
        return 2
    if a.moments:
        d = json.load(open(a.moments))
        src = a.source or (d.get('source') or {}).get('url') or (d.get('source') or {}).get('video_id')
        if not src:
            ap.error('--source is required (moments.json has no source)')
        os.makedirs(a.out_dir, exist_ok=True)
        for m in d.get('moments', [])[:a.top]:
            out = os.path.join(a.out_dir, f'{m["rank"]:02d}_{m["start_hms"].replace(":", "-")}.jpg')
            meta = sheet_from_source(src, float(m['start']), float(m['end']), out, a.cols, a.rows, a.width, a.keep_window)
            print(f'#{m["rank"]} {m["start_hms"]}-{m["end_hms"]} -> {meta["sheet"]}')
        return 0
    if a.video:
        start = cut_windows.parse_time(a.start) if a.start else 0.0
        length = (cut_windows.parse_time(a.end) - start) if a.end else None
        meta = sheet(a.video, a.out, start, length, a.cols, a.rows, a.width, a.offset)
    elif a.source and a.start and a.end:
        meta = sheet_from_source(a.source, cut_windows.parse_time(a.start), cut_windows.parse_time(a.end), a.out,
                                 a.cols, a.rows, a.width, a.keep_window)
    else:
        ap.error('give --video, or --source with --start/--end, or --moments')
    print(f'wrote {meta["sheet"]} ({a.cols}x{a.rows}, {meta["frames"][0]["stream_hms"]} to {meta["frames"][-1]["stream_hms"]})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
