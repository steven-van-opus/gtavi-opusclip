#!/usr/bin/env python3
"""Scan a full-stream audio track for clip-worthy moments.

  python3 -I scan_vod.py AUDIO.m4a OUT_DIR [--chunk 1200] [--model PATH] [--threads 8] [--skip-whisper] [--skip-loudness]

Writes to OUT_DIR:
  loudness.tsv      per-second RMS / peak dBFS (stream time)
  transcript.jsonl  {"start","end","text"} whisper segments in stream time (resumable, chunk by chunk)
  windows.tsv       30 s windows ranked by an excitement score (loudness z-score + reaction cues + speech rate)
  top_windows.md    the top 150 windows with their transcript text, for a human/agent to read
Stream time = seconds from the start of the VOD (the audio track's first fragment is t=0).
Re-running resumes: finished 20-minute chunks are skipped. Needs ffmpeg and whisper-cli (whisper.cpp)
plus a ggml model: --model, $WHISPER_MODEL, or ~/.cache/whisper.cpp/ggml-large-v3-turbo-q5_0.bin
(rank_moments.py turns the outputs into picked moments with reasons and brand-safety flags).
"""
import json, math, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_NAMES = ('ggml-large-v3-turbo-q5_0.bin', 'ggml-large-v3-turbo.bin', 'ggml-large-v3-turbo-q8_0.bin',
               'ggml-medium.en.bin', 'ggml-small.en.bin', 'ggml-base.en.bin')
MODEL_DIRS = (os.path.join(HERE, '..', 'models'), os.path.join(HERE, '..', 'tools', 'models'),
              os.path.expanduser('~/.cache/whisper.cpp'), os.path.expanduser('~/.local/share/whisper.cpp'),
              os.path.expanduser('~/whisper.cpp/models'), '/opt/homebrew/share/whisper-cpp', '/usr/local/share/whisper-cpp')


def find_model(explicit=None):
    """--model, then $WHISPER_MODEL, then the usual folders (largest good model first)."""
    for c in (explicit, os.environ.get('WHISPER_MODEL')):
        if c and os.path.isfile(c):
            return os.path.abspath(c)
    for d in MODEL_DIRS:
        for name in MODEL_NAMES:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return os.path.abspath(p)
    return None


MODEL = find_model()
THREADS = '8'
CUES = re.compile(r"\b(chat|bro+|yo+|no way|what the|oh my god|omg|hell no|let'?s go+|bruh|nah+|wait|look|run|stop|help|cops?|police|wanted|crash|dead|died|wasted|busted|sorry|hulk|monster|gun|shoot|car|dog|crazy|insane|why|how|ha(ha)+|aah+|ah+)\b", re.I)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def duration(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=start_time,duration', '-of', 'default=nw=1', path],
                         capture_output=True, text=True).stdout
    kv = dict(l.split('=', 1) for l in out.strip().splitlines() if '=' in l)
    st = float(kv.get('start_time') or 0) if kv.get('start_time') not in (None, 'N/A') else 0.0
    return float(kv.get('duration') or 0), st


def loudness(audio, out_dir, start_off):
    path = os.path.join(out_dir, 'loudness.tsv')
    if os.path.exists(path):
        return path
    tmp = os.path.join(out_dir, 'astats.txt')
    run(['ffmpeg', '-loglevel', 'error', '-y', '-i', audio, '-af',
         'aresample=8000,asetnsamples=n=8000:p=0,astats=metadata=1:reset=1,'
         f'ametadata=mode=print:file={tmp}', '-f', 'null', '-'])
    rows, t, rms, pk = [], None, None, None
    for line in open(tmp):
        if line.startswith('frame:'):
            if t is not None:
                rows.append((t, rms, pk))
            m = re.search(r'pts_time:([\d.]+)', line)
            t, rms, pk = (float(m.group(1)) - start_off if m else None), None, None
        elif 'Overall.RMS_level=' in line:
            v = line.split('=')[1].strip(); rms = float(v) if v not in ('-inf', 'inf', 'nan') else -90.0
        elif 'Overall.Peak_level=' in line:
            v = line.split('=')[1].strip(); pk = float(v) if v not in ('-inf', 'inf', 'nan') else -90.0
    if t is not None:
        rows.append((t, rms, pk))
    with open(path, 'w') as w:
        w.write('t\trms_db\tpeak_db\n')
        for t, r, p in rows:
            if t is not None:
                w.write(f'{t:.1f}\t{r if r is not None else -90:.1f}\t{p if p is not None else -90:.1f}\n')
    os.remove(tmp)
    return path


def whisper(audio, out_dir, total, chunk, model, start_off):
    tj = os.path.join(out_dir, 'transcript.jsonl')
    done = set()
    if os.path.exists(tj):
        for line in open(tj):
            done.add(json.loads(line).get('chunk'))
    n = int(math.ceil(total / chunk))
    for i in range(n):
        if i in done:
            continue
        s = i * chunk
        wav = os.path.join(out_dir, f'_chunk{i}.wav')
        run(['ffmpeg', '-loglevel', 'error', '-y', '-ss', str(s + start_off), '-t', str(chunk), '-i', audio,
             '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', wav])
        base = os.path.join(out_dir, f'_chunk{i}')
        run(['whisper-cli', '-m', model, '-f', wav, '-l', 'en', '-t', THREADS, '-np', '-oj', '-bs', '1', '-bo', '1', '-of', base],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        segs = json.loads(open(base + '.json', 'rb').read().decode('utf-8', 'replace')).get('transcription', [])
        with open(tj, 'a') as w:
            for sg in segs:
                txt = sg['text'].strip()
                if not txt:
                    continue
                w.write(json.dumps({'chunk': i, 'start': round(s + sg['offsets']['from'] / 1000, 2),
                                    'end': round(s + sg['offsets']['to'] / 1000, 2), 'text': txt}) + '\n')
            w.write(json.dumps({'chunk': i, 'start': s, 'end': s, 'text': '', 'marker': 'chunk_done'}) + '\n')
        os.remove(wav)
        os.remove(base + '.json')
        print(f'chunk {i + 1}/{n} done ({s / 3600:.2f}h)', flush=True)
    return tj


def rank(out_dir, total):
    W = 30
    nw = int(total // W) + 1
    loud = [[] for _ in range(nw)]
    lp = os.path.join(out_dir, 'loudness.tsv')
    if os.path.exists(lp):
        for line in list(open(lp))[1:]:
            t, r, p = line.split('\t')
            k = int(float(t) // W)
            if 0 <= k < nw:
                loud[k].append(float(r))
    text = [[] for _ in range(nw)]
    tp = os.path.join(out_dir, 'transcript.jsonl')
    if os.path.exists(tp):
        for line in open(tp):
            d = json.loads(line)
            if d.get('marker') or not d['text']:
                continue
            k = int(d['start'] // W)
            if 0 <= k < nw:
                text[k].append(d['text'])
    feats = []
    for k in range(nw):
        L = loud[k]
        top = sorted(L)[-5:] if L else [-90]
        feats.append((sum(top) / len(top), ' '.join(text[k])))
    vals = [f[0] for f in feats if f[0] > -80]
    mu = sum(vals) / max(1, len(vals))
    sd = math.sqrt(sum((v - mu) ** 2 for v in vals) / max(1, len(vals))) or 1.0
    rows = []
    for k, (lv, tx) in enumerate(feats):
        cues = len(CUES.findall(tx))
        words = len(tx.split())
        score = (lv - mu) / sd + 0.35 * min(cues, 12) + 0.01 * min(words, 150)
        rows.append((score, k * W, lv, cues, words, tx))
    rows.sort(reverse=True)
    with open(os.path.join(out_dir, 'windows.tsv'), 'w') as w:
        w.write('score\tstart_s\tstart_hms\tloud_top5_db\tcues\twords\ttext\n')
        for sc, s, lv, cu, wd, tx in rows:
            w.write(f'{sc:.2f}\t{s}\t{hms(s)}\t{lv:.1f}\t{cu}\t{wd}\t{tx[:400]}\n')
    with open(os.path.join(out_dir, 'top_windows.md'), 'w') as w:
        w.write('# Top 150 windows (30 s) by excitement score\n\n')
        for sc, s, lv, cu, wd, tx in rows[:150]:
            w.write(f'## {hms(s)} (score {sc:.2f}, loud {lv:.1f} dB, cues {cu})\n{tx}\n\n')


def hms(s):
    s = int(s)
    return f'{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}'


if __name__ == '__main__':
    if len(sys.argv) >= 2 and sys.argv[1] in ('-h', '--help'):
        print(__doc__); sys.exit(0)
    if len(sys.argv) < 3:
        print(__doc__); sys.exit(2)
    audio, out_dir = sys.argv[1], sys.argv[2]
    opts = sys.argv[3:]
    chunk = int(opts[opts.index('--chunk') + 1]) if '--chunk' in opts else 1200
    model = find_model(opts[opts.index('--model') + 1]) if '--model' in opts else MODEL
    if '--threads' in opts:
        THREADS = opts[opts.index('--threads') + 1]
    if '--skip-whisper' not in opts and not model:
        print('no whisper.cpp model found: pass --model PATH or set WHISPER_MODEL '
              '(download: https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin)')
        sys.exit(2)
    os.makedirs(out_dir, exist_ok=True)
    dur, st = duration(audio)
    print(f'audio {dur / 3600:.2f} h, start offset {st:.2f}s', flush=True)
    if '--skip-loudness' not in opts:
        loudness(audio, out_dir, st)
        print('loudness done', flush=True)
    rank(out_dir, dur)
    if '--skip-whisper' not in opts:
        whisper(audio, out_dir, dur, chunk, model, st)
        rank(out_dir, dur)
    print('ranked windows written', flush=True)
