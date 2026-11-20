#!/usr/bin/env python3
"""Download time windows (or the whole track) of a YouTube post-live VOD that is still served as
self-initializing 2-second DASH fragments (yt-dlp protocol http_dash_segments), which breaks
yt-dlp --download-sections.

Usage:
  # one window, video+audio muxed, trimmed to exact times (seconds or h:mm:ss[.s]):
  python3 -I ytfrag.py window VIDEO_ID 3:01:05 3:01:40 out.mp4 [--video 299] [--audio 140] [--pad 3]
  # whole audio track (for transcription / loudness scans):
  python3 -I ytfrag.py track VIDEO_ID 139 out.m4a [--jobs 8] [--frags N]

Fragments only last until YouTube processes the VOD into whole files (protocol https; the launch-day
VODs took about a day). From then on the server ignores `&sq=N` and answers every "fragment" request
with the entire file, so this script asks yt-dlp for the protocol of each format first and stops before
its first request on a processed VOD, and a reply over 16 MB (no 2-second fragment is that big) stops
the download after one round. Both exit with status 3. vodwatch.py and cut_windows.py ask yt-dlp before
every download and send processed VODs to yt-dlp themselves; by hand, use yt-dlp -f 139 (add
--download-sections "*START-END" for a window).

Signed fragment URLs come from `yt-dlp -g` and are refreshed on 401/403/410, on slow fragments and
every 1,500 fragments (YouTube throttles a URL after a few thousand). Downloads resume: finished
fragments stay on disk until the file is assembled, in <out>.raw.frags<format>/ for `track` (for
example speed_audio.m4a.raw.frags139/) and in <out>.parts/ for `window`. Both write <out>.part and
rename it to <out> only when ffmpeg succeeds, so an interrupted run never leaves a short <out> behind,
and the fragments are deleted only after that. Never run `yt-dlp -J` on a 24-hour VOD (its info JSON
can be hundreds of MB); this script only asks for the protocol, -g and the duration.
Common formats: video 299 = 1080p60, 137 = 1080p, 298 = 720p60, 134 = 360p; audio 140 = AAC 128k,
139 = AAC 48k (plenty for transcription).
"""
import json, os, subprocess, sys, time, threading, urllib.request, urllib.error, shutil, tempfile
from concurrent.futures import ThreadPoolExecutor

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36'
FRAGMENTED = 'http_dash_segments'   # yt-dlp's protocol while YouTube still serves the VOD as 2-second fragments
MAX_FRAG_BYTES = 16 * 1024 * 1024   # a 2 s fragment is a few MB at most, even at 1080p60: bigger is a whole file
EXIT_NOT_FRAGMENTED = 3             # exit status for a processed VOD or a whole-file reply: callers use yt-dlp


class NotFragmented(SystemExit):
    """The VOD is not (or no longer) served as 2-second fragments, so it has to come through yt-dlp. Raised before
    the first request on a processed VOD, and after one round when a "fragment" came back as a whole file."""


class WholeFileServed(Exception):
    """The server ignored &sq=N and answered with a whole file: stop instead of pulling it once per fragment."""


def hms(v):
    v = str(v)
    if ':' not in v:
        return float(v)
    parts = [float(p) for p in v.split(':')]
    s = 0.0
    for p in parts:
        s = s * 60 + p
    return s


class Source:
    """Signed base URLs come from `yt-dlp -g` (fast). Fragments are `<base>&sq=N`, exactly SEG seconds each
    (2.0 s for these YouTube post-live VODs). Gaps in the stream can shift the mapping, so callers verify
    timestamps after download and adjust."""
    SEG = 2.0

    def __init__(self, vid):
        self.vid = vid
        self.lock = threading.Lock()
        self.urls, self.when, self.protocols = {}, {}, {}

    def protocol(self, fid):
        """yt-dlp's delivery protocol for one format: 'http_dash_segments' while the VOD is still served as
        2-second fragments, 'https' once YouTube has processed it into whole files. Read-only (--print)."""
        if fid not in self.protocols:
            try:
                out = subprocess.run(['yt-dlp', '--no-warnings', '--no-playlist', '--skip-download', '-f', str(fid),
                                      '--print', 'protocol', f'https://www.youtube.com/watch?v={self.vid}'],
                                     capture_output=True, text=True, timeout=300)
            except subprocess.TimeoutExpired:
                raise SystemExit(f'yt-dlp did not say how format {fid} of {self.vid} is served within 300 s')
            lines = out.stdout.strip().splitlines()
            if out.returncode != 0 or not lines:
                raise SystemExit(f'yt-dlp could not read format {fid} of {self.vid}: '
                                 f'{(out.stderr.strip().splitlines() or ["no output"])[-1]}')
            self.protocols[fid] = lines[-1].strip()
        return self.protocols[fid]

    def require_fragments(self, *fids):
        """Stop before the first request unless every format is still served as fragments."""
        for fid in fids:
            p = self.protocol(fid)
            if p != FRAGMENTED:
                raise NotFragmented(
                    f'{self.vid} format {fid} is a processed VOD (protocol {p}), not 2-second fragments: download it '
                    f'with yt-dlp, e.g. yt-dlp -f {fid} https://www.youtube.com/watch?v={self.vid} (add '
                    '--download-sections "*START-END" for a window; vodwatch.py and cut_windows.py switch by themselves)')

    def refresh(self, fid=None):
        with self.lock:
            for f in ([fid] if fid else list(self.urls)):
                if time.time() - self.when.get(f, 0) < 20:
                    continue
                out = subprocess.run(['yt-dlp', '-g', '--no-warnings', '-f', str(f), f'https://www.youtube.com/watch?v={self.vid}'],
                                     capture_output=True, text=True, check=True).stdout.strip().splitlines()
                self.urls[f], self.when[f] = out[0], time.time()

    def base(self, fid):
        if fid not in self.urls:
            self.refresh(fid)
        return self.urls[fid]

    def frag_url(self, fid, sq):
        return self.base(fid) + f'&sq={sq}'

    def duration(self):
        if not hasattr(self, '_dur'):
            out = subprocess.run(['yt-dlp', '--print', 'duration', '--no-warnings', f'https://www.youtube.com/watch?v={self.vid}'],
                                 capture_output=True, text=True, check=True).stdout.strip()
            self._dur = float(out.splitlines()[-1])
        return self._dur


_COUNT = {}
_CLOCK = threading.Lock()


def fetch(src, fid, sq, tries=8):
    last = None
    with _CLOCK:
        _COUNT[fid] = _COUNT.get(fid, 0) + 1
        rotate = _COUNT[fid] % 1500 == 0
    if rotate:  # YouTube throttles a signed URL after a few thousand fragments; a fresh one is fast again
        src.when[fid] = 0
        src.refresh(fid)
    for i in range(tries):
        try:
            t0 = time.time()
            req = urllib.request.Request(src.frag_url(fid, sq), headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                size = int(r.headers.get('Content-Length') or 0)
                data = b'' if size > MAX_FRAG_BYTES else r.read(MAX_FRAG_BYTES + 1)
            if size > MAX_FRAG_BYTES or len(data) > MAX_FRAG_BYTES:
                raise WholeFileServed(f'{src.vid} format {fid}: fragment {sq} came back as '
                                      f'{max(size, len(data)) / 1048576:.0f}+ MiB, so YouTube no longer serves this VOD '
                                      'as 2-second fragments (it has processed it): download it with yt-dlp')
            if time.time() - t0 > 4:
                src.when[fid] = 0
                src.refresh(fid)
            return data
        except WholeFileServed:
            raise
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (401, 403, 410):
                src.when[fid] = 0
                src.refresh(fid)
            time.sleep(0.5 * (i + 1))
        except Exception as e:  # timeouts, resets
            last = e
            if i >= 1:
                src.when[fid] = 0
                src.refresh(fid)
            time.sleep(0.5 * (i + 1))
    raise RuntimeError(f'fragment {fid}/{sq} failed: {last}')


def download_range(src, fid, sq0, sq1, path, jobs=8, progress=True, tolerate_tail=False, assemble=True):
    n = sq1 - sq0 + 1
    tmpdir = os.path.abspath(path) + f'.frags{fid}'
    os.makedirs(tmpdir, exist_ok=True)
    done = [0]
    failed = []
    whole = []  # set once a reply was a whole file: every fragment still queued then returns at once
    t0 = time.time()

    def one(sq):
        if whole:
            return
        if os.path.exists(os.path.join(tmpdir, f'{sq:07d}')):
            done[0] += 1
            return
        try:
            data = fetch(src, fid, sq, tries=3 if (tolerate_tail and sq > sq1 - 80) else 5)
        except WholeFileServed as e:
            whole.append(str(e))
            return
        except RuntimeError:
            failed.append(sq)
            return
        tmp = os.path.join(tmpdir, f'.{sq:07d}')
        with open(tmp, 'wb') as w:
            w.write(data)
        os.replace(tmp, os.path.join(tmpdir, f'{sq:07d}'))
        done[0] += 1
        if progress and done[0] % 500 == 0:
            el = time.time() - t0
            print(f'  [{fid}] {done[0]}/{n} frags, {el:.0f}s elapsed, ~{el / done[0] * (n - done[0]):.0f}s left', flush=True)

    def stop_if_whole():
        if whole:
            try:
                os.rmdir(tmpdir)  # only when empty: fragments from an earlier run stay where they are
            except OSError:
                pass
            raise NotFragmented(whole[0])

    with ThreadPoolExecutor(jobs) as ex:
        list(ex.map(one, range(sq0, sq1 + 1)))
    stop_if_whole()
    for rnd in range(4):
        if not failed:
            break
        retry, failed[:] = sorted(set(failed)), []
        print(f'  [{fid}] retry round {rnd + 1}: {len(retry)} fragments', flush=True)
        src.when[fid] = 0
        src.refresh(fid)
        with ThreadPoolExecutor(max(2, jobs // 4)) as ex:
            list(ex.map(one, retry))
        stop_if_whole()
    if failed:
        tail_only = all(sq > sq1 - 80 for sq in failed)
        if not (tolerate_tail and (tail_only or len(failed) < 50)):
            raise RuntimeError(f'{len(failed)} fragments failed after retries: {sorted(failed)[:20]}')
        print(f'  [{fid}] giving up on {len(failed)} fragments (gaps become silence): {sorted(failed)[:20]}', flush=True)
    if not assemble:
        return tmpdir
    with open(path, 'wb') as w:
        for sq in range(sq0, sq1 + 1):
            p = os.path.join(tmpdir, f'{sq:07d}')
            if not os.path.exists(p):
                continue
            with open(p, 'rb') as r:
                shutil.copyfileobj(r, w)
    shutil.rmtree(tmpdir, ignore_errors=True)


def probe_start(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=start_time', '-of', 'csv=p=0', path],
                         capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def probe_span(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=start_time,duration', '-of', 'csv=p=0', path],
                         capture_output=True, text=True).stdout.strip().split(',')
    st = float(out[0]) if out and out[0] not in ('', 'N/A') else 0.0
    du = float(out[1]) if len(out) > 1 and out[1] not in ('', 'N/A') else 0.0
    return st, st + du


MUXER = {'.m4a': 'ipod', '.m4b': 'ipod', '.mp4': 'mp4', '.mov': 'mov', '.aac': 'adts'}


def muxer_for(out):
    """ffmpeg can't tell the format from a .part name, so it is named from OUT's own extension."""
    return MUXER.get(os.path.splitext(out)[1].lower(), 'mp4')


def cmd_window(argv):
    if len(argv) < 4:
        raise SystemExit('usage: ytfrag.py window VIDEO_ID START END OUT.mp4 [--video 299] [--audio 140] [--pad 3]')
    vid, a, b, out = argv[0], hms(argv[1]), hms(argv[2]), argv[3]
    opts = dict(zip(argv[4::2], argv[5::2]))
    vf, af, pad = opts.get('--video', '299'), opts.get('--audio', '140'), float(opts.get('--pad', 3))
    src = Source(vid)
    src.require_fragments(vf, *([af] if af and af != 'none' else []))   # before the first request
    seg = Source.SEG
    work = out + '.parts'
    os.makedirs(work, exist_ok=True)
    vpath, apath = os.path.join(work, 'v.mp4'), os.path.join(work, 'a.m4a')
    sq0, sq1 = max(0, int((a - pad) // seg)), int((b + pad) // seg) + 1
    try:
        for attempt in range(4):
            download_range(src, vf, sq0, sq1, vpath, progress=False)
            vstart, vend = probe_span(vpath)
            if vstart <= a + 0.01 and vend >= b - 0.01:
                break
            # stream gaps shift the index: move the window by the observed error and retry
            shift = int(round((a - pad - vstart) / seg)) if vstart > a else int(round((b + pad - vend) / seg))
            if shift == 0:
                shift = -2 if vstart > a else 2
            sq0, sq1 = max(0, sq0 + shift), sq1 + shift
            print(f'  window miss (got {vstart:.2f}-{vend:.2f}), shifting fragments by {shift}', flush=True)
        else:
            raise SystemExit(f'could not cover {a}-{b}s (last got {vstart:.2f}-{vend:.2f})')
        cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-i', vpath]
        if af and af != 'none':
            download_range(src, af, sq0, sq1, apath, progress=False)
            astart, _ = probe_span(apath)
            cmd += ['-itsoffset', f'{astart - vstart:.3f}', '-i', apath, '-map', '0:v:0', '-map', '1:a:0']
    except NotFragmented:
        try:
            os.rmdir(work)  # only when empty
        except OSError:
            pass
        raise
    part = out + '.part'
    cmd += ['-ss', f'{a - vstart:.3f}', '-t', f'{b - a:.3f}', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '16',
            '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-f', muxer_for(out), part]
    try:
        subprocess.run(cmd, check=True)
    except BaseException:
        if os.path.exists(part):
            os.remove(part)
        raise
    os.replace(part, out)  # OUT appears only once it is complete
    shutil.rmtree(work, ignore_errors=True)
    print(f'wrote {out}: {a:.2f}-{b:.2f}s of {vid} (fragments {sq0}-{sq1})')


def assemble_track(tmpdir, n, out):
    """Stream fragments 0..n-1 of tmpdir through ffmpeg into compact mono 16 kHz AAC (32 kbps).
    ffmpeg writes <out>.part; only when it exits 0 is that renamed to <out> and the fragments deleted.
    On failure the fragments stay, so the next run re-assembles without downloading again."""
    part = out + '.part'
    ff = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'mp4', '-i', 'pipe:0', '-vn',
                           '-af', 'aresample=async=1:first_pts=0', '-ac', '1', '-ar', '16000',
                           '-c:a', 'aac', '-b:a', '32k', '-f', muxer_for(out), part], stdin=subprocess.PIPE)
    try:
        for sq in range(0, n):
            p = os.path.join(tmpdir, f'{sq:07d}')
            if os.path.exists(p):
                with open(p, 'rb') as r:
                    ff.stdin.write(r.read())
    except BrokenPipeError:
        pass                                    # ffmpeg died; its exit code says so below
    finally:
        try:
            ff.stdin.close()
        except BrokenPipeError:
            pass
    if ff.wait() != 0:
        if os.path.exists(part):
            os.remove(part)
        raise SystemExit(f'ffmpeg could not assemble {out} (exit {ff.returncode}); the fragments are kept in '
                         f'{tmpdir}, so re-run the same command to try again without downloading')
    os.replace(part, out)
    shutil.rmtree(tmpdir, ignore_errors=True)
    return out


def cmd_track(argv):
    """Whole track -> compact mono 16 kHz AAC (32 kbps). The fragments are kept until the assembled
    file is complete, so peak disk use is the fragments plus the (smaller) finished track."""
    if len(argv) < 3:
        raise SystemExit('usage: ytfrag.py track VIDEO_ID FORMAT_ID OUT.m4a [--jobs 8] [--frags N]')
    vid, fid, out = argv[0], argv[1], argv[2]
    opts = dict(zip(argv[3::2], argv[4::2]))
    jobs = int(opts.get('--jobs', 8))
    if os.path.exists(out) and not os.path.isdir(os.path.abspath(out + '.raw') + f'.frags{fid}'):
        print(f'{out} is already assembled (delete it to download again)', flush=True)
        return
    src = Source(vid)
    src.require_fragments(fid)   # before the first request
    n = int(opts.get('--frags', 0)) or int(src.duration() // Source.SEG) + 2
    print(f'{vid} format {fid}: fetching ~{n} fragments', flush=True)
    tmpdir = download_range(src, fid, 0, n - 1, out + '.raw', jobs=jobs, tolerate_tail=True, assemble=False)
    assemble_track(tmpdir, n, out)
    st, en = probe_span(out)
    print(f'wrote {out} (duration {en - st:.1f}s)', flush=True)


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] in ('-h', '--help', 'help'):
        print(__doc__)
        sys.exit(0)
    if len(sys.argv) < 2 or sys.argv[1] not in ('window', 'track'):
        print(__doc__)
        sys.exit(2)
    try:
        {'window': cmd_window, 'track': cmd_track}[sys.argv[1]](sys.argv[2:])
    except NotFragmented as e:
        print(e, file=sys.stderr)
        sys.exit(EXIT_NOT_FRAGMENTED)
