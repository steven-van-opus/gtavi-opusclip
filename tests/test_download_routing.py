"""How VOD downloads are routed, now that YouTube serves the GTA 6 launch VODs as processed files. All offline.

Right after a long stream YouTube serves the VOD as 2-second fragments (yt-dlp protocol http_dash_segments); later
it processes it into whole files (https) and answers every "fragment" request with the entire file. A fake `yt-dlp`
(and `whisper-cli`) on PATH and a local HTTP server stand in for YouTube and the model:

- ytfrag.py refuses a processed VOD before its first request (exit 3), stops after one round when a "fragment"
  is a whole file (with or without Content-Length), and still downloads real fragments.
- vodwatch.py asks for the protocol before every audio download: ytfrag.py track while the VOD is still fragments,
  yt-dlp -f 139/ba/w plus a mono 16 kHz extract once it is processed (also when ytfrag.py exits 3), -f ba/w for
  Twitch; run.json and report.md say which way the audio came.
- cut_windows.py (and frame_sheet.py through it) asks before every cut: ytfrag.py window for fragments, yt-dlp
  --download-sections with the H.264 + AAC ids first for a processed VOD, and yt-dlp when ytfrag.py exits 3.
- scan_frags.py never writes a chunk twice when the track is assembled while whisper runs.
"""
import contextlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, '..', 'scripts'))
sys.path.insert(0, SCRIPTS)

import cut_windows  # noqa: E402
import vodwatch  # noqa: E402
import ytfrag  # noqa: E402

HAVE_FFMPEG = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))
VID = 'qSyo5d-dVzA'
META = {'kind': 'youtube', 'id': VID, 'url': f'https://www.youtube.com/watch?v={VID}'}

FAKE_YTDLP = r'''#!{python} -I
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as f:
    f.write(json.dumps(args) + '\n')


def opt(name):
    return args[args.index(name) + 1] if name in args else None


printed = opt('--print') or ''
if printed == 'protocol':
    if os.environ.get('FAKE_PROBE_FAIL'):
        sys.exit('ERROR: [youtube] fake: Requested format is not available')
    print(os.environ.get('FAKE_PROTOCOL', 'https'))
    sys.exit(0)
if printed == 'duration':
    print(os.environ.get('FAKE_DURATION', '60'))
    sys.exit(0)
if printed.startswith('%(id)s'):  # vodwatch.py resolve
    print('\t'.join(['qSyo5d-dVzA', 'Test stream', 'Test Streamer', os.environ.get('FAKE_DURATION', '60'), 'was_live']))
    sys.exit(0)
if '-g' in args:  # the signed base URL of a format's fragments
    print(os.environ['FAKE_FRAG_BASE'] + '?itag=' + opt('-f'))
    sys.exit(0)
src, out, sec = os.environ['FAKE_SOURCE'], opt('-o'), opt('--download-sections')
if sec:  # what yt-dlp does with --download-sections: just that window of the source
    a, b = (float(x) for x in sec.lstrip('*').split('-'))
    cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-ss', str(a), '-t', str(b - a), '-i', src, '-c:v', 'libx264',
           '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', out.replace('%(ext)s', 'mp4')]
else:  # a whole-format download: the scan audio
    cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-i', src, '-vn', '-c:a', 'aac', out.replace('%(ext)s', 'm4a')]
sys.exit(subprocess.run(cmd).returncode)
'''

FAKE_WHISPER = r'''#!{python} -I
import json, os, sys
args = sys.argv[1:]
base = args[args.index('-of') + 1]
with open(base + '.json', 'w') as f:
    json.dump({'transcription': [{'text': ' hello there', 'offsets': {'from': 1000, 'to': 2000}}]}, f)
if os.environ.get('FAKE_ASSEMBLE_DURING_WHISPER'):  # ytfrag.py finishes the track while whisper runs
    open(os.environ['FAKE_ASSEMBLE_DURING_WHISPER'], 'wb').close()
'''


def env(**values):
    return mock.patch.dict(os.environ, values)


def make_video(path, seconds=6):
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size=320x568:rate=30:duration={seconds}',
                    '-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}', '-c:v', 'libx264', '-preset', 'ultrafast',
                    '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', path], check=True)
    return path


def make_audio(path, seconds=6):
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=44100:duration={seconds}',
                    '-ac', '2', '-c:a', 'aac', path], check=True)
    return path


def fmp4_audio(path, seconds=4):
    """A fragmented MP4 audio track: cut into byte pieces and joined again, it reads like YouTube's fragments."""
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=48000:duration={seconds}',
                    '-c:a', 'aac', '-movflags', 'frag_keyframe+empty_moov+default_base_moof', '-f', 'mp4', path], check=True)
    with open(path, 'rb') as f:
        return f.read()


def pieces(data, n):
    step = -(-len(data) // n)
    return [data[i * step:(i + 1) * step] for i in range(n)]


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=channels,sample_rate:format=duration', '-of', 'json',
                          path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def read(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


@contextlib.contextmanager
def serve(handler):
    """A local HTTP server in a thread; yields its base URL."""
    srv = http.server.ThreadingHTTPServer(('127.0.0.1', 0), handler)
    srv.daemon_threads = True
    srv.handle_error = lambda *a: None   # the client hangs up mid-body on purpose
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        yield f'http://127.0.0.1:{srv.server_address[1]}'
    finally:
        srv.shutdown()
        srv.server_close()


class FakeResponse:
    """What urllib.request.urlopen returns, for the mocked-HTTP tests."""

    def __init__(self, body, headers=None):
        self.body, self.headers, self.reads = body, headers or {}, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, n=-1):
        self.reads.append(n)
        return self.body if n is None or n < 0 else self.body[:n]


@unittest.skipIf(' ' in sys.executable, 'the fake tools need a python path without spaces for their #! line')
class FakeTools(unittest.TestCase):
    """yt-dlp and whisper-cli on PATH are fakes, for subprocesses and for code imported into this process."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = os.path.join(self.tmp, 'bin')
        os.makedirs(self.bin)
        for name, body in (('yt-dlp', FAKE_YTDLP), ('whisper-cli', FAKE_WHISPER)):
            p = os.path.join(self.bin, name)
            with open(p, 'w') as f:
                f.write(body.replace('{python}', sys.executable))
            os.chmod(p, 0o755)
        self.log = os.path.join(self.tmp, 'calls.jsonl')
        # port 9 refuses connections: a fragment request nobody planned for fails fast instead of going anywhere
        patcher = env(PATH=self.bin + os.pathsep + os.environ.get('PATH', ''), FAKE_LOG=self.log,
                      FAKE_FRAG_BASE='http://127.0.0.1:9/videoplayback')
        patcher.start()
        self.addCleanup(patcher.stop)
        ytfrag._COUNT.clear()

    def run_script(self, name, *args, **extra):
        return subprocess.run([sys.executable, '-I', os.path.join(SCRIPTS, name)] + [str(a) for a in args],
                              capture_output=True, text=True, timeout=300, env=dict(os.environ, **extra))

    def calls(self):
        if not os.path.exists(self.log):
            return []
        with open(self.log) as f:
            return [json.loads(line) for line in f]

    def probes(self):
        return [c for c in self.calls() if '--print' in c and c[c.index('--print') + 1] == 'protocol']

    def folder(self, name):
        d = os.path.join(self.tmp, name)
        os.makedirs(d)
        return d


class YtfragGuards(FakeTools):
    def test_a_processed_vod_is_refused_before_the_first_request(self):
        track, window = os.path.join(self.tmp, 'a.m4a'), os.path.join(self.tmp, 'w.mp4')
        for args in (['track', VID, '139', track], ['window', VID, '10', '40', window, '--video', '134', '--audio', '139']):
            r = self.run_script('ytfrag.py', *args, FAKE_PROTOCOL='https')
            self.assertEqual(r.returncode, ytfrag.EXIT_NOT_FRAGMENTED, r.stderr)
            self.assertIn('processed VOD (protocol https)', r.stderr)
            self.assertNotIn('Traceback', r.stderr)
            self.assertEqual(sorted(os.listdir(self.tmp)), ['bin', 'calls.jsonl'], args[0])   # nothing written
        self.assertEqual(len(self.calls()), 2, self.calls())    # one protocol question per command
        self.assertEqual(len(self.probes()), 2, 'no signed URL was fetched and no duration asked')

    def test_an_unreadable_format_is_not_taken_for_a_processed_vod(self):
        r = self.run_script('ytfrag.py', 'track', VID, '139', os.path.join(self.tmp, 'a.m4a'), FAKE_PROBE_FAIL='1')
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn('could not read format 139', r.stderr)

    def test_a_whole_file_reply_stops_the_download(self):
        hits = []

        class WholeFile(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(200)
                self.send_header('Content-Length', '521615154')   # the processed format-139 file of qSyo5d-dVzA
                self.end_headers()
                try:
                    self.wfile.write(b'\0' * 65536)
                except OSError:
                    pass

            def log_message(self, *a):
                pass

        raw = os.path.join(self.tmp, 'a.m4a.raw')
        with serve(WholeFile) as base:
            src = ytfrag.Source(VID)
            src.urls['139'], src.when['139'] = base + '/videoplayback?itag=139', time.time() + 3600   # no -g refresh
            with self.assertRaises(ytfrag.NotFragmented) as cm:
                ytfrag.download_range(src, '139', 0, 999, raw, jobs=4, progress=False, tolerate_tail=True, assemble=False)
        self.assertIn('no longer serves this VOD as 2-second fragments', str(cm.exception))
        self.assertIn('497+ MiB', str(cm.exception))
        self.assertLessEqual(len(hits), 8, 'it kept requesting after the first whole-file reply')
        self.assertFalse(os.path.exists(raw + '.frags139'), 'the empty fragment folder is removed')
        self.assertEqual(self.calls(), [])

    def test_a_whole_file_without_content_length_is_caught_after_16_mb(self):
        resp = FakeResponse(b'\0' * (ytfrag.MAX_FRAG_BYTES + 4096))
        src = ytfrag.Source(VID)
        src.urls['140'], src.when['140'] = 'http://127.0.0.1:9/videoplayback?itag=140', time.time() + 3600
        with mock.patch.object(ytfrag.urllib.request, 'urlopen', lambda req, timeout=None: resp):
            with self.assertRaises(ytfrag.WholeFileServed):
                ytfrag.fetch(src, '140', 7)
        self.assertEqual(resp.reads, [ytfrag.MAX_FRAG_BYTES + 1], 'it never reads more than 16 MB + 1 byte')

    def test_real_fragments_still_download(self):
        asked = []

        def urlopen(req, timeout=None):
            sq = int(urllib.parse.parse_qs(urllib.parse.urlparse(req.full_url).query)['sq'][0])
            asked.append(sq)
            body = b'fragment-%03d;' % sq
            return FakeResponse(body, {'Content-Length': str(len(body))})

        src = ytfrag.Source(VID)
        src.urls['134'], src.when['134'] = 'http://127.0.0.1:9/videoplayback?itag=134', time.time() + 3600
        out = os.path.join(self.tmp, 'v.mp4')
        with mock.patch.object(ytfrag.urllib.request, 'urlopen', urlopen):
            ytfrag.download_range(src, '134', 3, 9, out, jobs=3, progress=False)
        with open(out, 'rb') as f:
            self.assertEqual(f.read(), b''.join(b'fragment-%03d;' % sq for sq in range(3, 10)))
        self.assertEqual(sorted(asked), list(range(3, 10)))
        self.assertFalse(os.path.exists(out + '.frags134'))

    @unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
    def test_a_fragmented_vod_still_downloads_end_to_end(self):
        parts = pieces(fmp4_audio(os.path.join(self.tmp, 'src.mp4')), 7)
        hits = []

        class Fragments(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                sq = int(urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)['sq'][0])
                hits.append(sq)
                if sq >= len(parts):
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(parts[sq])))
                self.end_headers()
                self.wfile.write(parts[sq])

            def log_message(self, *a):
                pass

        out = os.path.join(self.tmp, 'audio.m4a')
        with serve(Fragments) as base:
            r = self.run_script('ytfrag.py', 'track', VID, '139', out, '--frags', '7', '--jobs', '3',
                                FAKE_PROTOCOL='http_dash_segments', FAKE_FRAG_BASE=base + '/videoplayback')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(sorted(hits), list(range(7)))
        info = probe(out)
        self.assertAlmostEqual(float(info['format']['duration']), 4.0, delta=0.3)
        self.assertEqual((info['streams'][0]['channels'], info['streams'][0]['sample_rate']), (1, '16000'))
        self.assertFalse(os.path.exists(out + '.part'))
        self.assertFalse(os.path.exists(out + '.raw.frags139'))
        first, second = self.calls()[:2]
        self.assertEqual(first[first.index('--print') + 1], 'protocol')   # asked before the first request
        self.assertIn('-g', second)


class VodwatchAudio(FakeTools):
    @unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
    def test_a_processed_vod_comes_through_ytdlp_audio_only(self):
        src = make_audio(os.path.join(self.tmp, 'src.m4a'))
        out = self.folder('scan')
        info = {}
        with env(FAKE_PROTOCOL='https', FAKE_SOURCE=src), contextlib.redirect_stderr(io.StringIO()):
            dest = vodwatch.get_audio(dict(META), out, info=info)
        self.assertEqual(os.listdir(out), ['audio.m4a'], "yt-dlp's file is removed once the track is complete")
        st = probe(dest)['streams'][0]
        self.assertEqual((st['channels'], st['sample_rate']), (1, '16000'))
        self.assertEqual(info, {'protocol': 'https', 'via': 'yt-dlp', 'format': '139/ba/w'})
        ask, download = self.calls()
        self.assertEqual(ask[ask.index('-f') + 1], '139/ba')
        self.assertIn('--skip-download', ask)
        self.assertEqual(download[download.index('-f') + 1], '139/ba/w')
        self.assertNotIn('-g', download)

    def test_a_fragmented_vod_goes_to_ytfrag_track(self):
        cmds = []

        def fake_sh(cmd, **kw):
            cmds.append([str(c) for c in cmd])
            open(cmd[cmd.index('track') + 3], 'wb').close()   # what ytfrag.py leaves: the finished track

        out = self.folder('scan')
        info = {}
        with env(FAKE_PROTOCOL='http_dash_segments'), mock.patch.object(vodwatch, 'sh', fake_sh), \
                contextlib.redirect_stderr(io.StringIO()):
            dest = vodwatch.get_audio(dict(META), out, jobs=4, info=info)
        self.assertEqual(len(cmds), 1)
        self.assertEqual(cmds[0][2:], [os.path.join(vodwatch.HERE, 'ytfrag.py'), 'track', VID, '139', dest, '--jobs', '4'])
        self.assertEqual(info, {'protocol': 'http_dash_segments', 'via': 'ytfrag.py', 'format': '139'})
        self.assertEqual(len(self.calls()), 1, 'only the protocol question; ytfrag.py does the rest')

    @unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
    def test_the_protocol_is_asked_again_on_the_next_run(self):
        out = self.folder('scan')
        frags = os.path.join(out, 'audio.m4a.raw.frags139')

        def interrupted(cmd, **kw):   # run 1: still fragments, and ytfrag.py is stopped halfway
            os.makedirs(frags)
            open(os.path.join(frags, '0000000'), 'wb').close()
            raise subprocess.CalledProcessError(1, cmd)

        with env(FAKE_PROTOCOL='http_dash_segments'), mock.patch.object(vodwatch, 'sh', interrupted), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):
                vodwatch.get_audio(dict(META), out)
        src = make_audio(os.path.join(self.tmp, 'src.m4a'))
        err = io.StringIO()
        with env(FAKE_PROTOCOL='https', FAKE_SOURCE=src), contextlib.redirect_stderr(err):   # run 2: processed since
            vodwatch.get_audio(dict(META), out)
        self.assertTrue(os.path.getsize(os.path.join(out, 'audio.m4a')) > 0)
        self.assertIn('processed VOD (protocol https)', err.getvalue())
        self.assertIn('cannot be resumed now', err.getvalue())
        self.assertEqual(len(self.probes()), 2)

    @unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
    def test_ytfrag_exit_3_hands_the_audio_to_ytdlp(self):
        src = make_audio(os.path.join(self.tmp, 'src.m4a'))
        real_sh, tries = vodwatch.sh, []

        def fake_sh(cmd, **kw):
            if 'track' in cmd:   # ytfrag.py: YouTube processed the VOD right after vodwatch asked
                tries.append(cmd)
                os.environ['FAKE_PROTOCOL'] = 'https'
                raise subprocess.CalledProcessError(ytfrag.EXIT_NOT_FRAGMENTED, cmd)
            return real_sh(cmd, **kw)

        out = self.folder('scan')
        info = {}
        with env(FAKE_PROTOCOL='http_dash_segments', FAKE_SOURCE=src), mock.patch.object(vodwatch, 'sh', fake_sh), \
                contextlib.redirect_stderr(io.StringIO()):
            vodwatch.get_audio(dict(META), out, info=info)
        self.assertEqual(len(tries), 1)
        self.assertEqual(info, {'protocol': 'https', 'via': 'yt-dlp', 'format': '139/ba/w'})
        self.assertEqual(os.listdir(out), ['audio.m4a'])
        self.assertEqual(len(self.probes()), 2, 'asked again before the yt-dlp download')

    def test_ytfrag_exit_3_while_ytdlp_still_says_fragments_stops(self):
        def fake_sh(cmd, **kw):
            raise subprocess.CalledProcessError(ytfrag.EXIT_NOT_FRAGMENTED, cmd)

        with env(FAKE_PROTOCOL='http_dash_segments'), mock.patch.object(vodwatch, 'sh', fake_sh), \
                contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                vodwatch.get_audio(dict(META), self.folder('scan'))
        self.assertIn('run the same command again later', str(cm.exception))

    def test_twitch_audio_is_audio_only_or_the_smallest_rendition(self):
        cmds = []

        def fake_sh(cmd, **kw):
            cmds.append(cmd)
            raise subprocess.CalledProcessError(1, cmd)   # stop at the download

        with mock.patch.object(vodwatch, 'sh', fake_sh), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(subprocess.CalledProcessError):
                vodwatch.get_audio({'kind': 'twitch', 'id': None, 'url': 'https://www.twitch.tv/videos/123456789'},
                                   self.folder('scan'))
        self.assertEqual(cmds[0][cmds[0].index('-f') + 1], 'ba/w')
        self.assertEqual(self.calls(), [], 'no protocol question outside YouTube')

    def test_an_unreadable_vod_stops_before_any_download(self):
        with env(FAKE_PROBE_FAIL='1'), mock.patch.object(vodwatch, 'sh', side_effect=AssertionError('no download')):
            with self.assertRaises(SystemExit) as cm:
                vodwatch.get_audio(dict(META), self.folder('scan'))
        self.assertIn('could not read the audio formats', str(cm.exception))

    def test_the_report_says_how_the_audio_came(self):
        out = self.folder('scan')
        cases = (({'protocol': 'https', 'via': 'yt-dlp', 'format': '139/ba/w'},
                  '- Audio: yt-dlp -f 139/ba/w (processed YouTube VOD, protocol https)'),
                 ({'protocol': 'http_dash_segments', 'via': 'ytfrag.py', 'format': '139'},
                  '- Audio: ytfrag.py track 139 (YouTube still served the VOD as 2-second fragments)'),
                 ({'via': 'yt-dlp', 'format': 'ba/w'}, '- Audio: yt-dlp -f ba/w\n'))
        for audio, line in cases:
            vodwatch.write_report(out, {'stages': {}, 'source': dict(META, title='Test stream'), 'audio': audio})
            self.assertIn(line, read(os.path.join(out, 'report.md')))
        vodwatch.write_report(out, {'stages': {}, 'source': dict(META, title='Test stream')})   # a 1.0.0 run.json
        self.assertNotIn('- Audio:', read(os.path.join(out, 'report.md')))

    @unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
    def test_vodwatch_run_on_a_processed_vod(self):
        src = make_audio(os.path.join(self.tmp, 'src.m4a'), seconds=65)
        model = os.path.join(self.tmp, 'model.bin')
        open(model, 'wb').close()
        out = os.path.join(self.tmp, 'scan')
        r = self.run_script('vodwatch.py', 'run', VID, '--out', out, '--sheets', '0', '--no-plan', '--model', model,
                            FAKE_PROTOCOL='https', FAKE_SOURCE=src, FAKE_DURATION='65')
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        with open(os.path.join(out, 'run.json')) as f:
            run = json.load(f)
        self.assertEqual(run['audio'], {'protocol': 'https', 'via': 'yt-dlp', 'format': '139/ba/w'})
        self.assertIn('- Audio: yt-dlp -f 139/ba/w (processed YouTube VOD, protocol https)',
                      read(os.path.join(out, 'report.md')))
        self.assertFalse(any('-g' in c for c in self.calls()), 'no fragment URL was asked for')
        self.assertFalse(os.path.exists(os.path.join(out, 'audio.m4a.raw.frags139')))


class CutWindows(FakeTools):
    @unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
    def test_a_processed_vod_is_cut_by_ytdlp_h264_first(self):
        src = make_video(os.path.join(self.tmp, 'src.mp4'))
        out = os.path.join(self.tmp, 'w.mp4')
        r = self.run_script('cut_windows.py', '--source', VID, '--start', '1', '--end', '4', '--out', out,
                            FAKE_PROTOCOL='https', FAKE_SOURCE=src)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['method'], 'yt-dlp')
        self.assertIn('processed VOD (protocol https)', r.stderr)
        ask, download = self.calls()
        self.assertEqual(ask[ask.index('-f') + 1], '299')
        self.assertEqual(download[download.index('-f') + 1],
                         '299+140/137+140/298+140/136+140/bv*[height<=1080]+ba/b[height<=1080]')
        self.assertEqual(download[download.index('--download-sections') + 1], '*1.00-4.00')
        self.assertAlmostEqual(float(probe(out)['format']['duration']), 3.0, delta=0.3)

    def test_a_fragmented_vod_is_cut_by_ytfrag(self):
        ran = []

        def fake_run(cmd, **kw):
            ran.append(cmd)
            open(cmd[cmd.index('window') + 4], 'wb').close()

        out = os.path.join(self.tmp, 'w.mp4')
        with env(FAKE_PROTOCOL='http_dash_segments'), mock.patch.object(cut_windows, 'run', fake_run):
            how = cut_windows.cut_youtube(VID, 10.0, 40.0, out)
        self.assertEqual(how, ('ytfrag', '299'))
        self.assertEqual(ran[0][2:], [cut_windows.YTFRAG, 'window', VID, '10.0', '40.0', out, '--video', '299',
                                      '--audio', '140'])

    def test_ytfrag_exit_3_sends_the_cut_to_ytdlp(self):
        ran = []

        def fake_run(cmd, **kw):
            ran.append(cmd)
            if cmd[2:3] == [cut_windows.YTFRAG]:   # YouTube processed the VOD between the question and the download
                raise subprocess.CalledProcessError(ytfrag.EXIT_NOT_FRAGMENTED, cmd)
            open(cmd[cmd.index('-o') + 1].replace('%(ext)s', 'mp4'), 'wb').close()

        out = os.path.join(self.tmp, 'w.mp4')
        with env(FAKE_PROTOCOL='http_dash_segments'), mock.patch.object(cut_windows, 'run', fake_run), \
                contextlib.redirect_stderr(io.StringIO()):
            how = cut_windows.cut_youtube(VID, 10.0, 40.0, out)
        self.assertEqual(how, ('yt-dlp', None))
        self.assertEqual([c[0] for c in ran], [sys.executable, 'yt-dlp'], 'one ytfrag.py try, no walk down the ladder')
        self.assertIn('--download-sections', ran[1])

    def test_the_protocol_is_asked_before_every_cut(self):
        ran = []

        def fake_run(cmd, **kw):
            ran.append(cmd)
            target = cmd[cmd.index('window') + 4] if cmd[2:3] == [cut_windows.YTFRAG] else \
                cmd[cmd.index('-o') + 1].replace('%(ext)s', 'mp4')
            open(target, 'wb').close()

        with mock.patch.object(cut_windows, 'run', fake_run), contextlib.redirect_stderr(io.StringIO()):
            with env(FAKE_PROTOCOL='http_dash_segments'):
                first = cut_windows.cut_youtube(VID, 10.0, 40.0, os.path.join(self.tmp, 'a.mp4'))
            with env(FAKE_PROTOCOL='https'):   # YouTube processed the VOD between two cuts
                second = cut_windows.cut_youtube(VID, 50.0, 80.0, os.path.join(self.tmp, 'b.mp4'))
        self.assertEqual((first[0], second[0]), ('ytfrag', 'yt-dlp'))
        self.assertEqual(len(self.probes()), 2)

    def test_twitch_windows_keep_the_generic_format(self):
        self.assertEqual(cut_windows.ytdlp_format(1080, True), 'bv*[height<=1080]+ba/b[height<=1080]')
        self.assertEqual(cut_windows.ytdlp_format(360, False, cut_windows.VIDEO_LADDER[360]),
                         '134/133/bv*[height<=360]/b[height<=360]')

    @unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
    def test_a_frame_sheet_of_a_processed_vod(self):
        src = make_video(os.path.join(self.tmp, 'src.mp4'), seconds=10)
        out = os.path.join(self.tmp, 'sheets', 'manual.jpg')
        r = self.run_script('frame_sheet.py', '--source', VID, '--start', '2', '--end', '8', '--out', out,
                            FAKE_PROTOCOL='https', FAKE_SOURCE=src)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.splitext(out)[0] + '.json') as f:
            meta = json.load(f)
        self.assertEqual(len(meta['frames']), 12)
        self.assertEqual(meta['frames'][0]['stream_hms'], '0:00:02')
        ask, download = self.calls()
        self.assertEqual(ask[ask.index('-f') + 1], '134')
        self.assertEqual(download[download.index('-f') + 1], '134/133/bv*[height<=360]/b[height<=360]')


@unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
class ScanFrags(FakeTools):
    def setUp(self):
        super().setUp()
        self.frags = os.path.join(self.tmp, 'audio.m4a.raw.frags139')
        os.makedirs(self.frags)
        for i, piece in enumerate(pieces(fmp4_audio(os.path.join(self.tmp, 'src.mp4')), 600)):
            with open(os.path.join(self.frags, f'{i:07d}'), 'wb') as f:
                f.write(piece)
        self.model = os.path.join(self.tmp, 'model.bin')
        open(self.model, 'wb').close()
        self.scan = os.path.join(self.tmp, 'scan')
        self.assembled = os.path.join(self.tmp, 'audio.m4a')

    def transcript(self):
        p = os.path.join(self.scan, 'transcript.jsonl')
        if not os.path.exists(p):
            return []
        with open(p) as f:
            return [json.loads(line) for line in f]

    def frag_scan(self, **extra):
        return self.run_script('scan_frags.py', self.frags, self.scan, self.assembled, '--chunks', '1',
                               '--model', self.model, **extra)

    def test_a_chunk_finished_after_assembly_is_dropped(self):
        r = self.frag_scan(FAKE_ASSEMBLE_DURING_WHISPER=self.assembled)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('assembled track appeared during chunk 1', r.stdout)
        self.assertEqual(self.transcript(), [], 'scan_vod.py transcribes chunk 0 instead')
        self.assertEqual(sorted(f for f in os.listdir(self.scan) if f.startswith('_f')), [])

    def test_a_chunk_is_written_once(self):
        for _ in range(2):
            r = self.frag_scan()
            self.assertEqual(r.returncode, 0, r.stderr)
        rows = self.transcript()
        self.assertEqual([d.get('marker') for d in rows], [None, 'chunk_done'])
        self.assertEqual((rows[0]['start'], rows[0]['text']), (1.0, 'hello there'))


if __name__ == '__main__':
    unittest.main()
