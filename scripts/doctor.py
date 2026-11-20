#!/usr/bin/env python3
"""doctor.py: check that everything the desk needs is installed, without printing any secret.

  python3 -I doctor.py            # human checklist
  python3 -I doctor.py --json

Checks python, ffmpeg/ffprobe, yt-dlp, whisper-cli and a whisper.cpp model, node + puppeteer-core
(only for rendered title cards), Google Chrome, free disk space, and whether OPUSCLIP_API_KEY is set
(it reports set/unset, never the value). The OpusClip MCP connection is checked inside Claude Code
with /mcp, not here.
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_URL = 'https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin'


def version(cmd):
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
        txt = (out.stdout or out.stderr).strip().splitlines()
        return txt[0][:80] if txt else 'ok'
    except Exception as e:
        return f'error: {e}'


def find_model():
    sys.path.insert(0, HERE)
    try:
        from scan_vod import find_model as fm
        return fm()
    except Exception:
        return None


def checks():
    out = []

    def add(name, ok, detail, fix='', required=True):
        out.append({'check': name, 'ok': bool(ok), 'detail': detail, 'fix': fix, 'required': required})

    add('python >= 3.9', sys.version_info >= (3, 9), sys.version.split()[0], 'install a newer python3')
    for tool, fix in (('ffmpeg', 'brew install ffmpeg  (or apt install ffmpeg)'), ('ffprobe', 'comes with ffmpeg'),
                      ('yt-dlp', 'brew install yt-dlp  (or pipx install yt-dlp); keep it updated')):
        p = shutil.which(tool)
        add(tool, p, version([tool, '-version' if tool.startswith('ff') else '--version']) if p else 'not found', fix)
    w = shutil.which('whisper-cli')
    add('whisper-cli (whisper.cpp)', w, version(['whisper-cli', '--help'])[:40] if w else 'not found',
        'brew install whisper-cpp  (or build whisper.cpp)')
    m = find_model()
    add('whisper model', m, m or 'not found',
        f'mkdir -p ~/.cache/whisper.cpp && curl -L -o ~/.cache/whisper.cpp/ggml-large-v3-turbo-q5_0.bin {MODEL_URL}  '
        '(or set WHISPER_MODEL)')
    n = shutil.which('node')
    add('node (title cards only)', n, version(['node', '--version']) if n else 'not found', 'brew install node', required=False)
    deps = os.environ.get('RENDER_DEPS')
    pc = bool(deps and os.path.isdir(os.path.join(deps, 'node_modules', 'puppeteer-core'))) or \
        os.path.isdir(os.path.join(HERE, 'node_modules', 'puppeteer-core'))
    add('puppeteer-core (title cards only)', pc, deps or 'RENDER_DEPS not set',
        'npm install --prefix "$RENDER_DEPS" puppeteer-core@23', required=False)
    chrome = os.environ.get('CHROME_PATH') or next((p for p in ('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
                                                               '/usr/bin/google-chrome', '/usr/bin/chromium') if os.path.exists(p)), None)
    add('Google Chrome (title cards only)', chrome, chrome or 'not found', 'install Chrome or set CHROME_PATH', required=False)
    try:
        free = shutil.disk_usage(os.getcwd()).free / 1e9
        add('free disk >= 5 GB', free >= 5, f'{free:.1f} GB free here',
            'a 24 h audio track is ~350 MB at 32 kbps; 1080p windows are ~100-400 MB each; free some space')
    except OSError:
        pass
    key = os.environ.get('OPUSCLIP_API_KEY')
    add('OPUSCLIP_API_KEY (scripts only)', key, 'set' if key else 'not set',
        'clip.opus.pro/dashboard -> API Access -> Create key (Pro (limited API access), Max or Business); export it in your shell, '
        'never paste it into a file in this repo. Interactive use can go through the OpusClip MCP (/mcp) instead.',
        required=False)
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ('-h', '--help'):
        print(__doc__)
        return 0
    res = checks()
    if '--json' in argv:
        print(json.dumps(res, indent=2))
    else:
        for c in res:
            mark = 'ok ' if c['ok'] else ('-- ' if not c['required'] else 'XX ')
            print(f'[{mark}] {c["check"]:<36} {c["detail"]}')
            if not c['ok'] and c['fix']:
                print(f'       fix: {c["fix"]}')
        print('\nOpusClip MCP: inside Claude Code run /mcp and authenticate the "opusclip" server (OAuth in your browser).')
    return 0 if all(c['ok'] for c in res if c['required']) else 1


if __name__ == '__main__':
    sys.exit(main())
