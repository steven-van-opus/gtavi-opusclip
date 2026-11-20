#!/usr/bin/env python3
"""rank_moments.py: rank every 30-second window of a full stream and pick the moments worth clipping.

Input is what scan_vod.py writes (transcript.jsonl + loudness.tsv), or any .vtt/.srt transcript
(for example YouTube auto-captions). Nothing leaves your machine.

  python3 -I rank_moments.py --transcript scan/transcript.jsonl --loudness scan/loudness.tsv \\
      --out-dir scan --top 15 --video-id qSyo5d-dVzA --label "IShowSpeed (YouTube)"

Each window gets features, then one score:
  loud_z    top-5 loudness of the window against the median of the 20 minutes around it (robust z)
  jump_z    how much louder it got than the minute before (sudden reactions)
  cues      weighted reaction words: "no way", "oh my god", "let's go", "bro", "chat", laughter,
            screams, game events (cops, crash, wasted, alligator...), each capped so one word can't win
  rate_z    words per second against the local level (talking fast = hyped)
  penalties repeated lines (song lyrics, whisper loops), music markers, dead air
  score = loud_z + 0.5*jump_z + cues + 0.25*rate_z - penalties
Then non-maximum suppression keeps the best window per --min-gap seconds, grows it into a 20-60 s
moment snapped to sentence edges, and writes the reasons and brand-safety flags (profanity to bleep,
slurs, sexual or drug terms, music) next to each pick.

Outputs in --out-dir: moments.json, moments.md, ranked_windows.tsv (and eval.md with --eval).
"""
import argparse
import bisect
import json
import math
import os
import re
import statistics
import sys
import time

VERSION = '1.0.0'

CUES = [  # (name, regex, weight, cap)
    ('no way', r"\bno+ wa+y+\b", 1.0, 3),
    ('oh my god', r"\b(oh my (god|gosh|lord)|omg)\b", 1.0, 3),
    ('what the', r"\bwhat the (hell|heck|f\w*)\b", 1.0, 3),
    ("let's go", r"\blet'?s go+\b", 1.0, 3),
    ('yo', r"\byo+\b", 0.6, 3),
    ('bro', r"\b(bro+|bruh+|dude)\b", 0.5, 4),
    ('nah', r"\b(na+h+|hell no|hell yeah)\b", 0.6, 3),
    ('chat', r"\bchat\b", 0.5, 4),
    ('wait', r"\bwait\b", 0.4, 3),
    ('look', r"\blook at (this|that|him|her|it)\b", 0.6, 2),
    ('oh no', r"\boh no+\b", 1.0, 3),
    ('serious', r"\b(are you serious|you'?re joking|i can'?t believe|i'?m crying|i'?m dead)\b", 1.0, 2),
    ('crazy', r"\b(crazy|insane|wild|unbelievable|ridiculous)\b", 0.6, 3),
    ('holy', r"\bholy (shit|moly|crap|cow)\b", 1.0, 2),
    ('run', r"\b(run+|go go|help|stop)\b", 0.5, 3),
    ('laughter', r"\b(ha(ha)+h?|he(he)+|lmao+|lol)\b|\[laugh\w*\]|\(laugh\w*\)", 1.2, 3),
    ('scream', r"\b(a{2,}h+|ah{2,}|no{3,}|ay{2,}|wo{3,}|yea{2,}h)\b|\[scream\w*\]", 1.2, 3),
    ('countdown', r"\b(10|ten)\W+(9|nine)\W+(8|eight)\W+(7|seven)\b|\b(3|three)\W+(2|two)\W+(1|one)\W+(go|let'?s go)?", 1.5, 1),
    ('game event', r"\b(cops?|police|wanted|busted|wasted|dead|died|killed|crash\w*|explo\w*|shoot\w*|"
                   r"guns?|chase|flip\w*|plane|heli\w*|alligator|gator|shark|bear|tiger|hulk|monster|rob\w*|heist|jail)\b", 0.6, 4),
]
CUES = [(n, re.compile(p, re.I), w, cap) for n, p, w, cap in CUES]

PROFANITY = re.compile(r"\b(f+u+c*k\w*|motherf\w*|sh+i+t\w*|bitch\w*|cunt\w*|pussy|dick\w*|asshole\w*|bastard\w*|goddamn)\b", re.I)
SLURS = re.compile(r"\b(n[i1]gg(?:a|er|ah|as|ers|uh|az)?s?|f[a@]gg?(?:ot|ots|s)?|retard(?:ed|s)?|ch[i1]nks?|sp[i1]cs?|k[i1]kes?|"
                   r"trann(?:y|ies)|wetbacks?)\b", re.I)
SEXUAL = re.compile(r"\b(strip ?club|strippers?|nipples?|naked|nude|porn\w*|boobs?|titties|thongs?|twerk\w*|sex\w*)\b", re.I)
DRUGS = re.compile(r"\b(weed|bong|blunt|cocaine|coke|meth|crack|painkillers?|pills|high as)\b", re.I)
MUSIC = re.compile(r"[♪♫]|\[music\]|\(music\)", re.I)


# ---------------------------------------------------------------- loading

def parse_ts(s):
    s = s.strip().replace(',', '.')
    parts = [float(p) for p in s.split(':')]
    t = 0.0
    for p in parts:
        t = t * 60 + p
    return t


def load_transcript(path, duration=None):
    """-> (segments [(start, end, text)], covered spans [(a, b)] or None).
    scan_vod.py writes a chunk_done marker per finished chunk, so a partial scan only ranks what is done."""
    if path.endswith('.jsonl') or path.endswith('.json'):
        segs, marks = [], []
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get('marker') == 'chunk_done':
                    marks.append((d.get('chunk'), float(d.get('start', 0))))
                    continue
                if d.get('text', '').strip():
                    segs.append((float(d['start']), float(d['end']), d['text'].strip()))
        segs.sort()
        spans = None
        if marks:
            starts = sorted(s for _, s in marks)
            diffs = [b - a for a, b in zip(starts, starts[1:]) if b > a]
            if diffs:
                chunk = statistics.mode(diffs)
            elif duration:
                chunk = duration - starts[0]
            else:
                chunk = max((e for _, e, _ in segs), default=starts[0] + 1) - starts[0]
            spans = merge_spans([(s, s + chunk) for s in starts])
            if duration:
                spans = [(a, min(b, duration)) for a, b in spans if a < duration]
        return segs, spans
    return parse_captions(open(path, encoding='utf-8', errors='replace').read()), None


def parse_captions(text):
    """WebVTT or SRT -> segments; strips tags and the rolling duplicates of YouTube auto-captions."""
    cues = []
    blocks = re.split(r'\n\s*\n', text.replace('\r', ''))
    for b in blocks:
        m = re.search(r'(\d[\d:.,]*)\s*-->\s*(\d[\d:.,]*)', b)
        if not m:
            continue
        lines = b[m.end():].split('\n')[1:] if '\n' in b[m.end():] else []
        body = [re.sub(r'<[^>]+>', '', l).strip() for l in lines]
        body = [l for l in body if l]
        if body:
            cues.append((parse_ts(m.group(1)), parse_ts(m.group(2)), body))
    segs, prev_last = [], ''
    for a, b, body in cues:
        new = [l for l in body if l != prev_last]
        if len(body) > 1 and body[0] == prev_last:
            new = body[1:]
        txt = ' '.join(new).strip()
        if txt and (not segs or txt != segs[-1][2]):
            segs.append((a, b, txt))
        prev_last = body[-1]
    return segs


def load_loudness(path):
    rms = {}
    with open(path) as f:
        next(f, None)
        for line in f:
            p = line.rstrip('\n').split('\t')
            if len(p) >= 2:
                try:
                    rms[int(float(p[0]))] = float(p[1])
                except ValueError:
                    pass
    return rms


def merge_spans(spans, gap=1.0):
    out = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [tuple(x) for x in out]


# ---------------------------------------------------------------- features

def robust(values):
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0, 1.0
    med = statistics.median(vals)
    mad = statistics.median([abs(v - med) for v in vals]) * 1.4826
    return med, mad


def cue_hits(text, idf=None):
    """Weighted reaction cues. idf scales each cue by how rare it is for this streamer, so their
    filler words ("bro", "chat") count for less than the rare ones ("no way", screams)."""
    hits, total = {}, 0.0
    for name, rx, w, cap in CUES:
        n = len(rx.findall(text))
        if n:
            hits[name] = n
            total += w * min(n, cap) * (idf.get(name, 1.0) if idf else 1.0)
    excl = text.count('!')
    if excl:
        hits['!'] = excl
        total += 0.3 * min(excl, 8) * (idf.get('!', 1.0) if idf else 1.0)
    return hits, total


def cue_idf(rows):
    """0.3 for a cue in about half of all windows, 1.0 at 5%, up to 1.5 for rarer cues."""
    n = max(1, len(rows))
    df = {}
    for r in rows:
        for k in r['hits']:
            df[k] = df.get(k, 0) + 1
    return {k: max(0.3, min(1.5, math.log(n / v) / math.log(20))) for k, v in df.items()}


def repetition(text):
    toks = re.findall(r"[a-z']+", text.lower())
    if len(toks) < 9:
        return 0.0
    tri = list(zip(toks, toks[1:], toks[2:]))
    return 1.0 - len(set(tri)) / len(tri)


def mask(text):
    text = SLURS.sub('[slur]', text)
    return PROFANITY.sub(lambda m: m.group(0)[0] + '*' * (len(m.group(0)) - 1), text)


def rank(segs, rms, spans=None, window=30, hop=10, duration=None, allow_untranscribed=False):
    if not spans:
        end = duration or max([e for _, e, _ in segs] + [max(rms) + 1 if rms else 0])
        spans = [(0.0, float(end))]
    seg_starts = [s for s, _, _ in segs]
    windows = []
    for a, b in spans:
        t = math.floor(a / hop) * hop
        first = len(windows)
        while t + window <= b + 0.01:
            windows.append(t)
            t += hop
        if len(windows) == first and b > a:   # a span shorter than one window still gets scored
            windows.append(float(a))
    rows = []
    for t in windows:
        i = bisect.bisect_left(seg_starts, t - 20)
        words, texts = 0.0, []
        while i < len(segs) and segs[i][0] < t + window:
            s, e, tx = segs[i]
            ov = min(e, t + window) - max(s, t)
            if ov > 0:
                frac = ov / max(0.5, e - s)
                words += len(tx.split()) * frac
                if s + (e - s) / 2 >= t - 1 or frac > 0.5:
                    texts.append(tx)
            i += 1
        text = ' '.join(texts)
        sec = [rms.get(k) for k in range(int(t), int(t + window))]
        sec = [v for v in sec if v is not None]
        loud = sum(sorted(sec)[-5:]) / len(sorted(sec)[-5:]) if sec else None
        before = [rms.get(k) for k in range(int(t) - 60, int(t))]
        before = [v for v in before if v is not None]
        hits, _ = cue_hits(text)
        rows.append({'t': t, 'loud': loud, 'before': statistics.median(before) if before else None,
                     'words': words, 'rate': words / window, 'text': text,
                     'hits': hits, 'rep': repetition(text), 'music': bool(MUSIC.search(text)),
                     'prof': len(PROFANITY.findall(text)), 'slur': bool(SLURS.search(text)),
                     'sexual': bool(SEXUAL.search(text)), 'drugs': bool(DRUGS.search(text))})
    # local baselines: +-10 minutes of windows
    n, reach = len(rows), int(600 / hop)
    louds = [r['loud'] for r in rows]
    rates = [r['rate'] for r in rows]
    idf = cue_idf(rows)
    for k, r in enumerate(rows):
        lo, hi = max(0, k - reach), min(n, k + reach + 1)
        r['jump'] = None
        if r['loud'] is not None:
            med, mad = robust(louds[lo:hi])
            r['loud_z'] = max(-3.0, min(6.0, (r['loud'] - med) / max(1.0, mad)))
            r['loud_over'] = r['loud'] - med
            if r['before'] is not None:   # coming back from silence is not a reaction: floor the "before" level
                r['jump'] = r['loud'] - max(r["before"], med - 12.0)
        else:
            r['loud_z'], r['loud_over'] = 0.0, None
        r['cue_w'] = cue_hits(r['text'], idf)[1]
    jmed, jmad = robust([r['jump'] for r in rows])
    for k, r in enumerate(rows):
        lo, hi = max(0, k - reach), min(n, k + reach + 1)
        r['jump_z'] = max(-3.0, min(6.0, (r['jump'] - jmed) / max(1.0, jmad))) if r['jump'] is not None else 0.0
        med, mad = robust(rates[lo:hi])
        r['rate_z'] = max(-2.0, min(3.0, (r['rate'] - med) / max(0.25, mad)))
        r['cues'] = 2.2 * math.log1p(r['cue_w'])
        pen = 4.0 * max(0.0, r['rep'] - 0.25) + (2.0 if r['music'] else 0.0)
        if r['words'] < 3 and r['loud_z'] < 0.5:
            pen += 1.0
        r['penalty'] = pen
        r['score'] = r['loud_z'] + 0.5 * r['jump_z'] + r['cues'] + 0.25 * r['rate_z'] - pen
    return rows


# ---------------------------------------------------------------- picking

def snap(segs, seg_starts, t, prefer, lo, hi):
    """Move t to the nearest segment start (prefer='start') or end within [lo, hi]."""
    best, bd = t, 1e9
    i = bisect.bisect_left(seg_starts, lo - 30)
    for s, e, _ in segs[i:]:
        if s > hi + 30:
            break
        c = s if prefer == 'start' else e
        if lo <= c <= hi and abs(c - t) < bd:
            best, bd = c, abs(c - t)
    return best


def nms(rows, top, min_gap, window=30):
    """Best windows first, skipping any within min_gap seconds of one already kept."""
    picked, centers = [], []
    for r in sorted(rows, key=lambda r: -r['score']):
        c = r['t'] + window / 2
        k = bisect.bisect_left(centers, c)
        if (k < len(centers) and centers[k] - c < min_gap) or (k > 0 and c - centers[k - 1] < min_gap):
            continue
        centers.insert(k, c)
        picked.append(r)
        if len(picked) >= top:
            break
    return picked


def pick(rows, segs, rms, top=15, min_gap=120, window=30, hop=10, min_len=20, max_len=60, pre=6, post=4, limit=None):
    by_t = {r['t']: r for r in rows}
    seg_starts = [s for s, _, _ in segs]
    picked = nms(rows, top, min_gap, window)
    moments = []
    for rank_i, r in enumerate(picked, 1):
        a, b = r['t'], r['t'] + window
        while (b - a) < max_len:            # grow toward strong neighbours
            nb_l, nb_r = by_t.get(a - hop), by_t.get(b - window + hop)
            sl = nb_l['score'] if nb_l else -1e9
            sr = nb_r['score'] if nb_r else -1e9
            if max(sl, sr) < 0.7 * r['score'] or r['score'] <= 0:
                break
            if sl >= sr:
                a -= hop
            else:
                b += hop
        a, b = max(0.0, a - pre), b + post
        if segs:
            a = snap(segs, seg_starts, a, 'start', a - 5, a + 5)
            b = snap(segs, seg_starts, b, 'end', b - 5, b + 5)
        if b - a < min_len:
            b = a + min_len
        if b - a > max_len + pre + post:
            b = a + max_len + pre + post
        if limit is not None and b > limit:
            b = limit
            a = max(0.0, min(a, b - min_len))
        secs = [(rms.get(k, -90.0), k) for k in range(int(a), int(b))]
        peak_at = max(secs)[1] if secs else int(r['t'])
        moments.append(build_moment(rank_i, r, a, b, peak_at, segs, seg_starts))
    return moments


def reasons_for(r):
    out = []
    if r.get('loud_over') is not None and r['loud_z'] >= 1.5:
        out.append(f'volume spike: {r["loud_over"]:+.1f} dB over the 20 minutes around it')
    if r.get('jump') is not None and r['jump_z'] >= 1.5:
        out.append(f'sudden jump: {r["jump"]:+.1f} dB vs the minute before')
    if r['hits']:
        top = sorted(r['hits'].items(), key=lambda kv: -kv[1])[:4]
        label = {'!': 'exclamations', 'game event': 'game-event words'}
        out.append('reactions: ' + ', '.join((f'{label[k]} x{v}' if k in label else f'"{k}" x{v}' if v > 1 else f'"{k}"')
                                            for k, v in top))
    if r['rate_z'] >= 1.5:
        out.append(f'talking fast: {r["rate"]:.1f} words/s')
    if not out:
        out.append('above the local baseline on several weak signals')
    return out


def flags_for(r, text):
    f = []
    if SLURS.search(text):
        f.append('HARD: a slur is heard in this window (listen first; skip unless it can be cut out cleanly)')
    n = len(PROFANITY.findall(text))
    if n:
        f.append(f'profanity: {n} word(s) to bleep (OpusClip auto censor on Pro+, or trim)')
    if SEXUAL.search(text):
        f.append('sexual terms: check the picture (no sexualized NPC content)')
    if DRUGS.search(text):
        f.append('drug terms: check before posting')
    if r['music'] or r['rep'] > 0.4:
        f.append('music or looped lines: licensed music risk or a transcription loop')
    if r['words'] < 3:
        f.append('little speech: verify on the frame sheet that something happens')
    return f


def build_moment(rank_i, r, a, b, peak_at, segs, seg_starts):
    i = bisect.bisect_left(seg_starts, a - 60)
    parts = []
    for s, e, tx in segs[i:]:
        if s >= b:
            break
        if e > a:
            parts.append(tx)
    text = ' '.join(parts)
    return {
        'rank': rank_i, 'start': round(a, 2), 'end': round(b, 2), 'duration': round(b - a, 1),
        'start_hms': hms(a), 'end_hms': hms(b), 'peak_at': peak_at, 'peak_hms': hms(peak_at),
        'score': round(r['score'], 2),
        'features': {k: (round(r[k], 2) if isinstance(r[k], float) else r[k]) for k in
                     ('loud_z', 'jump_z', 'cues', 'rate_z', 'penalty', 'words') if k in r},
        'reasons': reasons_for(r), 'flags': flags_for(r, text), '_text': text,
    }


def hms(sec):
    sec = int(round(sec))
    return f'{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def excerpt(text, n):
    words = mask(text).split()
    return ' '.join(words[:n]) + (' ...' if len(words) > n else '')


def source_link(video_id, url, t):
    if video_id:
        return f'https://www.youtube.com/watch?v={video_id}&t={int(t)}s'
    if url and 'twitch.tv/videos' in url:
        h, m, s = int(t) // 3600, int(t) % 3600 // 60, int(t) % 60
        return f'{url}?t={h}h{m}m{s}s'
    return None


# ---------------------------------------------------------------- eval

def load_leads(path):
    d = json.load(open(path))
    items = d.get('leads', d) if isinstance(d, dict) else d
    out = []
    for x in items:
        a = parse_ts(str(x['start'])) if ':' in str(x['start']) else float(x['start'])
        b = parse_ts(str(x['end'])) if ':' in str(x['end']) else float(x['end'])
        out.append({'label': x.get('label') or x.get('slug') or '', 'start': a, 'end': b})
    return out


def evaluate(moments, leads, spans, tol=30):
    covered = [l for l in leads if any(a <= l['start'] and l['end'] <= b + 60 for a, b in spans)]
    res = []
    for l in covered:
        best = None
        for m in moments:
            if m['start'] <= l['end'] + tol and m['end'] >= l['start'] - tol:
                best = m['rank']
                break
        res.append({**l, 'best_rank': best})
    ks = [k for k in (5, 10, 20, 50, 100) if k <= max(len(moments), 5)]
    recall = {k: sum(1 for r in res if r['best_rank'] and r['best_rank'] <= k) for k in ks}
    return covered, res, recall


# ---------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--transcript', required=True, help='transcript.jsonl from scan_vod.py, or a .vtt/.srt file')
    ap.add_argument('--loudness', help='loudness.tsv from scan_vod.py (strongly recommended)')
    ap.add_argument('--out-dir', default='.', help='where to write moments.json / moments.md / ranked_windows.tsv')
    ap.add_argument('--top', type=int, default=15, help='how many moments to keep (pick the final clips from these)')
    ap.add_argument('--window', type=int, default=30)
    ap.add_argument('--hop', type=int, default=10)
    ap.add_argument('--min-gap', type=int, default=120, help='seconds between two picks')
    ap.add_argument('--min-len', type=int, default=20)
    ap.add_argument('--max-len', type=int, default=60)
    ap.add_argument('--duration', type=float, help='source length in seconds (when there are no chunk markers)')
    ap.add_argument('--video-id', help='YouTube video id, for timestamp links')
    ap.add_argument('--source-url', help='source URL (Twitch VODs get ?t= links)')
    ap.add_argument('--label', default='', help='credit label for the report, e.g. "IShowSpeed (YouTube)"')
    ap.add_argument('--excerpt-words', type=int, default=18)
    ap.add_argument('--eval', help='JSON list of known moments [{label,start,end}] to measure recall')
    ap.add_argument('--allow-untranscribed', action='store_true', help='also rank stretches with no transcript')
    a = ap.parse_args(argv)

    t0 = time.time()
    segs, spans = load_transcript(a.transcript, a.duration)
    rms = load_loudness(a.loudness) if a.loudness else {}
    if a.allow_untranscribed or not spans:
        end = a.duration or max([e for _, e, _ in segs] + [max(rms) + 1 if rms else 0])
        spans = [(0.0, float(end))]
    rows = rank(segs, rms, spans, a.window, a.hop, a.duration)
    if not rows:
        print('nothing to rank (empty transcript and loudness)', file=sys.stderr)
        return 2
    limit = max(b for _, b in spans)
    moments = pick(rows, segs, rms, a.top, a.min_gap, a.window, a.hop, a.min_len, a.max_len, limit=limit)
    scores = sorted(r['score'] for r in rows)
    p99 = scores[int(0.99 * (len(scores) - 1))]
    found = sum(1 for r in nms(rows, 10 ** 9, a.min_gap, a.window) if r['score'] >= p99)
    covered_s = sum(b - a_ for a_, b in spans)
    for m in moments:
        m['excerpt'] = excerpt(m.pop('_text'), a.excerpt_words)
        m['link'] = source_link(a.video_id, a.source_url, m['start'])
    runtime = time.time() - t0
    out = {
        'tool': f'rank_moments.py {VERSION}',
        'source': {'video_id': a.video_id, 'url': a.source_url, 'label': a.label},
        'params': {k: getattr(a, k) for k in ('window', 'hop', 'min_gap', 'min_len', 'max_len', 'top')},
        'coverage': {'spans': [[round(x, 1), round(y, 1)] for x, y in spans], 'hours': round(covered_s / 3600, 2),
                     'segments': len(segs), 'loudness_seconds': len(rms)},
        'stats': {'windows_scored': len(rows), 'strong_moments': found,
                  'strong_definition': f'non-overlapping peaks ({a.min_gap}s apart) scoring in the top 1% of windows (score >= {p99:.2f})',
                  'rank_seconds': round(runtime, 2)},
        'moments': moments,
    }
    os.makedirs(a.out_dir, exist_ok=True)
    with open(os.path.join(a.out_dir, 'ranked_windows.tsv'), 'w') as w:
        w.write('score\tstart_s\tstart_hms\tloud_z\tjump_z\tcues\trate_z\tpenalty\twords\ttext\n')
        for r in sorted(rows, key=lambda r: -r['score']):
            w.write(f'{r["score"]:.2f}\t{r["t"]:.0f}\t{hms(r["t"])}\t{r["loud_z"]:.2f}\t{r["jump_z"]:.2f}\t{r["cues"]:.2f}\t'
                    f'{r["rate_z"]:.2f}\t{r["penalty"]:.2f}\t{r["words"]:.0f}\t{mask(r["text"])[:300]}\n')
    if a.eval:
        leads = load_leads(a.eval)
        everything = pick(rows, segs, rms, top=max(100, a.top), min_gap=a.min_gap, window=a.window, hop=a.hop, limit=limit)
        covered, res, recall = evaluate(everything, leads, spans)
        out['eval'] = {'leads_total': len(leads), 'leads_in_coverage': len(covered), 'recall_at': recall,
                       'leads': [{k: r[k] for k in ('label', 'start', 'end', 'best_rank')} for r in res]}
        with open(os.path.join(a.out_dir, 'eval.md'), 'w') as w:
            w.write(f'# Recall against {len(covered)} known moments inside the scanned span\n\n')
            w.write(' | '.join(f'top {k}: {v}/{len(covered)}' for k, v in recall.items()) + '\n\n')
            w.write('| known moment | at | best rank |\n|---|---|---|\n')
            for r in res:
                w.write(f'| {r["label"]} | {hms(r["start"])}-{hms(r["end"])} | {r["best_rank"] or "not in top 100"} |\n')
    with open(os.path.join(a.out_dir, 'moments.json'), 'w') as w:
        json.dump(out, w, indent=2)
    write_md(os.path.join(a.out_dir, 'moments.md'), out)
    print(f'ranked {len(rows)} windows over {covered_s / 3600:.2f} h in {runtime:.1f}s; '
          f'{found} strong moments; top {len(moments)} -> {os.path.join(a.out_dir, "moments.md")}')
    return 0


def write_md(path, out):
    src = out['source']
    with open(path, 'w') as w:
        w.write(f'# Moments{" from " + src["label"] if src.get("label") else ""}\n\n')
        cov = out['coverage']
        w.write(f'Scanned {cov["hours"]} h ({cov["segments"]} transcript segments, {cov["loudness_seconds"]} s of loudness). '
                f'{out["stats"]["windows_scored"]} windows scored, {out["stats"]["strong_moments"]} strong moments '
                f'({out["stats"]["strong_definition"]}).\n\n')
        w.write('Verify every pick on a frame sheet before it goes anywhere (frame_sheet.py), and credit the streamer.\n\n')
        w.write('| # | in-out | len | score | why | flags | they said |\n|---|---|---|---|---|---|---|\n')
        for m in out['moments']:
            when = f'[{m["start_hms"]}-{m["end_hms"]}]({m["link"]})' if m.get('link') else f'{m["start_hms"]}-{m["end_hms"]}'
            flags = '<br>'.join(m['flags']) or 'none'
            w.write(f'| {m["rank"]} | {when} | {m["duration"]:.0f}s | {m["score"]:.1f} | {"; ".join(m["reasons"])} | '
                    f'{flags} | {m["excerpt"].replace("|", "/")} |\n')


if __name__ == '__main__':
    sys.exit(main())
