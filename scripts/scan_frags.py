#!/usr/bin/env python3
"""Transcribe 20-minute chunks straight from a ytfrag fragment directory while the download is still running.
Writes the same transcript.jsonl format as scan_vod.py (chunk = 1200 s = 600 two-second fragments), so
scan_vod.py can resume from it later. Stops as soon as the assembled track file appears.
  python3 -I scan_frags.py FRAG_DIR OUT_DIR ASSEMBLED_PATH [--chunks 72] [--model PATH]
FRAG_DIR is the <out>.frags139 folder that `ytfrag.py track` fills; --chunks = ceil(VOD seconds / 1200).
The model is found the same way as in scan_vod.py (--model, $WHISPER_MODEL, ~/.cache/whisper.cpp/...)."""
import json, os, subprocess, sys, time
if len(sys.argv) >= 2 and sys.argv[1] in ('-h', '--help'):
    print(__doc__); sys.exit(0)
if len(sys.argv) < 4:
    print(__doc__); sys.exit(2)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_vod import find_model  # noqa: E402
frag_dir, out_dir, assembled = sys.argv[1], sys.argv[2], sys.argv[3]
nchunks = int(sys.argv[sys.argv.index('--chunks') + 1]) if '--chunks' in sys.argv else 72
MODEL = find_model(sys.argv[sys.argv.index('--model') + 1] if '--model' in sys.argv else None)
if not MODEL:
    print('no whisper.cpp model found: pass --model PATH or set WHISPER_MODEL'); sys.exit(2)
os.makedirs(out_dir, exist_ok=True)
tj = os.path.join(out_dir, 'transcript.jsonl')
done = set()
if os.path.exists(tj):
    for line in open(tj):
        d = json.loads(line)
        if d.get('marker') == 'chunk_done':
            done.add(d['chunk'])
for i in range(nchunks):
    if i in done:
        continue
    sqs = range(i * 600, i * 600 + 600)
    while True:
        if os.path.exists(assembled) or not os.path.isdir(frag_dir):
            print('assembled track exists; leaving the rest to scan_vod.py', flush=True); sys.exit(0)
        have = sum(os.path.exists(os.path.join(frag_dir, f'{sq:07d}')) for sq in sqs)
        if have >= 598:
            break
        time.sleep(10)
    wav = os.path.join(out_dir, f'_f{i}.wav')
    ff = subprocess.Popen(['ffmpeg', '-loglevel', 'error', '-y', '-f', 'mp4', '-i', 'pipe:0', '-vn', '-af',
                           'aresample=async=1:first_pts=0', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', wav],
                          stdin=subprocess.PIPE)
    ok = True
    try:
        for sq in sqs:
            p = os.path.join(frag_dir, f'{sq:07d}')
            if os.path.exists(p):
                ff.stdin.write(open(p, 'rb').read())
    except (FileNotFoundError, BrokenPipeError):
        ok = False
    ff.stdin.close(); ff.wait()
    if not ok:
        os.path.exists(wav) and os.remove(wav)
        print('fragments vanished (assembly started); stopping', flush=True); sys.exit(0)
    base = os.path.join(out_dir, f'_f{i}')
    subprocess.run(['whisper-cli', '-m', MODEL, '-f', wav, '-l', 'en', '-t', '8', '-np', '-oj', '-bs', '1', '-bo', '1',
                    '-of', base], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    segs = json.loads(open(base + '.json', 'rb').read().decode('utf-8', 'replace')).get('transcription', [])
    s0 = i * 1200
    with open(tj, 'a') as w:
        for sg in segs:
            txt = sg['text'].strip()
            if txt:
                w.write(json.dumps({'chunk': i, 'start': round(s0 + sg['offsets']['from'] / 1000, 2),
                                    'end': round(s0 + sg['offsets']['to'] / 1000, 2), 'text': txt}) + '\n')
        w.write(json.dumps({'chunk': i, 'start': s0, 'end': s0, 'text': '', 'marker': 'chunk_done'}) + '\n')
    os.remove(wav); os.remove(base + '.json')
    print(f'chunk {i + 1}/{nchunks} transcribed ({s0 / 3600:.2f} h)', flush=True)
