#!/usr/bin/env python3
"""Transcribe 20-minute chunks straight from a ytfrag fragment directory while the download is still running.
Only for YouTube VODs still served as 2-second fragments (the ones `ytfrag.py track` downloads): a processed
VOD is one file that yt-dlp fetches, so there is nothing to overlap.
Writes the same transcript.jsonl format as scan_vod.py (chunk = 1200 s = 600 two-second fragments), so
scan_vod.py can resume from it later. Stops as soon as the assembled track file appears; a chunk whose
transcription finishes after that is dropped (scan_vod.py transcribes it), so no 20 minutes are written twice.
If YouTube processes the VOD before the track is assembled, don't resume this transcript on yt-dlp's
audio: the processed VOD can be on another timeline (vodwatch.py sets such rows aside by itself).
  python3 -I scan_frags.py FRAG_DIR OUT_DIR ASSEMBLED_PATH [--chunks 72] [--model PATH]
FRAG_DIR is the <out>.raw.frags<format>/ folder that `ytfrag.py track` fills (for example
speed_audio.m4a.raw.frags139/ for `ytfrag.py track <id> 139 speed_audio.m4a`); --chunks = ceil(VOD seconds / 1200).
The model is found the same way as in scan_vod.py (--model, $WHISPER_MODEL, ~/.cache/whisper.cpp/...)."""
import json, os, subprocess, sys, time
if len(sys.argv) >= 2 and sys.argv[1] in ('-h', '--help'):
    print(__doc__); sys.exit(0)
if len(sys.argv) < 4:
    print(__doc__); sys.exit(2)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan_vod import find_model, finished_chunks  # noqa: E402
frag_dir, out_dir, assembled = sys.argv[1], sys.argv[2], sys.argv[3]
nchunks = int(sys.argv[sys.argv.index('--chunks') + 1]) if '--chunks' in sys.argv else 72
MODEL = find_model(sys.argv[sys.argv.index('--model') + 1] if '--model' in sys.argv else None)
if not MODEL:
    print('no whisper.cpp model found: pass --model PATH or set WHISPER_MODEL'); sys.exit(2)
os.makedirs(out_dir, exist_ok=True)
tj = os.path.join(out_dir, 'transcript.jsonl')


def marked_done(path):
    """Chunks with a chunk_done marker, read without rewriting the file (scan_vod.py may be writing it by now)."""
    got = set()
    if os.path.exists(path):
        with open(path, encoding='utf-8', errors='replace') as f:
            for line in f:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get('marker') == 'chunk_done':
                    got.add(d.get('chunk'))
    return got


done = finished_chunks(tj)   # chunks with a chunk_done marker; lines of unfinished chunks are removed
for i in range(nchunks):
    if i in done:
        continue
    sqs = range(i * 600, i * 600 + 600)
    while True:
        if os.path.exists(assembled):
            print('assembled track exists; leaving the rest to scan_vod.py', flush=True); sys.exit(0)
        if not os.path.isdir(frag_dir):
            print(f'no fragment folder at {frag_dir}: `ytfrag.py track` keeps its fragments in <out>.raw.frags<format>/',
                  flush=True); sys.exit(2)
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
    if os.path.exists(assembled) or i in marked_done(tj):
        # The track was assembled while whisper ran, so scan_vod.py may already be on this chunk (or another run
        # finished it): appending now would put the same 20 minutes in the transcript twice.
        for p in (wav, base + '.json'):
            if os.path.exists(p):
                os.remove(p)
        if os.path.exists(assembled):
            print(f'assembled track appeared during chunk {i + 1}; leaving it and the rest to scan_vod.py', flush=True)
            sys.exit(0)
        continue
    segs = json.loads(open(base + '.json', 'rb').read().decode('utf-8', 'replace')).get('transcription', [])
    s0 = i * 1200
    lines = [json.dumps({'chunk': i, 'start': round(s0 + sg['offsets']['from'] / 1000, 2),
                         'end': round(s0 + sg['offsets']['to'] / 1000, 2), 'text': sg['text'].strip()}) + '\n'
             for sg in segs if sg['text'].strip()]
    lines.append(json.dumps({'chunk': i, 'start': s0, 'end': s0, 'text': '', 'marker': 'chunk_done'}) + '\n')
    with open(tj, 'a', encoding='utf-8') as w:    # one write per chunk; the marker goes last
        w.write(''.join(lines))
    os.remove(wav); os.remove(base + '.json')
    print(f'chunk {i + 1}/{nchunks} transcribed ({s0 / 3600:.2f} h)', flush=True)
