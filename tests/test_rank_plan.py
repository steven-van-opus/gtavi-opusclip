"""Tests for the moment ranker, the OpusClip job planner and the campaign math (pure Python, no network)."""
import json
import os
import random
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'scripts'))

import campaign_math as cm  # noqa: E402
import plan_jobs as pj  # noqa: E402
import rank_moments as rm  # noqa: E402
import scan_vod  # noqa: E402


def synthetic_stream(hours=2.0, seed=7):
    """Two hours of flat game audio and chatter, one real reaction at 1:00:00 and one looped song at 1:30:00."""
    rnd = random.Random(seed)
    total = int(hours * 3600)
    rms = {t: -30.0 + rnd.uniform(-2, 2) for t in range(total)}
    segs = []
    filler = ['okay so where are we going', 'let me check the map real quick', 'this car is kind of slow',
              'alright chat we keep going', 'I need to find the mission marker']
    for t in range(0, total, 6):
        segs.append((float(t), float(t + 5), rnd.choice(filler)))
    # the moment: loud, fast, full of reactions
    for t in range(3600, 3625):
        rms[t] = -11.0 + rnd.uniform(-1, 1)
    segs = [s for s in segs if not 3594 <= s[0] < 3640 and not 5394 <= s[0] < 5440]
    segs += [(3600.0, 3604.0, 'No way! Oh my god! What the hell is that?'),
             (3604.0, 3608.0, 'Bro, bro, run! Let\'s go! Ahhh!'),
             (3608.0, 3614.0, 'No way, no way, I\'m crying, chat, that is insane!'),
             (3614.0, 3620.0, 'Oh no, the cops! Haha, he crashed!')]
    # a looped song: loud-ish, repeated lines, should be penalized
    for t in range(5400, 5430):
        rms[t] = -16.0
    segs += [(5400.0 + i * 3, 5403.0 + i * 3, 'body heart body heart body heart body heart') for i in range(10)]
    segs.sort()
    return segs, rms, total


class TestRanker(unittest.TestCase):
    def test_finds_the_planted_reaction_first(self):
        segs, rms, total = synthetic_stream()
        rows = rm.rank(segs, rms, [(0.0, float(total))])
        moments = rm.pick(rows, segs, rms, top=5, limit=total)
        self.assertTrue(moments[0]['start'] <= 3605 <= moments[0]['end'], moments[0])
        self.assertTrue(any('reactions' in r for r in moments[0]['reasons']))
        song = [m for m in moments if m['start'] <= 5410 <= m['end']]
        self.assertTrue(not song or song[0]['rank'] > 1, 'a looped song must not outrank the reaction')

    def test_repetition_penalty(self):
        self.assertGreater(rm.repetition('body heart body heart body heart body heart body heart'), 0.5)
        self.assertLess(rm.repetition('no way what the hell is that bro run he crashed into the cops'), 0.1)

    def test_rarity_weighting(self):
        rows = [{'hits': {'bro': 1}} for _ in range(90)] + [{'hits': {'no way': 1}} for _ in range(5)]
        idf = rm.cue_idf(rows)
        self.assertLess(idf['bro'], idf['no way'])
        self.assertGreaterEqual(idf['bro'], 0.3)

    def test_brand_flags_and_masking(self):
        r = {'music': False, 'rep': 0.0, 'words': 30}
        flags = rm.flags_for(r, 'what the fuck is this shit, strip club next')
        self.assertTrue(any(f.startswith('profanity: 2') for f in flags))
        self.assertTrue(any('sexual' in f for f in flags))
        self.assertEqual(rm.mask('what the fuck'), 'what the f***')
        hard = rm.flags_for(r, 'some npc says n' + 'igga here')
        self.assertTrue(any(f.startswith('HARD') for f in hard))
        self.assertIn('[slur]', rm.mask('npc says n' + 'igger'))

    def test_partial_scan_only_ranks_finished_chunks(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'transcript.jsonl')
            with open(p, 'w') as w:
                for chunk in (0, 1, 3):   # chunk 2 not transcribed yet
                    s = chunk * 1200
                    w.write(json.dumps({'chunk': chunk, 'start': s + 10, 'end': s + 14, 'text': 'hello chat'}) + '\n')
                    w.write(json.dumps({'chunk': chunk, 'start': s, 'end': s, 'text': '', 'marker': 'chunk_done'}) + '\n')
            segs, spans = rm.load_transcript(p)
        self.assertEqual(spans, [(0.0, 2400.0), (3600.0, 4800.0)])
        rows = rm.rank(segs, {}, spans)
        self.assertFalse(any(2400 <= r['t'] < 3600 for r in rows))

    def test_resume_redoes_a_chunk_cut_off_mid_write(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'transcript.jsonl')
            with open(p, 'w') as w:
                for chunk in (0, 1):
                    s = chunk * 1200
                    w.write(json.dumps({'chunk': chunk, 'start': s + 10, 'end': s + 14, 'text': 'hello chat'}) + '\n')
                    w.write(json.dumps({'chunk': chunk, 'start': s, 'end': s, 'text': '', 'marker': 'chunk_done'}) + '\n')
                w.write(json.dumps({'chunk': 2, 'start': 2410, 'end': 2414, 'text': 'no way bro'}) + '\n')  # no marker
                w.write('{"chunk": 2, "start": 2420, "end": 24')                                          # half a line
            segs, spans = rm.load_transcript(p)          # the ranker reads past the broken line
            self.assertEqual(spans, [(0.0, 2400.0)])
            self.assertEqual(scan_vod.finished_chunks(p), {0, 1}, 'only a chunk_done marker finishes a chunk')
            with open(p) as fh:
                rows = [json.loads(line) for line in fh]
            self.assertEqual({r['chunk'] for r in rows}, {0, 1}, "chunk 2's orphan lines are removed before it is redone")
            self.assertEqual(len(rows), 4)
            self.assertEqual(scan_vod.finished_chunks(p), {0, 1})
            self.assertEqual(scan_vod.finished_chunks(os.path.join(d, 'missing.jsonl')), set())

    def test_duplicate_segments_count_once(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'transcript.jsonl')
            line = json.dumps({'chunk': 0, 'start': 10, 'end': 14, 'text': 'no way'}) + '\n'
            with open(p, 'w') as w:
                w.write(line + line)
                w.write(json.dumps({'chunk': 0, 'start': 0, 'end': 0, 'text': '', 'marker': 'chunk_done'}) + '\n')
            segs, _ = rm.load_transcript(p)
        self.assertEqual(segs, [(10.0, 14.0, 'no way')])

    def test_youtube_caption_dedupe(self):
        vtt = ('WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nno way bro\n\n'
               '00:00:03.000 --> 00:00:05.000\nno way bro\nwhat is that\n\n'
               '00:00:05.000 --> 00:00:07.000\nwhat is that\nhe crashed\n')
        segs = rm.parse_captions(vtt)
        self.assertEqual([s[2] for s in segs], ['no way bro', 'what is that', 'he crashed'])

    def test_short_source_and_limit(self):
        segs = [(0.0, 3.0, 'bro what'), (4.0, 8.0, 'no way')]
        rows = rm.rank(segs, {0: -20, 1: -10, 2: -12}, [(0.0, 20.0)])
        self.assertEqual(len(rows), 1)
        m = rm.pick(rows, segs, {}, top=1, limit=20.0)[0]
        self.assertLessEqual(m['end'], 20.0)

    def test_eval_recall(self):
        moments = [{'rank': 1, 'start': 100, 'end': 160}, {'rank': 2, 'start': 900, 'end': 950}]
        leads = [{'label': 'a', 'start': 120, 'end': 140}, {'label': 'b', 'start': 5000, 'end': 5040},
                 {'label': 'out of scan', 'start': 99999, 'end': 100040}]
        covered, res, recall = rm.evaluate(moments, leads, [(0, 6000)])
        self.assertEqual(len(covered), 2)
        self.assertEqual(recall[5], 1)


class TestPlanner(unittest.TestCase):
    M = [{'rank': 1, 'start': 15162, 'end': 15232}, {'rank': 2, 'start': 15300, 'end': 15350},
         {'rank': 4, 'start': 73284, 'end': 73354}]

    def test_long_youtube_vod_is_cut_and_uploaded(self):
        plan = pj.build_plan(self.M, 'https://www.youtube.com/watch?v=qSyo5d-dVzA', duration=85542)
        self.assertEqual(plan['route'], 'upload')
        self.assertEqual(len(plan['jobs']), 2, 'moments 1 and 2 sit close together and merge')
        j = plan['jobs'][0]
        self.assertEqual(j['moments'], [1, 2])
        self.assertEqual(j['cut']['start'], 15162 - 60)
        self.assertEqual(j['cut']['end'], 15350 + 45)
        self.assertEqual(j['request']['videoUrl'], '<uploadId>')
        self.assertNotIn('range', j['request']['curationPref'])
        self.assertEqual(j['estimatedCredits'], 10)

    def test_short_vod_uses_url_and_range(self):
        m = [{'rank': 1, 'start': 3600, 'end': 3660}]
        plan = pj.build_plan(m, 'https://www.youtube.com/watch?v=abcdefghijk', duration=6 * 3600)
        j = plan['jobs'][0]
        self.assertEqual(plan['route'], 'url')
        self.assertEqual(j['request']['curationPref']['range'], {'startSec': 3540, 'endSec': 3705})
        self.assertEqual(j['request']['curationPref']['genre'], 'Gaming')
        self.assertEqual(j['request']['brandTemplateId'], 'preset-fancy-Gameplay')

    def test_kick_and_files_upload(self):
        self.assertEqual(pj.build_plan(self.M[:1], 'https://kick.com/someone/videos/123', duration=3 * 3600)['route'], 'upload')
        with tempfile.NamedTemporaryFile(suffix='.mp4') as f:
            self.assertEqual(pj.build_plan(self.M[:1], f.name, duration=20000)['route'], 'upload')

    def test_unknown_length_warns(self):
        plan = pj.build_plan(self.M[:1], 'https://www.twitch.tv/videos/123456789')
        self.assertEqual(plan['route'], 'url')
        self.assertTrue(any('unknown' in n for n in plan['notes']))

    def test_credit_estimate_totals(self):
        m = [{'rank': 1, 'start': 0, 'end': 1500}]   # 25 minutes plus padding
        plan = pj.build_plan(m, 'https://www.youtube.com/watch?v=abcdefghijk', duration=7200, pad_before=0, pad_after=0)
        self.assertEqual(plan['totals']['estimatedCredits'], 25)

    def test_youtube_id_parsing(self):
        self.assertEqual(pj.youtube_id('https://youtu.be/qSyo5d-dVzA'), 'qSyo5d-dVzA')
        self.assertEqual(pj.youtube_id('https://www.youtube.com/live/qSyo5d-dVzA?si=x'), 'qSyo5d-dVzA')
        self.assertEqual(pj.youtube_id('qSyo5d-dVzA'), 'qSyo5d-dVzA')
        self.assertIsNone(pj.youtube_id('https://www.twitch.tv/videos/1'))


class TestCampaignMath(unittest.TestCase):
    def test_minimum_and_cap(self):
        self.assertEqual(cm.clip_payout(3000, 1.0, 10, 500), 0.0)
        self.assertEqual(cm.clip_payout(50000, 1.0, 10, 500), 50.0)
        self.assertEqual(cm.clip_payout(1_000_000, 1.0, 10, 500), 500.0)

    def test_810_clip_example(self):
        r = cm.cpm_report(cm.parse_dist('770x3000,30x50000,10x1000000'), 1.0, 10, 500, 0.10)
        self.assertEqual(r['clips'], 810)
        self.assertEqual(r['total_views'], 13_810_000)
        self.assertEqual(r['naive_views_x_cpm'], 13810.0)
        self.assertEqual(r['gross'], 6500.0)
        self.assertEqual(r['net'], 5850.0)

    def test_pool_and_target(self):
        self.assertEqual(cm.pool_report(10000, 100_000_000, 1_000_000, 0.0)['gross'], 100.0)
        t = cm.target_report(10000, 1.0, 0.10)
        self.assertEqual(t['paid_views_needed'], 11_111_111)

    def test_parse_views(self):
        self.assertEqual(cm.parse_views('3k,1.5m,200'), [3000, 1_500_000, 200])


if __name__ == '__main__':
    unittest.main()
