"""Unit tests for scripts/opusclip_api.py with a mocked HTTP layer (no network, no credits)."""
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))

import opusclip_api as oc  # noqa: E402

KEY = 'sk-test-SECRET-do-not-print'


class FakeTransport:
    """Records every request and answers from a queue of (status, headers, body)."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = list(answers or [])

    def __call__(self, method, url, headers, data=None, timeout=60):
        self.calls.append({'method': method, 'url': url, 'headers': dict(headers),
                           'body': json.loads(data) if data else None})
        if not self.answers:
            return oc.Response(200, {}, b'{}')
        status, hdrs, body = self.answers.pop(0)
        return oc.Response(status, hdrs, json.dumps(body).encode() if not isinstance(body, bytes) else body)


def client(answers=None, **kw):
    t = FakeTransport(answers)
    sleeps = []
    c = oc.OpusClip(api_key=KEY, transport=t, sleep=sleeps.append, min_interval=0, out=io.StringIO(),
                    err=io.StringIO(), **kw)
    return c, t, sleeps


def run_main(argv, c=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = oc.main(argv, client=c)
    return code, out.getvalue(), err.getvalue()


class TestPureHelpers(unittest.TestCase):
    def test_parse_time(self):
        self.assertEqual(oc.parse_time('1:02:03'), 3723)
        self.assertEqual(oc.parse_time('62:03'), 3723)
        self.assertEqual(oc.parse_time('90.5'), 90.5)
        self.assertIsNone(oc.parse_time(None))

    def test_estimate_credits_minimum_and_rounding(self):
        self.assertEqual(oc.estimate_credits(30), 10)          # under 1 min rounds up to 1, API minimum 10
        self.assertEqual(oc.estimate_credits(9.9 * 60), 10)
        self.assertEqual(oc.estimate_credits(25.7 * 60), 25)   # partial minutes round down
        self.assertIsNone(oc.estimate_credits(None))

    def test_parse_durations(self):
        d, odd = oc.parse_durations('0-30,30-60')
        self.assertEqual(d, [[0, 30], [30, 60]])
        self.assertEqual(odd, [])
        d, odd = oc.parse_durations('0-60')
        self.assertEqual(odd, ['0-60'])
        with self.assertRaises(oc.UsageError):
            oc.parse_durations('60-30')

    def test_bare_clip_id(self):
        self.assertEqual(oc.bare_clip_id('P0000000demo.CUexample1'), 'CUexample1')
        self.assertEqual(oc.bare_clip_id('CUexample1'), 'CUexample1')

    def test_unwrap(self):
        self.assertEqual(oc.unwrap({'data': {'jobId': 'J'}}), {'jobId': 'J'})
        self.assertEqual(oc.unwrap({'id': 'P', 'stage': 'QUEUED'}), {'id': 'P', 'stage': 'QUEUED'})
        self.assertEqual(oc.unwrap([1, 2]), [1, 2])

    def test_verify_webhook(self):
        raw, secret, salt = b'{"projectId":"P1"}', 'whsec', 'a1b2c3d4e5f60718'
        import hashlib
        import hmac
        sig = hmac.new(secret.encode(), raw + salt.encode(), hashlib.sha256).hexdigest()
        h = {'X-Opus-Signature': sig, 'X-Opus-Salt': salt, 'X-Opus-Timestamp': '1000'}
        seen = set()
        self.assertTrue(oc.verify_webhook(raw, h, secret, seen, now=1000))
        self.assertFalse(oc.verify_webhook(raw, h, secret, seen, now=1000), 'replayed salt must fail')
        self.assertFalse(oc.verify_webhook(raw + b' ', {**h, 'X-Opus-Salt': 'ffff'}, secret, set(), now=1000))
        self.assertFalse(oc.verify_webhook(raw, h, secret, set(), now=2000), 'stale timestamp must fail')


class TestProjectBody(unittest.TestCase):
    def test_documented_fields(self):
        b = oc.build_project_body('https://www.youtube.com/watch?v=VOD', title='Speed 4:11', template='preset-fancy-Gameplay',
                                  prompt='the alligator attack', start=15090.4, end=15690.2)
        self.assertEqual(b['videoUrl'], 'https://www.youtube.com/watch?v=VOD')
        self.assertEqual(b['uploadedVideoAttr'], {'title': 'Speed 4:11'})
        self.assertEqual(b['brandTemplateId'], 'preset-fancy-Gameplay')
        cur = b['curationPref']
        self.assertEqual(cur['model'], 'ClipAnything')
        self.assertEqual(cur['genre'], 'Gaming')
        self.assertEqual(cur['clipDurations'], [[0, 30], [30, 60]])
        self.assertEqual(cur['customPrompt'], 'the alligator attack')
        self.assertEqual(cur['range'], {'startSec': 15090, 'endSec': 15691})
        self.assertEqual(b['renderPref'], {'layoutAspectRatio': 'portrait'})
        self.assertEqual(b['importPref'], {'sourceLang': 'en'})
        self.assertNotIn('conclusionActions', b)

    def test_clip_durations_always_sent(self):
        b = oc.build_project_body('U', durations=[[0, 90]])
        self.assertEqual(b['curationPref']['clipDurations'], [[0, 90]])

    def test_validation(self):
        with self.assertRaises(oc.UsageError):
            oc.build_project_body('U', model='ClipBasic', prompt='x')         # prompt is ClipAnything-only
        with self.assertRaises(oc.UsageError):
            oc.build_project_body('U', keywords='a,b')                       # keywords are ClipBasic-only
        with self.assertRaises(oc.UsageError):
            oc.build_project_body('U', start=10)                             # range needs both ends
        with self.assertRaises(oc.UsageError):
            oc.build_project_body('U', start=0, end=11 * 3600)               # over 10 h
        with self.assertRaises(oc.UsageError):
            oc.build_project_body('U', genre='Speedrun')
        b = oc.build_project_body('U', model='ClipBasic', keywords='hulk, alligator')
        self.assertEqual(b['curationPref']['topicKeywords'], ['hulk', 'alligator'])

    def test_render_and_actions(self):
        b = oc.build_project_body('U', captions=True, emoji=False, remove_fillers=True, webhook='https://x.test/h',
                                  email='a@b.test', auto_hook=True)
        self.assertEqual(b['renderPref']['enableCaption'], True)
        self.assertEqual(b['renderPref']['enableEmoji'], False)
        self.assertNotIn('enableHighlight', b['renderPref'])
        self.assertEqual(b['renderPref']['quickstartConfig'], {'enableRemoveFillerWords': True})
        self.assertTrue(b['curationPref']['enableAutoHook'])
        types = [a['type'] for a in b['conclusionActions']]
        self.assertEqual(types, ['WEBHOOK', 'EMAIL'])


class TestHttpBehaviour(unittest.TestCase):
    def test_bearer_header_and_json(self):
        c, t, _ = client([(201, {}, {'id': 'P1', 'projectId': 'P1', 'stage': 'QUEUED'})])
        p = c.create_project({'videoUrl': 'U', 'curationPref': {'clipDurations': [[0, 30]]}})
        self.assertEqual(p['projectId'], 'P1')
        call = t.calls[0]
        self.assertEqual(call['method'], 'POST')
        self.assertEqual(call['url'], 'https://api.opus.pro/api/clip-projects')
        self.assertEqual(call['headers']['Authorization'], f'Bearer {KEY}')
        self.assertEqual(call['headers']['Content-Type'], 'application/json')
        self.assertEqual(call['body']['videoUrl'], 'U')

    def test_429_retry_after(self):
        c, t, sleeps = client([(429, {'Retry-After': '3'}, {'message': 'slow down'}), (200, {}, {'id': 'P1', 'stage': 'QUEUED'})])
        p = c.project('P1')
        self.assertEqual(p['stage'], 'QUEUED')
        self.assertEqual(len(t.calls), 2)
        self.assertIn(3.0, sleeps)

    def test_429_concurrent_backoff(self):
        c, t, sleeps = client([(429, {'X-Cap-Reason': 'concurrent'}, {}), (201, {}, {'id': 'P2'})])
        c.create_project({'videoUrl': 'U'})
        self.assertEqual(sleeps[0], 30.0)

    def test_monthly_cap_is_not_retried(self):
        body = {'code': 'API_MONTHLY_CAP_REACHED', 'reset_at': '2026-12-01T00:00:00Z',
                'upgrade_url': 'https://www.opus.pro/contact-api'}
        c, t, sleeps = client([(403, {}, body)])
        with self.assertRaises(oc.CapReached) as ctx:
            c.create_project({'videoUrl': 'U'})
        self.assertEqual(len(t.calls), 1)
        self.assertIn('2026-12-01', str(ctx.exception))
        self.assertEqual(sleeps, [])

    def test_post_not_retried_on_server_error(self):
        c, t, _ = client([(500, {}, {'message': 'boom'})])
        with self.assertRaises(oc.ApiError):
            c.create_project({'videoUrl': 'U'})
        self.assertEqual(len(t.calls), 1)

    def test_get_retried_on_server_error(self):
        c, t, _ = client([(502, {}, b''), (200, {}, {'id': 'P1', 'stage': 'COMPLETE'})])
        self.assertEqual(c.project('P1')['stage'], 'COMPLETE')
        self.assertEqual(len(t.calls), 2)

    def test_missing_key(self):
        c = oc.OpusClip(api_key=None, transport=FakeTransport(), min_interval=0)
        with self.assertRaises(oc.UsageError) as ctx:
            c.project('P1')
        self.assertIn('OPUSCLIP_API_KEY', str(ctx.exception))

    def test_org_header_only_on_clip_listing(self):
        c, t, _ = client([(200, {}, [])], org_id='org_1')
        c.clips('P1')
        self.assertEqual(t.calls[0]['headers'].get('x-opus-org-id'), 'org_1')
        self.assertIn('q=findByProjectId', t.calls[0]['url'])
        self.assertIn('projectId=P1', t.calls[0]['url'])

    def test_wait_until_complete(self):
        c, t, sleeps = client([(200, {}, {'stage': 'CURATE'}), (200, {}, {'stage': 'RENDER'}), (200, {}, {'stage': 'COMPLETE'})])
        p = c.wait('P1', interval=7)
        self.assertEqual(p['stage'], 'COMPLETE')
        self.assertEqual(sleeps.count(7), 2)


class TestClips(unittest.TestCase):
    CLIPS = [
        {'id': 'P1.CUa', 'projectId': 'P1', 'curationId': 'CUa', 'durationMs': 19478, 'timeRanges': [[192170, 211630]],
         'title': 'Low one', 'score': 61, 'uriForExport': 'https://cdn.example.test/a/ehd.mp4'},
        {'id': 'P1.CUb', 'projectId': 'P1', 'curationId': 'CUb', 'durationMs': 31000, 'timeRanges': [[15162000, 15193000]],
         'title': 'Alligator!', 'score': 92, 'uriForExport': 'https://cdn.example.test/b/ehd.mp4',
         'judgeResult': {'hookScore': 9}},
        {'id': 'P1.CUc', 'projectId': 'P1', 'durationMs': 25000, 'title': 'No score'},
    ]

    def test_sorted_by_score_and_ms_ranges(self):
        c, t, _ = client([(200, {}, {'data': self.CLIPS})])
        clips = c.clips('P1')
        self.assertEqual([x['clipId'] for x in clips], ['CUb', 'CUa', 'CUc'])
        self.assertAlmostEqual(clips[0]['sourceStart'], 15162.0)
        self.assertAlmostEqual(clips[1]['sourceStart'], 192.17)
        self.assertEqual(clips[0]['hookScore'], 9)

    def test_download_never_sends_the_key(self):
        c, t, _ = client([(200, {}, self.CLIPS)])
        got = []

        def fake_get(url, dest):
            got.append((url, dest))
            with open(dest, 'wb') as fh:
                fh.write(b'mp4')
        c.get_file = fake_get
        with tempfile.TemporaryDirectory() as d:
            saved = c.download_clips(c.clips('P1')[:2], d)
            self.assertEqual(len(got), 2)
            self.assertTrue(got[0][0].startswith('https://cdn.example.test/b/'))
            self.assertTrue(os.path.exists(os.path.join(d, 'clips.json')))
            self.assertTrue(saved[0]['file'].startswith('01_alligator'))
        self.assertEqual(len(t.calls), 1, 'downloads must not go through the authenticated transport')


class TestUpload(unittest.TestCase):
    def test_v2_four_steps(self):
        link = {'url': 'https://storage.example.test/upload?sig=1', 'uploadId': 'UP123',
                'requiredHeaders': {'x-goog-resumable': 'start', 'Content-Type': 'application/octet-stream'}}
        c, t, _ = client([(200, {}, link), (201, {'Location': 'https://storage.example.test/session/abc'}, b''),
                          (201, {}, {'id': 'P9', 'stage': 'QUEUED'})])
        puts = []
        c.put_file = lambda url, path, headers: (puts.append((url, path, headers)), 200)[1]
        with tempfile.NamedTemporaryFile(suffix='.mp4') as f:
            f.write(b'0' * 1024)
            f.flush()
            uid = c.upload(f.name)
            c.create_project(oc.build_project_body(uid))
        self.assertEqual(uid, 'UP123')
        step1, step2, step4 = t.calls
        self.assertEqual(step1['url'], 'https://api.opus.pro/api/upload-links-v2')
        self.assertEqual(step1['body'], {'usecase': 'LocalUpload'})
        self.assertEqual(step2['url'], link['url'])
        self.assertEqual(step2['headers']['x-goog-resumable'], 'start')
        self.assertEqual(step2['headers']['Content-Length'], '0')
        self.assertNotIn('Authorization', step2['headers'], 'the API key must never go to the storage host')
        self.assertEqual(puts[0][0], 'https://storage.example.test/session/abc')
        self.assertEqual(puts[0][2], {'Content-Type': 'application/octet-stream'})
        self.assertEqual(step4['body']['videoUrl'], 'UP123')


class TestSocial(unittest.TestCase):
    def test_social_copy_polls_and_uses_bare_clip_id(self):
        c, t, sleeps = client([(200, {}, {'data': {'jobId': 'J1'}}), (200, {}, {'data': {'status': 'RUNNING'}}),
                               (200, {}, {'data': {'status': 'COMPLETED', 'title': 'T', 'description': 'D', 'hashtags': ['#gta6']}})])
        r = c.social_copy('P1', 'P1.CUb', 'acct1', prompt='punchy')
        self.assertEqual(r['status'], 'COMPLETED')
        self.assertEqual(t.calls[0]['body'], {'projectId': 'P1', 'clipId': 'CUb', 'postAccountId': 'acct1', 'prompt': 'punchy'})
        self.assertEqual(len(t.calls), 3)

    def test_post_body(self):
        b = oc.OpusClip.post_body('P1', 'P1.CUb', 'acct', 'Hook', sub_account='sub', description='D', privacy='public')
        self.assertEqual(b, {'projectId': 'P1', 'clipId': 'CUb', 'postAccountId': 'acct', 'subAccountId': 'sub',
                             'postDetail': {'title': 'Hook', 'custom': {'description': 'D', 'privacy': 'public'}}})
        with self.assertRaises(oc.UsageError):
            oc.OpusClip.post_body('P1', 'C', 'a', '')
        with self.assertRaises(oc.UsageError):
            oc.OpusClip.post_body('P1', 'C', 'a', 'T', privacy='friends')

    def test_publish_at(self):
        self.assertEqual(oc._publish_at('2099-01-02T03:04:05Z'), '2099-01-02T03:04:05Z')
        self.assertEqual(oc._publish_at('2099-01-02T03:04:05-05:00'), '2099-01-02T08:04:05Z')
        for bad in ('2001-01-01T00:00:00Z', '2099-01-02T03:04:05', 'tomorrow'):
            with self.assertRaises(oc.UsageError):
                oc._publish_at(bad)


class TestCli(unittest.TestCase):
    def setUp(self):
        os.environ[oc.KEY_ENV] = KEY

    def tearDown(self):
        os.environ.pop(oc.KEY_ENV, None)

    def test_dry_run_prints_request_without_key(self):
        t = FakeTransport()
        c = oc.OpusClip(api_key=KEY, transport=t, min_interval=0, dry_run=True)
        code, out, err = run_main(['--dry-run', 'create', '--url', 'https://www.youtube.com/watch?v=qSyo5d-dVzA',
                                   '--start', '4:11:30', '--end', '4:21:30', '--prompt', 'the alligator'], c)
        self.assertEqual(code, 0)
        self.assertIn('POST https://api.opus.pro/api/clip-projects', out)
        self.assertIn('Authorization: Bearer $OPUSCLIP_API_KEY', out)
        self.assertIn('"startSec": 15090', out)
        self.assertNotIn(KEY, out + err)
        self.assertEqual(t.calls, [], 'a dry run must not send anything')

    def test_create_needs_yes(self):
        t = FakeTransport()
        c = oc.OpusClip(api_key=KEY, transport=t, min_interval=0)
        code, out, err = run_main(['create', '--url', 'https://www.youtube.com/watch?v=x', '--start', '0', '--end', '600'], c)
        self.assertEqual(code, oc.EXIT_NEEDS_YES)
        self.assertIn('--yes', err)
        self.assertEqual(t.calls, [])

    def test_create_with_yes_sends(self):
        t = FakeTransport([(201, {}, {'id': 'P5', 'stage': 'QUEUED'})])
        c = oc.OpusClip(api_key=KEY, transport=t, min_interval=0, sleep=lambda s: None)
        code, out, err = run_main(['--json', 'create', '--url', 'https://www.youtube.com/watch?v=x', '--start', '0',
                                   '--end', '600', '--yes'], c)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['projectId'], 'P5')
        self.assertEqual(json.loads(out)['estimatedCredits'], 10)

    def test_post_and_schedule_need_yes(self):
        for argv in (['post', 'P1', 'C1', '--account', 'a', '--title', 'T'],
                     ['schedule', 'P1', 'C1', '--account', 'a', '--title', 'T', '--at', '2099-01-01T00:00:00Z'],
                     ['unschedule', 'S1'], ['share', 'P1'], ['censor', 'P1', 'C1']):
            t = FakeTransport()
            code, _, _ = run_main(argv, oc.OpusClip(api_key=KEY, transport=t, min_interval=0))
            self.assertEqual(code, oc.EXIT_NEEDS_YES, argv)
            self.assertEqual(t.calls, [], argv)

    def test_cap_exit_code(self):
        t = FakeTransport([(403, {}, {'code': 'API_MONTHLY_CAP_REACHED', 'reset_at': 'soon'})])
        code, _, err = run_main(['status', 'P1'], oc.OpusClip(api_key=KEY, transport=t, min_interval=0))
        self.assertEqual(code, oc.EXIT_CAP)
        self.assertIn('monthly API cap', err)

    def test_templates_presets_offline(self):
        code, out, _ = run_main(['templates', '--presets'], oc.OpusClip(api_key=None, transport=FakeTransport()))
        self.assertEqual(code, 0)
        self.assertIn('preset-fancy-Gameplay', out)

    def test_batch_dry_run_from_plan(self):
        import plan_jobs
        moments = [{'rank': 1, 'start': 15162, 'end': 15232}, {'rank': 2, 'start': 15300, 'end': 15350},
                   {'rank': 3, 'start': 20000, 'end': 20060}]
        plan = plan_jobs.build_plan(moments, 'https://www.youtube.com/watch?v=abcdefghijk', duration=7 * 3600)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'plan.json')
            with open(p, 'w') as fh:
                json.dump(plan, fh)
            t = FakeTransport()
            c = oc.OpusClip(api_key=None, transport=t, min_interval=0, dry_run=True)
            code, out, err = run_main(['--dry-run', 'batch', p], c)
        self.assertEqual(code, 0)
        self.assertEqual(out.count('POST https://api.opus.pro/api/clip-projects'), 2)   # jobs 1+2 merged
        self.assertEqual(t.calls, [])


class TestBatchSlotsAndVodTimes(unittest.TestCase):
    """batch frees a slot when a project fails, gives up after --timeout, and clips/download report stream time."""

    def _plan(self, d, duration=7 * 3600):
        import plan_jobs
        moments = [{'rank': 1, 'start': 3600, 'end': 3660}, {'rank': 2, 'start': 20000, 'end': 20060}]
        plan = plan_jobs.build_plan(moments, 'https://www.youtube.com/watch?v=abcdefghijk', duration=duration)
        p = os.path.join(d, 'plan.json')
        with open(p, 'w') as fh:
            json.dump(plan, fh)
        return p

    def _client(self, answers):
        t, now = FakeTransport(answers), [0.0]

        def sleep(s):
            now[0] += s
        return oc.OpusClip(api_key=KEY, transport=t, sleep=sleep, clock=lambda: now[0], min_interval=0), t

    def test_project_with_an_error_frees_its_slot(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._plan(d)
            c, t = self._client([(201, {}, {'id': 'P1', 'stage': 'QUEUED'}),
                                 (200, {}, {'id': 'P1', 'stage': 'IMPORT', 'error': 'import failed'}),
                                 (201, {}, {'id': 'P2', 'stage': 'QUEUED'})])
            code, out, err = run_main(['batch', p, '--yes', '--max-concurrent', '1'], c)
            with open(p) as fh:
                saved = json.load(fh)
        self.assertEqual(code, 0, err)
        self.assertEqual([x['method'] for x in t.calls], ['POST', 'GET', 'POST'])
        self.assertEqual([j['projectId'] for j in saved['jobs']], ['P1', 'P2'])

    def test_waiting_for_a_slot_times_out(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._plan(d)
            c, t = self._client([(201, {}, {'id': 'P1', 'stage': 'QUEUED'})])   # P1 never finishes
            code, out, err = run_main(['batch', p, '--yes', '--max-concurrent', '1', '--interval', '30',
                                       '--timeout', '60'], c)
            with open(p) as fh:
                saved = json.load(fh)
        self.assertEqual(code, oc.EXIT_API)
        self.assertIn('no free project slot after 60s', err)
        self.assertEqual(saved['jobs'][0]['projectId'], 'P1', 'jobs already sent stay in the plan for a re-run')
        self.assertIsNone(saved['jobs'][1].get('projectId'))
        self.assertEqual(sum(1 for x in t.calls if x['method'] == 'POST'), 1)

    def test_clip_times_become_stream_times_for_upload_jobs(self):
        clip = {'id': 'P1.CUa', 'projectId': 'P1', 'durationMs': 30000, 'timeRanges': [[12000, 42000]],
                'title': 'Alligator', 'score': 90}
        with tempfile.TemporaryDirectory() as d:
            p = self._plan(d, duration=23.7 * 3600)          # longer than 10 h: every job routes "upload"
            with open(p) as fh:
                plan = json.load(fh)
            self.assertEqual(plan['route'], 'upload')
            plan['jobs'][0]['projectId'] = 'P1'
            with open(p, 'w') as fh:
                json.dump(plan, fh)
            start = plan['jobs'][0]['start']
            c, _ = self._client([(200, {}, [clip])])
            code, out, err = run_main(['--json', 'clips', 'P1', '--plan', p], c)
            got = json.loads(out)[0]
            self.assertEqual(code, 0, err)
            self.assertAlmostEqual(got['sourceStart'], 12.0)                  # inside the uploaded cut
            self.assertAlmostEqual(got['vodStart'], start + 12.0)             # in the stream
            self.assertAlmostEqual(got['vodEnd'], start + 42.0)
            self.assertEqual(got['job'], 'job01')
            c, _ = self._client([(200, {}, [clip])])
            code, out, _ = run_main(['clips', 'P1'], c)
            self.assertIn("VOD time = the job's start in plan.json", out)
            c, _ = self._client([(200, {}, [clip])])
            code, _, err = run_main(['clips', 'P9', '--plan', p], c)
            self.assertEqual(code, oc.EXIT_USAGE)
            self.assertIn('not in', err)


if __name__ == '__main__':
    unittest.main()
