"""A scan of a VOD's 2-second fragments against the processed VOD YouTube serves later. All offline.

IShowSpeed's launch VOD plays everything about 144 s later processed than its fragments did, so windows taken from a
fragment scan land early unless the shift is added. Here the "processed VOD" is a local video whose audio is the scan's
audio with SHIFT seconds of other noise in front, served by the fake yt-dlp from test_download_routing:

- timeline.py measures the shift once (two probes far apart), keeps it in run.json and reuses it; it asks nothing for
  a scan that can't be off, gives 0 while the VOD is still fragments, and stops on a weak match or missing audio.
- frame_sheet.py --moments, cut_windows.py --plan and vodwatch.py run fetch the shifted windows; plan_jobs.py moves the
  plan onto a kept shift; batch holds back url jobs nobody has checked; vodwatch.py sets fragment-time rows aside.
- ytfrag.py window leaves no <out>.parts behind when the VOD turns out to be processed.
"""
import contextlib
import glob
import http.server
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from unittest import mock

from test_download_routing import FakeTools, HAVE_FFMPEG, META, SCRIPTS, VID, env, read, serve

import cut_windows  # noqa: E402
import opusclip_api  # noqa: E402
import plan_jobs  # noqa: E402
import timeline  # noqa: E402
import vodwatch  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')
SHIFT = 37.0          # the fake processed VOD plays the scan's times this much later
LENGTH = 1800         # seconds of scan audio (the fixtures' transcript and loudness cover 0-1800 s)
FRAGMENT_RUN = {'stages': {}, 'source': dict(META, title='Test stream', channel='Test Streamer', duration=LENGTH + SHIFT),
                'credit': 'Test Streamer (YouTube)',
                'audio': {'protocol': 'http_dash_segments', 'via': 'ytfrag.py', 'format': '139'}}


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', *args], check=True)


def sections(calls):
    """(start, end) of every yt-dlp --download-sections call, in order."""
    out = []
    for c in calls:
        if '--download-sections' in c:
            a, b = c[c.index('--download-sections') + 1].lstrip('*').split('-')
            out.append((float(a), float(b)))
    return out


@unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
class Timeline(FakeTools):
    @classmethod
    def setUpClass(cls):
        cls.media = tempfile.mkdtemp()
        base = os.path.join(cls.media, 'base.m4a')
        # noise gated on and off at random every 128 ms: a loudness pattern that never repeats
        ffmpeg('-f', 'lavfi', '-i', f"anoisesrc=d={LENGTH}:c=pink:r=8000:a=0.5:seed=11,"
               "volume='0.03+0.97*gt(random(0),0.55)':eval=frame", '-c:a', 'aac', '-b:a', '48k', base)
        cls.scan_audio = os.path.join(cls.media, 'audio.m4a')     # what vodwatch keeps: mono 16 kHz, 32 kbps
        ffmpeg('-i', base, '-ac', '1', '-ar', '16000', '-c:a', 'aac', '-b:a', '32k', '-f', 'ipod', cls.scan_audio)
        cls.other_audio = os.path.join(cls.media, 'other.m4a')    # same kind of sound, unrelated pattern
        ffmpeg('-i', cls.scan_audio, '-af', 'areverse', '-c:a', 'aac', '-b:a', '32k', '-f', 'ipod', cls.other_audio)
        cls.vod = os.path.join(cls.media, 'processed.mp4')       # SHIFT s of other sound, then the same stream
        ffmpeg('-f', 'lavfi', '-i', f'anoisesrc=d={SHIFT}:c=brown:r=8000:a=0.4:seed=5', '-i', base,
               '-f', 'lavfi', '-i', f'testsrc2=size=160x90:rate=2:duration={LENGTH + SHIFT}',
               '-filter_complex', '[0:a][1:a]concat=n=2:v=0:a=1[a]', '-map', '2:v', '-map', '[a]',
               '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '48k',
               '-shortest', cls.vod)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.media, ignore_errors=True)

    def scan(self, run=FRAGMENT_RUN, audio=True, moments=((60.0, 120.0), (690.0, 750.0))):
        """A vodwatch scan folder: run.json, the scan's audio.m4a and a moments.json in scan time."""
        d = self.folder(f'scan{len(os.listdir(self.tmp))}')
        if run is not None:
            with open(os.path.join(d, 'run.json'), 'w') as f:
                json.dump(run, f)
        if audio:
            shutil.copy(self.scan_audio if audio is True else audio, os.path.join(d, 'audio.m4a'))
        with open(os.path.join(d, 'moments.json'), 'w') as f:
            json.dump({'source': {'video_id': VID, 'url': None, 'label': 'Test Streamer (YouTube)'},
                       'moments': [{'rank': i + 1, 'start': a, 'end': b, 'start_hms': timeline.hms(a),
                                    'end_hms': timeline.hms(b), 'label': ''} for i, (a, b) in enumerate(moments)]}, f)
        return d

    def processed(self, **extra):
        return env(FAKE_PROTOCOL='https', FAKE_SOURCE=self.vod, **extra)

    def run_json(self, d):
        with open(os.path.join(d, 'run.json')) as f:
            return json.load(f)

    # -- timeline.py

    def test_the_shift_is_measured_once_at_two_moments_and_kept(self):
        d = self.scan()
        made, real = [], tempfile.mkdtemp

        def mkdtemp(*a, **k):
            made.append(real(*a, **k))
            return made[-1]

        with self.processed(), contextlib.redirect_stderr(io.StringIO()), \
                mock.patch.object(timeline.tempfile, 'mkdtemp', mkdtemp):
            seconds, state = timeline.resolve(d, VID, [90.0, 720.0])
        self.assertEqual(state, 'measured')
        self.assertAlmostEqual(seconds, SHIFT, delta=0.1)
        rec = self.run_json(d)['timeline_offset']
        self.assertEqual(rec['seconds'], seconds)
        self.assertEqual(len(rec['points']), 2, 'a second point 600 s or more away checks for drift')
        for p in rec['points']:
            self.assertGreater(p['r'], 0.9)
            self.assertAlmostEqual(p['shift'], SHIFT, delta=0.1)
        self.assertEqual(sections(self.calls()), [(60.0, 120.0), (690.0, 750.0)], '60 s of the VOD per probe')
        self.assertEqual([c[c.index('-f') + 1] for c in self.calls() if '--download-sections' in c], ['139/ba'] * 2)
        n = len(self.calls())
        with self.processed():
            self.assertEqual(timeline.resolve(d, VID, [90.0]), (seconds, 'measured'))
        self.assertEqual(len(self.calls()), n, 'the kept shift is reused: nothing asked')
        self.assertTrue(made and not any(os.path.exists(p) for p in made), 'the probes are deleted')

    def test_a_probe_that_doesnt_match_is_replaced_by_the_next_moment(self):
        d = self.scan()
        # 0:00:15 sits where the processed VOD still plays its first 37 s, which the scan never had
        with self.processed(), contextlib.redirect_stderr(io.StringIO()):
            seconds, state = timeline.resolve(d, VID, [15.0, 90.0, 720.0, 1500.0])
        self.assertEqual(state, 'measured')
        self.assertAlmostEqual(seconds, SHIFT, delta=0.1)
        self.assertEqual(sections(self.calls()), [(0.0, 60.0), (60.0, 120.0), (690.0, 750.0)])

    def test_a_weak_match_stops_and_keeps_nothing(self):
        d = self.scan(audio=self.other_audio)
        with self.processed(), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                timeline.resolve(d, VID, [90.0, 720.0])
        self.assertIn('could not pin down', str(cm.exception))
        self.assertIn('Scan the VOD again', str(cm.exception))
        self.assertNotIn('timeline_offset', self.run_json(d))

    def test_still_fragments_means_no_shift_yet(self):
        d = self.scan()
        with env(FAKE_PROTOCOL='http_dash_segments'), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(timeline.resolve(d, VID, [90.0]), (0.0, 'pending'))
        self.assertEqual(len(self.calls()), 1, 'only the protocol question')
        self.assertNotIn('timeline_offset', self.run_json(d))

    def test_scans_that_cant_be_off_ask_nothing(self):
        processed = dict(FRAGMENT_RUN, audio={'protocol': 'https', 'via': 'yt-dlp', 'format': '139/ba/w'})
        kept = dict(FRAGMENT_RUN, timeline_offset={'seconds': 12.5})
        with self.processed():
            self.assertEqual(timeline.resolve(self.scan(run=processed), VID, [90.0]), (0.0, 'none'))
            self.assertEqual(timeline.resolve(self.scan(run=kept), VID, [90.0]), (12.5, 'measured'))
            self.assertEqual(timeline.resolve(self.scan(), 'https://www.twitch.tv/videos/123', [90.0]), (0.0, 'none'))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                self.assertEqual(timeline.resolve(self.scan(run=None), VID, [90.0]), (0.0, 'none'))
        self.assertIn('pass --timeline-offset', err.getvalue())
        self.assertEqual(self.calls(), [])

    def test_a_fragment_scan_without_its_audio_stops(self):
        legacy = {k: v for k, v in FRAGMENT_RUN.items() if k != 'audio'}   # a 1.0.0 run.json: no audio record
        for run in (FRAGMENT_RUN, legacy):
            with self.processed(), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    timeline.resolve(self.scan(run=run, audio=False), VID, [90.0])
            self.assertIn('no audio.m4a left', str(cm.exception))

    def test_the_cli_moves_a_plan(self):
        d = self.scan()
        plan = plan_jobs.build_plan([{'rank': 1, 'start': 60.0, 'end': 120.0, 'score': 1, 'label': ''}],
                                    f'https://www.youtube.com/watch?v={VID}', duration=LENGTH + SHIFT)
        plan['timeline'] = {'scan': 'fragments', 'scanDir': d, 'offset': 0.0, 'state': 'unchecked'}
        p = os.path.join(d, 'plan.json')
        with open(p, 'w') as f:
            json.dump(plan, f)
        r = self.run_script('timeline.py', '--plan', p, FAKE_PROTOCOL='https', FAKE_SOURCE=self.vod)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn('VOD time now = scan time + 3', r.stdout)
        with open(p) as f:
            moved = json.load(f)
        job = moved['jobs'][0]
        self.assertEqual(moved['timeline']['state'], 'measured')
        self.assertAlmostEqual(job['start'], 0.0 + SHIFT, delta=0.1)      # 60 s before the moment, clamped at 0
        self.assertEqual((job['scanStart'], job['scanEnd']), (0.0, 165.0))
        self.assertEqual(job['request']['curationPref']['range'],
                         {'startSec': math.floor(job['start']), 'endSec': math.ceil(job['end'])})

    def test_rebasing_is_idempotent_and_leaves_sent_jobs_alone(self):
        plan = plan_jobs.build_plan([{'rank': 1, 'start': 600.0, 'end': 660.0, 'score': 1, 'label': ''},
                                     {'rank': 2, 'start': 3000.0, 'end': 3060.0, 'score': 1, 'label': ''}],
                                    f'https://www.youtube.com/watch?v={VID}', duration=85542, title_prefix='Test')
        plan['jobs'][1]['projectId'] = 'P1'
        self.assertEqual(timeline.rebase_plan(plan, 144.0, 'given'), 1)
        once = json.dumps(plan, sort_keys=True)
        self.assertEqual(timeline.rebase_plan(plan, 144.0, 'given'), 0)
        self.assertEqual(json.dumps(plan, sort_keys=True), once)
        first, sent = plan['jobs']
        self.assertEqual((first['start'], first['end'], first['scanStart']), (684.0, 849.0, 540.0))
        self.assertEqual((first['cut']['start'], first['cut']['end']), (684.0, 849.0))
        self.assertEqual(first['request']['uploadedVideoAttr']['title'], 'Test 0:11:24-0:14:09')
        self.assertEqual((sent['start'], sent['end'], sent['scanStart']), (2940.0, 3105.0, 2940.0), 'a sent job stays')
        self.assertEqual(plan['timeline'], {'offset': 144.0, 'state': 'given'})

    # -- the scripts that fetch windows

    def test_frame_sheets_of_moments_fetch_the_shifted_windows(self):
        d = self.scan()
        out = os.path.join(d, 'sheets')
        r = self.run_script('frame_sheet.py', '--moments', os.path.join(d, 'moments.json'), '--source', VID,
                            '--top', '1', '--out-dir', out, FAKE_PROTOCOL='https', FAKE_SOURCE=self.vod)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertIn('as served now', r.stdout)
        probes, window = sections(self.calls())[:2], sections(self.calls())[2]
        self.assertEqual(len(probes), 2)
        self.assertAlmostEqual(window[0], 60.0 + SHIFT, delta=0.1)
        self.assertAlmostEqual(window[1], 120.0 + SHIFT, delta=0.1)
        with open(glob.glob(os.path.join(out, '01_*.json'))[0]) as f:
            meta = json.load(f)
        self.assertAlmostEqual(meta['timeline_offset'], SHIFT, delta=0.1)
        self.assertEqual(meta['frames'][0]['stream_hms'], '0:01:02', 'tiles keep the scan\'s times')
        self.assertIn('timeline_offset', self.run_json(d))

    def test_an_explicit_offset_needs_no_scan_folder(self):
        sheet = os.path.join(self.tmp, 'manual.jpg')
        r = self.run_script('frame_sheet.py', '--source', VID, '--start', '60', '--end', '66', '--out', sheet,
                            '--timeline-offset', '37', FAKE_PROTOCOL='https', FAKE_SOURCE=self.vod)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        cut = os.path.join(self.tmp, 'w.mp4')
        r = self.run_script('cut_windows.py', '--source', VID, '--start', '60', '--end', '63', '--out', cut,
                            '--timeline-offset', '37', '--keep-fps', FAKE_PROTOCOL='https', FAKE_SOURCE=self.vod)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        self.assertEqual(json.loads(r.stdout)['timeline_offset'], 37.0)
        self.assertEqual(sections(self.calls()), [(97.0, 103.0), (97.0, 100.0)])
        r = self.run_script('frame_sheet.py', '--video', cut, '--timeline-offset', '37')
        self.assertEqual(r.returncode, 2)
        self.assertIn('--timeline-offset is for --source and --moments', r.stderr)

    def test_cut_windows_moves_an_unchecked_plan_before_it_cuts(self):
        d = self.scan()
        p = os.path.join(d, 'plan.json')
        r = self.run_script('plan_jobs.py', os.path.join(d, 'moments.json'), '--source-duration', '40000', '--top', '1',
                            '--out', p, '--cut-dir', os.path.join(d, 'cuts'))
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(p) as f:
            plan = json.load(f)
        self.assertEqual((plan['route'], plan['timeline']['state']), ('upload', 'unchecked'))
        self.assertIn('nobody has compared', plan['notes'][0])
        r = self.run_script('cut_windows.py', '--plan', p, '--height', '360', '--keep-fps', FAKE_PROTOCOL='https',
                            FAKE_SOURCE=self.vod)
        self.assertEqual(r.returncode, 0, r.stderr[-1500:])
        with open(p) as f:
            plan = json.load(f)
        job = plan['jobs'][0]
        self.assertEqual(plan['timeline']['state'], 'measured')
        self.assertAlmostEqual(job['cut']['start'], job['scanStart'] + SHIFT, delta=0.1)
        cut = sections(self.calls())[-1]
        self.assertAlmostEqual(cut[0], job['scanStart'] + SHIFT, delta=0.1)
        self.assertAlmostEqual(cut[1], job['scanEnd'] + SHIFT, delta=0.1)
        self.assertTrue(os.path.isfile(job['file']))

    def test_plan_jobs_moves_the_windows_by_a_kept_shift(self):
        d = self.scan(run=dict(FRAGMENT_RUN, timeline_offset={'seconds': 37.0}))
        p = os.path.join(d, 'plan.json')
        r = self.run_script('plan_jobs.py', os.path.join(d, 'moments.json'), '--source-duration', '3600', '--top', '1',
                            '--out', p)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(p) as f:
            plan = json.load(f)
        job = plan['jobs'][0]
        self.assertEqual((plan['route'], plan['timeline']['state'], plan['timeline']['offset']), ('url', 'measured', 37.0))
        self.assertEqual((job['start'], job['end'], job['scanStart'], job['scanEnd']), (37.0, 202.0, 0.0, 165.0))
        self.assertEqual(job['request']['curationPref']['range'], {'startSec': 37, 'endSec': 202})
        self.assertEqual(self.calls(), [], 'plan_jobs.py never asks YouTube')
        r = self.run_script('plan_jobs.py', os.path.join(d, 'moments.json'), '--source-duration', '3600', '--top', '1',
                            '--out', p, '--timeline-offset', '0')
        with open(p) as f:
            plan = json.load(f)
        self.assertEqual((plan['timeline']['state'], plan['jobs'][0]['start']), ('given', 0.0))

    def test_batch_holds_back_url_jobs_nobody_has_checked(self):
        d = self.scan()
        p = os.path.join(d, 'plan.json')
        self.assertEqual(self.run_script('plan_jobs.py', os.path.join(d, 'moments.json'), '--source-duration', '3600',
                                         '--top', '1', '--out', p).returncode, 0)

        def never(*a, **k):
            raise AssertionError('nothing may be sent')

        client = opusclip_api.OpusClip(api_key='test-key', transport=never, sleep=lambda s: None, min_interval=0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(opusclip_api.main(['batch', p, '--yes'], client), opusclip_api.EXIT_USAGE)
        self.assertIn(f'timeline.py --plan {p}', err.getvalue())
        out, err = io.StringIO(), io.StringIO()
        dry = opusclip_api.OpusClip(dry_run=True, sleep=lambda s: None, min_interval=0, out=out, err=err)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(opusclip_api.main(['--dry-run', 'batch', p], dry), opusclip_api.EXIT_OK)
        self.assertIn('nobody has compared', err.getvalue())
        self.assertIn('"startSec": 0', out.getvalue(), 'the dry run still prints the request')

    # -- vodwatch.py

    def test_vodwatch_run_adds_the_shift_to_sheets_and_plan(self):
        d = self.scan(moments=())
        for f in ('transcript.jsonl', 'loudness.tsv'):
            shutil.copy(os.path.join(FIX, f), d)
        model = os.path.join(self.tmp, 'model.bin')
        open(model, 'wb').close()
        r = self.run_script('vodwatch.py', 'run', VID, '--out', d, '--sheets', '1', '--clips', '1', '--model', model,
                            FAKE_PROTOCOL='https', FAKE_SOURCE=self.vod, FAKE_DURATION=str(LENGTH + SHIFT))
        self.assertEqual(r.returncode, 0, r.stderr[-2500:])
        run = self.run_json(d)
        self.assertEqual(run['audio']['via'], 'ytfrag.py', 'the scan audio was kept, not fetched again')
        seconds = run['timeline_offset']['seconds']
        self.assertAlmostEqual(seconds, SHIFT, delta=0.1)
        with open(os.path.join(d, 'moments.json')) as f:
            top = json.load(f)['moments'][0]
        window = sections(self.calls())[-1]       # the frame sheet's window, after the two probes
        self.assertAlmostEqual(window[0], top['start'] + seconds, delta=0.02)
        with open(os.path.join(d, 'plan.json')) as f:
            plan = json.load(f)
        self.assertEqual((plan['route'], plan['timeline']['state']), ('url', 'measured'))
        self.assertAlmostEqual(plan['jobs'][0]['start'] - plan['jobs'][0]['scanStart'], seconds, delta=0.01)
        report = read(os.path.join(d, 'report.md'))
        self.assertRegex(report, r'- Timeline: VOD time = scan time \+ 3[67]\.\d\d s')

    def test_a_switch_to_ytdlp_sets_fragment_rows_aside(self):
        src = os.path.join(self.tmp, 'src.m4a')
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:duration=4', '-c:a', 'aac', src)
        for before, kept in (({}, False), ({'protocol': 'http_dash_segments', 'via': 'ytfrag.py', 'format': '139'}, False),
                             ({'protocol': 'https', 'via': 'yt-dlp', 'format': '139/ba/w'}, True)):
            out = self.folder(f'scan-{len(os.listdir(self.tmp))}')
            for f in ('transcript.jsonl', 'loudness.tsv'):
                shutil.copy(os.path.join(FIX, f), out)
            with env(FAKE_PROTOCOL='https', FAKE_SOURCE=src), contextlib.redirect_stderr(io.StringIO()):
                vodwatch.get_audio(dict(META), out, info=dict(before))
            names = sorted(os.listdir(out))
            if kept:
                self.assertEqual(names, ['audio.m4a', 'loudness.tsv', 'transcript.jsonl'], before)
            else:
                self.assertEqual(names, ['audio.m4a', 'loudness.fragment-timeline.tsv',
                                         'transcript.fragment-timeline.jsonl'], before)

    def test_the_report_says_when_the_timeline_is_unchecked(self):
        d = self.scan(moments=())
        vodwatch.write_report(d, dict(FRAGMENT_RUN))
        self.assertIn('- Timeline: scanned from 2-second fragments', read(os.path.join(d, 'report.md')))
        vodwatch.write_report(d, dict(FRAGMENT_RUN, audio={'protocol': 'https', 'via': 'yt-dlp', 'format': '139/ba/w'}))
        self.assertNotIn('- Timeline:', read(os.path.join(d, 'report.md')))


@unittest.skipUnless(HAVE_FFMPEG, 'needs ffmpeg')
class WindowCleanup(FakeTools):
    def test_a_whole_file_audio_reply_leaves_no_parts_folder(self):
        video = os.path.join(self.tmp, 'frag.mp4')
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10:duration=6', '-c:v', 'libx264', '-preset', 'ultrafast',
               '-pix_fmt', 'yuv420p', '-movflags', 'frag_keyframe+empty_moov+default_base_moof', '-f', 'mp4', video)
        with open(video, 'rb') as f:
            data = f.read()
        step = -(-len(data) // 4)
        parts = [data[i * step:(i + 1) * step] for i in range(4)]

        class Server(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                sq = int(q['sq'][0])
                if q['itag'][0] == '140':          # the audio came back as the whole processed file
                    self.send_response(200)
                    self.send_header('Content-Length', '521615154')
                    self.end_headers()
                    try:
                        self.wfile.write(b'\0' * 65536)
                    except OSError:
                        pass
                    return
                if sq >= len(parts):
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header('Content-Length', str(len(parts[sq])))
                self.end_headers()
                self.wfile.write(parts[sq])

            def log_message(self, *a):
                pass

        src = os.path.join(self.tmp, 'src.mp4')
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=10:duration=6', '-f', 'lavfi', '-i',
               'sine=frequency=440:duration=6', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
               '-c:a', 'aac', '-shortest', src)
        out = os.path.join(self.tmp, 'w.mp4')
        with serve(Server) as base, env(FAKE_PROTOCOL='http_dash_segments', FAKE_FRAG_BASE=base + '/videoplayback',
                                        FAKE_SOURCE=src), contextlib.redirect_stderr(io.StringIO()):
            how = cut_windows.cut_youtube(VID, 0.0, 2.0, out)
        self.assertEqual(how, ('yt-dlp', None))
        self.assertTrue(os.path.isfile(out))
        self.assertFalse(os.path.exists(out + '.parts'), 'the video fragments that did come are removed too')
        self.assertFalse(os.path.exists(out + '.part'))

    def test_fragments_left_from_before_processing_are_removed(self):
        out = os.path.join(self.tmp, 'w.mp4')
        os.makedirs(os.path.join(out + '.parts', 'v.mp4.frags299'))
        open(os.path.join(out + '.parts', 'v.mp4.frags299', '0000001'), 'wb').close()
        r = self.run_script('ytfrag.py', 'window', VID, '10', '20', out, FAKE_PROTOCOL='https')
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertFalse(os.path.exists(out + '.parts'))


if __name__ == '__main__':
    unittest.main()
