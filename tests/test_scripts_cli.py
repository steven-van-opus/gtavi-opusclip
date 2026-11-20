"""Smoke tests: every script's CLI runs, offline. Media tests need ffmpeg and skip without it."""
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.normpath(os.path.join(HERE, '..', 'scripts'))
FIX = os.path.join(HERE, 'fixtures')
PY_SCRIPTS = sorted(f for f in os.listdir(SCRIPTS) if f.endswith('.py'))
HAVE_FFMPEG = bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


def run(script, *args, stdin=None, cwd=None, env=None):
    return subprocess.run([sys.executable, '-I', os.path.join(SCRIPTS, script), *args], input=stdin,
                          capture_output=True, text=True, cwd=cwd, env=env, timeout=300)


def read_json(path):
    with open(path) as f:
        return json.load(f)


def write_json(path, obj):
    with open(path, 'w') as f:
        json.dump(obj, f)


def read_text(path):
    with open(path) as f:
        return f.read()


def make_video(path, seconds=6, size='320x568', rate='30', audio=True):
    cmd = ['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', f'testsrc2=size={size}:rate={rate}:duration={seconds}']
    if audio:
        cmd += ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}', '-c:a', 'aac', '-shortest']
    subprocess.run(cmd + ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', path], check=True)


class TestHelp(unittest.TestCase):
    def test_every_python_script_has_help(self):
        self.assertGreaterEqual(len(PY_SCRIPTS), 17)
        for s in PY_SCRIPTS:
            with self.subTest(script=s):
                r = run(s, '--help')
                self.assertEqual(r.returncode, 0, f'{s}: {r.stderr[-400:]}')
                self.assertGreater(len(r.stdout), 80, s)

    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_render_help(self):
        r = subprocess.run(['node', os.path.join(SCRIPTS, 'render.mjs'), '--help'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('still', r.stdout)


class TestOfflineCommands(unittest.TestCase):
    def test_doctor_json_never_leaks_the_key(self):
        env = dict(os.environ, OPUSCLIP_API_KEY='sk-should-never-print')
        r = run('doctor.py', '--json', env=env)
        self.assertIn(r.returncode, (0, 1))
        data = json.loads(r.stdout)
        self.assertTrue(any(c['check'].startswith('OPUSCLIP_API_KEY') and c['detail'] == 'set' for c in data))
        self.assertNotIn('sk-should-never-print', r.stdout + r.stderr)

    def test_guard_asks_for_publishing_and_spending(self):
        def bash(cmd):
            return {'tool_name': 'Bash', 'tool_input': {'command': cmd}}
        ask = [
            {'tool_name': 'mcp__plugin_gtavi-opusclip_opusclip__opusclip_create_post_task', 'tool_input': {'clipId': 'C'}},
            {'tool_name': 'mcp__opusclip__opusclip_schedule_publish', 'tool_input': {}},
            {'tool_name': 'mcp__plugin_opusclip_opusclip__opusclip_submit_project', 'tool_input': {'videoUrl': 'U'}},
            # tools that aren't on the documented read-only list ask too (media generation names aren't documented)
            {'tool_name': 'mcp__opusclip__opusclip_generate_music', 'tool_input': {}},
            {'tool_name': 'mcp__opusclip__opusclip_create_upload_link', 'tool_input': {}},
            bash('python3 -I scripts/opusclip_api.py batch plan.json --yes'),
            bash('python3 -I opusclip_api.py post P C --account a --title t --yes'),
            # a dry run chained in front of a real run must not hide the real run
            bash('python3 -I scripts/opusclip_api.py --dry-run batch plan.json && '
                 'python3 -I scripts/opusclip_api.py batch plan.json --yes --wait'),
            bash('python3 -I scripts/opusclip_api.py --dry-run batch p.json; python3 -I scripts/opusclip_api.py censor P C --yes'),
            # a --dry-run in a comment, or after the subcommand, doesn't count
            bash('python3 -I scripts/opusclip_api.py batch plan.json --yes --wait # --dry-run'),
            bash('python3 -I scripts/opusclip_api.py batch plan.json --yes --dry-run'),
            bash('python3 -I "$S/opusclip_api.py" --json --org-id org_1 \\\n  schedule P C --account a --title t --at x --yes'),
            bash("bash -c 'python3 opusclip_api.py --dry-run batch p; python3 opusclip_api.py batch p --yes'"),
        ]
        quiet = [
            {'tool_name': 'mcp__opusclip__opusclip_list_clips', 'tool_input': {}},
            {'tool_name': 'mcp__plugin_gtavi-opusclip_opusclip__opusclip_analyze_video', 'tool_input': {}},
            bash('python3 -I scripts/opusclip_api.py --dry-run batch plan.json'),
            bash('python3 -I scripts/opusclip_api.py --dry-run batch plan.json   # next: opusclip_api.py batch --yes'),
            bash('python3 -I scripts/opusclip_api.py clips P1'),
            bash('python3 -I scripts/opusclip_api.py censor-status JOB1'),   # read-only, not "censor"
            bash('python3 -I scripts/opusclip_api.py download P1 --out clips/post'),
            bash('# python3 -I scripts/opusclip_api.py batch plan.json --yes\nls'),
            bash('ls -la'),
            {'tool_name': 'Read', 'tool_input': {'file_path': 'x'}},
        ]
        for ev in ask:
            r = run('guard.py', stdin=json.dumps(ev))
            self.assertTrue(r.stdout.strip(), ev)
            out = json.loads(r.stdout)['hookSpecificOutput']
            self.assertEqual(out['permissionDecision'], 'ask', ev)
            self.assertEqual(out['hookEventName'], 'PreToolUse')
        for ev in quiet:
            r = run('guard.py', stdin=json.dumps(ev))
            self.assertEqual(r.stdout.strip(), '', ev)
            self.assertEqual(r.returncode, 0)
        r = run('guard.py', stdin=json.dumps(bash('python3 -I scripts/opusclip_api.py censor-status J')))
        self.assertNotIn('spends', r.stdout)

    def test_guard_knows_every_opusclip_api_subcommand(self):
        sys.path.insert(0, SCRIPTS)
        try:
            import guard
            import opusclip_api
        finally:
            sys.path.remove(SCRIPTS)
        sub = next(a for a in opusclip_api.build_parser()._actions if a.__class__.__name__ == '_SubParsersAction')
        self.assertEqual(set(sub.choices), guard.SUBCOMMANDS)

    def test_copy_kit_rules(self):
        r = run('copy_kit.py', '--hook', 'IShowSpeed tried to hug the Red Hulk', '--streamer', 'IShowSpeed',
                '--source-platform', 'YouTube', '--tags', 'gta6,ishowspeed', '--json')
        d = json.loads(r.stdout)
        self.assertEqual(d['credit'], '🎥 IShowSpeed (YouTube)')
        for plat, p in d['posts'].items():
            text = p['text'] if isinstance(p['text'], str) else p['text']['description']
            self.assertIn('🎥 IShowSpeed (YouTube)', text, plat)
            self.assertEqual(p['warnings'], [], plat)
        r = run('copy_kit.py', '--hook', 'OFFICIAL clip, get rich while you sleep', '--streamer', 'Kai Cenat',
                '--credit', 'twitch.tv/KaiCenat', '--campaign-tag', '#KAI', '--paid', '--json')
        d = json.loads(r.stdout)
        tik = d['posts']['tiktok']
        self.assertTrue(tik['text'].startswith('#ad'))
        self.assertTrue(any('official' in w.lower() for w in tik['warnings']))
        self.assertTrue(any('earnings' in w for w in tik['warnings']))
        long = run('copy_kit.py', '--hook', 'x' * 300, '--streamer', 'S', '--json')
        self.assertTrue(any('280' in w for w in json.loads(long.stdout)['posts']['x']['warnings']))

    def test_copy_kit_paid_post_passes_its_own_checks(self):
        # Shorts puts #ad first in the description (the title is the hook); that must not be flagged
        for extra in ([], ['--extra', 'his first time in Vice City']):
            r = run('copy_kit.py', '--hook', 'x', '--streamer', 'y', '--paid', *extra)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertNotIn('#ad must come first', r.stdout)
            j = run('copy_kit.py', '--hook', 'x', '--streamer', 'y', '--paid', *extra, '--json')
            self.assertEqual(j.returncode, 0)
            shorts = json.loads(j.stdout)['posts']['shorts']
            self.assertTrue(shorts['text']['description'].startswith('#ad'))
            self.assertEqual(shorts['warnings'], [])
        # --json and text mode give the same exit code when there are warnings
        bad = ('--hook', 'OFFICIAL clip', '--streamer', 'y')
        self.assertEqual(run('copy_kit.py', *bad).returncode, 1)
        self.assertEqual(run('copy_kit.py', *bad, '--json').returncode, 1)

    def test_post_calendar(self):
        with tempfile.TemporaryDirectory() as d:
            clips = [{'file': f'0{i}_clip.mp4', 'title': f'Clip {i}', 'projectId': 'P1', 'clipId': f'C{i}'} for i in range(1, 6)]
            write_json(os.path.join(d, 'clips.json'), clips)
            out = os.path.join(d, 'cal.csv')
            r = run('post_calendar.py', '--clips', os.path.join(d, 'clips.json'), '--start', '2026-11-20', '--days', '2',
                    '--tz', 'America/New_York', '--per-day', 'tiktok=3,x=20', '--credit', 'IShowSpeed (YouTube)', '--out', out)
            self.assertEqual(r.returncode, 0, r.stderr)
            with open(out) as fh:
                rows = list(csv.DictReader(fh))
        self.assertEqual(sum(1 for x in rows if x['platform'] == 'tiktok'), 5)
        self.assertEqual(sum(1 for x in rows if x['platform'] == 'x'), 5)
        self.assertTrue(all(x['utc'].endswith('Z') and x['credit'] == 'IShowSpeed (YouTube)' for x in rows))
        self.assertIn('2026-11-20', rows[0]['date'])

    def test_scoreboard_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, 'sb.csv')
            for i, (plat, streamer, hook, views) in enumerate([
                    ('tiktok', 'IShowSpeed', 'Speed tried to hug the Red Hulk', 90000),
                    ('tiktok', 'IShowSpeed', 'Speed tried to pet an alligator', 70000),
                    ('tiktok', 'IShowSpeed', 'Speed tried to fly a plane', 50000),
                    ('tiktok', 'Kai Cenat', 'Kai drives around', 9000),
                    ('tiktok', 'Kai Cenat', 'Kai at the store', 8000),
                    ('tiktok', 'Kai Cenat', 'Kai checks the map', 7000)]):
                url = f'https://example.test/{i}'
                self.assertEqual(run('scoreboard.py', '--file', f, 'add', '--platform', plat, '--url', url,
                                     '--streamer', streamer, '--hook', hook, '--length', '30').returncode, 0)
                self.assertEqual(run('scoreboard.py', '--file', f, 'update', url, '--views', str(views)).returncode, 0)
            self.assertEqual(run('scoreboard.py', '--file', f, 'add', '--platform', 'x', '--url',
                                 'https://example.test/0').returncode, 1, 'duplicate URL must be refused')
            rep = json.loads(run('scoreboard.py', '--file', f, 'report', '--json').stdout)
        self.assertEqual(rep['posts'], 6)
        self.assertEqual(rep['groups']['streamer'][0]['streamer'], 'IShowSpeed')
        self.assertTrue(any('streamer "IShowSpeed"' in l for l in rep['next']))

    def test_campaign_math_cli(self):
        r = run('campaign_math.py', 'cpm', '--cpm', '1', '--min-payout', '10', '--cap', '500', '--fee', '0.10',
                '--dist', '770x3000,30x50000,10x1000000')
        self.assertEqual(r.returncode, 0)
        self.assertIn('$5,850.00', r.stdout)
        self.assertIn('not a promise', r.stdout)

    def test_find_streams_from_saved_json(self):
        r = run('find_streams.py', '--from-json', os.path.join(FIX, 'ytsearch_flat.json'), '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        res = {x['url']: x for x in json.loads(r.stdout)['results']}
        self.assertEqual(res['https://www.youtube.com/watch?v=LIVE0000001']['status'], 'LIVE')
        self.assertIn('VOD >10h', res['https://www.youtube.com/watch?v=LONG0000001']['next'])
        self.assertIn('VOD <=10h', res['https://www.youtube.com/watch?v=SHRT0000001']['next'])
        self.assertEqual(res['https://www.youtube.com/watch?v=CLIP0000001']['status'], 'VIDEO')

    def test_rank_and_plan_cli(self):
        with tempfile.TemporaryDirectory() as d:
            r = run('rank_moments.py', '--transcript', os.path.join(FIX, 'transcript.jsonl'), '--loudness',
                    os.path.join(FIX, 'loudness.tsv'), '--out-dir', d, '--top', '3', '--video-id', 'abcdefghijk',
                    '--label', 'Test Streamer (YouTube)')
            self.assertEqual(r.returncode, 0, r.stderr)
            m = read_json(os.path.join(d, 'moments.json'))
            self.assertTrue(m['moments'][0]['link'].startswith('https://www.youtube.com/watch?v=abcdefghijk&t='))
            self.assertTrue(os.path.exists(os.path.join(d, 'moments.md')))
            p = run('plan_jobs.py', os.path.join(d, 'moments.json'), '--source-duration', '1800', '--top', '2',
                    '--out', os.path.join(d, 'plan.json'))
            self.assertEqual(p.returncode, 0, p.stderr)
            plan = read_json(os.path.join(d, 'plan.json'))
            self.assertEqual(plan['route'], 'url')
            self.assertEqual(plan['credit'], 'Test Streamer (YouTube)')
            b = run('opusclip_api.py', '--dry-run', 'batch', os.path.join(d, 'plan.json'))
            self.assertEqual(b.returncode, 0, b.stderr)
            self.assertIn('POST https://api.opus.pro/api/clip-projects', b.stdout)

    def test_vodwatch_rank_and_report_offline(self):
        with tempfile.TemporaryDirectory() as d:
            shutil.copy(os.path.join(FIX, 'transcript.jsonl'), d)
            shutil.copy(os.path.join(FIX, 'loudness.tsv'), d)
            write_json(os.path.join(d, 'run.json'),
                       {'stages': {}, 'source': {'kind': 'youtube', 'url': 'https://www.youtube.com/watch?v=abcdefghijk',
                                                 'id': 'abcdefghijk', 'title': 'Test stream', 'channel': 'Test Streamer',
                                                 'duration': 1800}, 'credit': 'Test Streamer (YouTube)'})
            r = run('vodwatch.py', 'rank', d, '--clips', '2', '--candidates', '4')
            self.assertEqual(r.returncode, 0, r.stderr[-800:])
            report = read_text(os.path.join(d, 'report.md'))
        self.assertIn('Hours scanned: 0.5', report)
        self.assertIn('Compute cost: $0', report)
        self.assertIn('OpusClip plan (not sent)', report)


@unittest.skipUnless(HAVE_FFMPEG, 'ffmpeg not installed')
class TestMedia(unittest.TestCase):
    def test_cut_local_normalizes_fractional_fps(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'src.mp4')
            make_video(src, seconds=8, rate='60000/1001')
            out = os.path.join(d, 'cut.mp4')
            r = run('cut_windows.py', '--source', src, '--start', '2', '--end', '6', '--out', out)
            self.assertEqual(r.returncode, 0, r.stderr)
            info = json.loads(r.stdout)
            self.assertEqual(info['fps'], '60/1')
            dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', out],
                                       capture_output=True, text=True).stdout)
            self.assertAlmostEqual(dur, 4.0, delta=0.25)

    def test_track_assembly_is_all_or_nothing(self):
        sys.path.insert(0, SCRIPTS)
        try:
            import ytfrag
        finally:
            sys.path.remove(SCRIPTS)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'src.mp4')            # a fragmented MP4 audio track, like YouTube's 2 s fragments
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=6',
                            '-c:a', 'aac', '-movflags', 'frag_keyframe+empty_moov', '-f', 'mp4', src], check=True)
            data = open(src, 'rb').read()
            good, bad = os.path.join(d, 'good.frags139'), os.path.join(d, 'bad.frags139')
            os.makedirs(good)
            os.makedirs(bad)
            third = len(data) // 3
            for i, piece in enumerate((data[:third], data[third:2 * third], data[2 * third:])):
                with open(os.path.join(good, f'{i:07d}'), 'wb') as w:
                    w.write(piece)
                with open(os.path.join(bad, f'{i:07d}'), 'wb') as w:
                    w.write(b'not a video ' * 50)
            out = os.path.join(d, 'audio.m4a')
            ytfrag.assemble_track(good, 3, out)
            self.assertTrue(os.path.getsize(out) > 0)
            self.assertFalse(os.path.exists(out + '.part'))
            self.assertFalse(os.path.exists(good), 'fragments are deleted once the track is complete')
            broken = os.path.join(d, 'broken.m4a')
            with self.assertRaises(SystemExit):
                ytfrag.assemble_track(bad, 3, broken)
            self.assertFalse(os.path.exists(broken), 'a failed assembly must not leave a track behind')
            self.assertFalse(os.path.exists(broken + '.part'))
            self.assertEqual(len(os.listdir(bad)), 3, 'fragments are kept for the next try')

    def test_vodwatch_audio_from_a_file_is_written_atomically(self):
        sys.path.insert(0, SCRIPTS)
        try:
            import vodwatch
        finally:
            sys.path.remove(SCRIPTS)
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'stream.mp4')
            make_video(src, seconds=4)
            out = os.path.join(d, 'scan')
            os.makedirs(out)
            dest = vodwatch.get_audio({'kind': 'file', 'url': src}, out)
            self.assertEqual(dest, os.path.join(out, 'audio.m4a'))
            self.assertTrue(os.path.getsize(dest) > 0)
            self.assertEqual(os.listdir(out), ['audio.m4a'])
            junk = os.path.join(d, 'junk.mp4')
            with open(junk, 'wb') as w:
                w.write(b'\0' * 1000)
            out2 = os.path.join(d, 'scan2')
            os.makedirs(out2)
            with self.assertRaises(subprocess.CalledProcessError):
                vodwatch.get_audio({'kind': 'file', 'url': junk}, out2)
            self.assertEqual(os.listdir(out2), [], 'no audio.m4a (or .part) after a failed conversion')

    def test_frame_sheet(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, 'src.mp4')
            make_video(src, seconds=6, audio=False)
            out = os.path.join(d, 'sheet.jpg')
            r = run('frame_sheet.py', '--video', src, '--out', out, '--offset', '3600')
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = read_json(os.path.join(d, 'sheet.json'))
            self.assertEqual(len(meta['frames']), 12)
            self.assertEqual(meta['frames'][0]['stream_hms'], '1:00:00')
            self.assertGreater(os.path.getsize(out), 1000)

    def test_countdown_dry_run(self):
        with tempfile.TemporaryDirectory() as d:
            items = []
            for i in range(5):
                p = os.path.join(d, f'c{i}.mp4')
                make_video(p, seconds=4)
                items += ['--item', f'{p}|MOMENT {5 - i}|IShowSpeed (YouTube)|0|3']
            r = run('countdown.py', *items, '--out', os.path.join(d, 'cd.mp4'), '--dry-run')
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = json.loads(r.stdout[r.stdout.index('{'):])
        self.assertEqual([s['segment'] for s in meta['segments']], ['cold', 'n5', 'n4', 'n3', 'n2', 'n1'])
        self.assertAlmostEqual(meta['duration_s'], 2.5 + 5 * 3, delta=0.01)
        self.assertEqual(meta['credits'], ['🎥 IShowSpeed (YouTube)'])


@unittest.skipUnless(os.environ.get('WHISPER_MODEL') and shutil.which('whisper-cli') and HAVE_FFMPEG,
                     'set WHISPER_MODEL (and install whisper-cli) to run the real transcription smoke test')
class TestWhisper(unittest.TestCase):
    def test_scan_vod_on_a_tone(self):
        with tempfile.TemporaryDirectory() as d:
            audio = os.path.join(d, 'a.m4a')
            subprocess.run(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=300:duration=12',
                            '-c:a', 'aac', audio], check=True)
            r = run('scan_vod.py', audio, d)
            self.assertEqual(r.returncode, 0, r.stderr)
            for f in ('loudness.tsv', 'transcript.jsonl', 'windows.tsv', 'top_windows.md'):
                self.assertTrue(os.path.exists(os.path.join(d, f)), f)


if __name__ == '__main__':
    unittest.main()
