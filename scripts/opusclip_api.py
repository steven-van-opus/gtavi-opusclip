#!/usr/bin/env python3
"""opusclip_api.py: a stdlib-only client for the OpusClip public REST API (https://api.opus.pro).

It follows the documented API ("Clip API" v1.0, https://help.opus.pro/api-reference/overview):
create a clipping project from a URL or an uploaded file, poll it, list the clips with titles and
scores, download them, and (only when you say so) write social copy, post or schedule them.

  export OPUSCLIP_API_KEY=...      # clip.opus.pro/dashboard -> API Access -> Create key (Pro Beta, Max, Business)

  # see the exact request first (nothing is sent, no key needed):
  python3 -I opusclip_api.py --dry-run create --url "https://www.youtube.com/watch?v=VOD" \\
      --start 1:00:00 --end 1:20:00 --prompt "biggest reactions and funniest chaos"
  # send it (spends credits), wait for the render, list the clips:
  python3 -I opusclip_api.py create --url ... --start 1:00:00 --end 1:20:00 --yes --wait
  python3 -I opusclip_api.py clips PROJECT_ID --top 10
  python3 -I opusclip_api.py download PROJECT_ID --top 5 --out clips/
  # a local window (cut with cut_windows.py), 4-step V2 upload then create:
  python3 -I opusclip_api.py create --file cuts/job01.mp4 --title "Speed, alligator" --yes

Rules this client enforces:
  * The key comes from the environment only. It is never printed, logged or written to disk;
    dry runs show the literal text $OPUSCLIP_API_KEY.
  * The Authorization header is sent to the API host only, never to upload or download URLs.
  * Anything that spends credits or publishes needs --yes: create, batch, censor, post, schedule,
    unschedule, share. Without --yes the request is printed and nothing is sent (exit code 3).
  * 30 requests/min client-side throttle, backoff on 429 (including X-Cap-Reason: concurrent),
    no retry on 403 API_MONTHLY_CAP_REACHED (exit code 4), POSTs are never retried after a 5xx.
  * curationPref.clipDurations is always sent; range needs both ends; customPrompt only with
    ClipAnything; topicKeywords only with ClipBasic; sources over 10 h are refused.

Exit codes: 0 ok, 1 API or project failure, 2 usage or config error, 3 needs --yes, 4 monthly cap reached.
"""
import argparse
import hashlib
import hmac
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

API_BASE = 'https://api.opus.pro'
KEY_ENV = 'OPUSCLIP_API_KEY'
GENRES = ('Auto', 'Q&A', 'Commentary', 'Marketing', 'Webinar', 'Motivational speech', 'Podcast', 'Academic',
          'Listicle', 'Product reviews', 'How-to', 'Comedy', 'Sports commentary', 'Church', 'News', 'Vlog',
          'Gaming', 'Others')
MODELS = ('ClipAnything', 'ClipBasic')
ASPECTS = ('portrait', 'landscape', 'square')
DOCUMENTED_DURATIONS = ((0, 30), (30, 60), (60, 90), (90, 180), (0, 90))
PRESET_TEMPLATES = ('preset-fancy-Karaoke', 'preset-fancy-Gameplay', 'preset-fancy-Beasty', 'preset-fancy-Deep_Diver',
                    'preset-fancy-Youshaei', 'preset-fancy-Pod_P', 'preset-fancy-Mozi', 'preset-fancy-Popline',
                    'preset-fancy-Simple', 'preset-fancy-Think_Media')
STAGES = ('PENDING', 'QUEUED', 'IMPORT', 'CURATE', 'REFINE', 'RENDER', 'UPLOAD', 'COMPLETE', 'STALLED')
MAX_SOURCE_SECONDS = 10 * 3600          # documented video limit: 10 h
MAX_UPLOAD_BYTES = 30 * 1024 ** 3       # documented video limit: 30 GB
API_MIN_CREDITS = 10                    # every API project costs at least 10 credits
PRIVACY = ('public', 'private', 'unlisted')
EXIT_OK, EXIT_API, EXIT_USAGE, EXIT_NEEDS_YES, EXIT_CAP = 0, 1, 2, 3, 4


class ApiError(Exception):
    def __init__(self, status, body, message=''):
        self.status, self.body = status, body
        self.code = body.get('code') if isinstance(body, dict) else None
        super().__init__(message or f'HTTP {status}: {str(body)[:300]}')


class CapReached(ApiError):
    """403 API_MONTHLY_CAP_REACHED: do not retry until reset_at."""


class UsageError(Exception):
    pass


class NeedsConfirmation(Exception):
    pass


class Response:
    def __init__(self, status, headers=None, body=b''):
        self.status = status
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.body = body if isinstance(body, bytes) else str(body).encode()

    def data(self):
        if not self.body:
            return None
        try:
            return json.loads(self.body.decode('utf-8', 'replace'))
        except ValueError:
            return self.body.decode('utf-8', 'replace')


def urllib_transport(method, url, headers, data=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return Response(r.status, dict(r.headers.items()), r.read())
    except urllib.error.HTTPError as e:
        body = b''
        try:
            body = e.read()
        except Exception:
            pass
        return Response(e.code, dict(e.headers.items()) if e.headers else {}, body)


def stream_put(url, path, headers, timeout=6 * 3600):
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        req = urllib.request.Request(url, data=f, method='PUT', headers={**headers, 'Content-Length': str(size)})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status


def stream_get(url, dest, timeout=600):
    tmp = dest + '.part'
    req = urllib.request.Request(url, headers={'User-Agent': 'gtavi-opusclip/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, 'wb') as w:
        shutil.copyfileobj(r, w, 1 << 20)
    os.replace(tmp, dest)
    return dest


# ---------------------------------------------------------------- pure helpers (unit-tested)

def parse_time(v):
    """'1:02:03.5' / '62:03' / '3723' -> seconds (float)."""
    if v is None or v == '':
        return None
    s = str(v).strip()
    if ':' not in s:
        return float(s)
    total = 0.0
    for part in s.split(':'):
        total = total * 60 + float(part)
    return total


def hms(sec):
    sec = int(round(sec or 0))
    return f'{sec // 3600}:{sec % 3600 // 60:02d}:{sec % 60:02d}'


def parse_durations(text):
    """'0-30,30-60' -> [[0, 30], [30, 60]]. Undocumented ranges are allowed but reported."""
    out, odd = [], []
    for part in str(text).split(','):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r'(\d+)\s*-\s*(\d+)', part)
        if not m:
            raise UsageError(f'bad --durations item {part!r}; use seconds like 0-30,30-60')
        a, b = int(m.group(1)), int(m.group(2))
        if not 0 <= a < b <= 900:
            raise UsageError(f'bad clip duration range {a}-{b}')
        if (a, b) not in DOCUMENTED_DURATIONS:
            odd.append(f'{a}-{b}')
        out.append([a, b])
    if not out:
        raise UsageError('--durations is empty')
    return out, odd


def estimate_credits(seconds):
    """1 credit per minute of processed source (under 1 min rounds up, partial minutes round down),
    with the 10-credit minimum for every API project."""
    if seconds is None:
        return None
    minutes = seconds / 60.0
    base = 1 if minutes < 1 else int(math.floor(minutes))
    return max(API_MIN_CREDITS, base)


def bare_clip_id(clip_id):
    """Social and censor endpoints want the clip part of '<projectId>.<clipId>'."""
    clip_id = str(clip_id)
    return clip_id.split('.', 1)[1] if '.' in clip_id else clip_id


def build_project_body(video_url, title=None, template=None, model='ClipAnything', genre='Gaming',
                       durations=((0, 30), (30, 60)), prompt=None, keywords=None, start=None, end=None,
                       auto_hook=False, aspect='portrait', captions=None, emoji=None, highlight=None,
                       remove_fillers=False, lang='en', webhook=None, email=None):
    if not video_url:
        raise UsageError('a source is required (--url, --upload-id or --file)')
    if model not in MODELS:
        raise UsageError(f'--model must be one of {MODELS}')
    if genre not in GENRES:
        raise UsageError(f'--genre must be one of: {", ".join(GENRES)}')
    if aspect not in ASPECTS:
        raise UsageError(f'--aspect must be one of {ASPECTS}')
    if prompt and model != 'ClipAnything':
        raise UsageError('--prompt (customPrompt) only works with --model ClipAnything')
    if keywords and model != 'ClipBasic':
        raise UsageError('--keywords (topicKeywords) only works with --model ClipBasic')
    body = {'videoUrl': video_url}
    if title:
        body['uploadedVideoAttr'] = {'title': title[:200]}
    if template:
        body['brandTemplateId'] = template
    cur = {'model': model, 'genre': genre, 'clipDurations': [list(d) for d in durations]}
    if prompt:
        cur['customPrompt'] = prompt
    if keywords:
        cur['topicKeywords'] = [k.strip() for k in (keywords.split(',') if isinstance(keywords, str) else keywords) if k.strip()]
    if start is not None or end is not None:
        if start is None or end is None:
            raise UsageError('a range needs both --start and --end (leave both out to process the whole video)')
        if not 0 <= start < end:
            raise UsageError(f'bad range {start}-{end}')
        if end - start > MAX_SOURCE_SECONDS:
            raise UsageError('range is longer than the 10 h OpusClip limit')
        cur['range'] = {'startSec': int(math.floor(start)), 'endSec': int(math.ceil(end))}
    if auto_hook:
        cur['enableAutoHook'] = True
    body['curationPref'] = cur
    rp = {'layoutAspectRatio': aspect}
    for key, val in (('enableCaption', captions), ('enableEmoji', emoji), ('enableHighlight', highlight)):
        if val is not None:
            rp[key] = bool(val)
    if remove_fillers:
        rp['quickstartConfig'] = {'enableRemoveFillerWords': True}
    body['renderPref'] = rp
    body['importPref'] = {'sourceLang': lang or 'auto'}
    actions = []
    if webhook:
        actions.append({'type': 'WEBHOOK', 'url': webhook, 'notifyFailure': True})
    if email:
        actions.append({'type': 'EMAIL', 'email': email, 'notifyFailure': True})
    if actions:
        body['conclusionActions'] = actions
    return body


def unwrap(d):
    """Some endpoints answer {data: ...}; others answer the object or array directly."""
    if isinstance(d, dict) and 'data' in d and len(d) <= 3:
        return d['data']
    return d


def _clip_times(c):
    tr = c.get('timeRanges') or []
    if not tr or not isinstance(tr[0], (list, tuple)) or len(tr[0]) < 2:
        return None, None
    a, b = float(tr[0][0]), float(tr[-1][1])
    dur_ms = c.get('durationMs')
    # documented as seconds, but the documented example is milliseconds: pick whichever fits durationMs
    if dur_ms:
        as_ms = abs((b - a) - dur_ms)
        as_s = abs((b - a) * 1000 - dur_ms)
        if as_ms <= as_s:
            return a / 1000, b / 1000
        return a, b
    return (a / 1000, b / 1000) if b > 200000 else (a, b)


def normalize_clip(c, position=0):
    cid = str(c.get('id') or '')
    project_id = c.get('projectId') or (cid.split('.', 1)[0] if '.' in cid else None)
    clip_part = bare_clip_id(cid) if cid else (c.get('curationId') or '')
    start, end = _clip_times(c)
    judge = c.get('judgeResult') or {}
    return {
        'id': cid or f'{project_id}.{clip_part}', 'projectId': project_id, 'clipId': clip_part,
        'title': c.get('title') or '', 'description': c.get('description') or '',
        'hashtags': c.get('hashtags') or [], 'durationSec': round((c.get('durationMs') or 0) / 1000, 1),
        'sourceStart': start, 'sourceEnd': end,
        'score': c.get('score'), 'rank': c.get('rank'),
        'hookScore': judge.get('hookScore'), 'coherenceScore': judge.get('coherenceScore'),
        'connectionScore': judge.get('connectionScore'), 'trendScore': judge.get('trendScore'),
        'uriForExport': c.get('uriForExport'), 'uriForPreview': c.get('uriForPreview'),
        'uriForThumbnail': c.get('uriForThumbnail'), 'position': position,
    }


def sort_clips(clips):
    def key(c):
        s = c.get('score')
        r = c.get('rank')
        return (0 if isinstance(s, (int, float)) else 1, -(s or 0), r if isinstance(r, (int, float)) else 1e9, c['position'])
    return sorted(clips, key=key)


def slug(text, n=40):
    s = re.sub(r'[^a-zA-Z0-9]+', '-', text or '').strip('-').lower()
    return (s[:n].rstrip('-') or 'clip')


def verify_webhook(raw, headers, secret, seen, now=None, tolerance=300):
    """X-Opus-Signature = hex HMAC-SHA256(raw body + X-Opus-Salt) keyed with the org's API secret.
    The timestamp is not signed, so remembered salts block replays."""
    h = {k.lower(): v for k, v in headers.items()}
    sig, salt, ts = h.get('x-opus-signature', ''), h.get('x-opus-salt', ''), h.get('x-opus-timestamp', '')
    if not (sig and salt and ts.isdigit()) or salt in seen:
        return False
    want = hmac.new(secret.encode(), raw + salt.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(want, sig):
        return False
    if abs((now if now is not None else time.time()) - int(ts)) > tolerance:
        return False
    seen.add(salt)
    return True


def probe_media(path):
    """(duration_s, fps_fraction_string) via ffprobe, or (None, None) when ffprobe is missing."""
    if not shutil.which('ffprobe'):
        return None, None
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
                          'stream=r_frame_rate:format=duration', '-of', 'json', path], capture_output=True, text=True)
    try:
        d = json.loads(out.stdout or '{}')
    except ValueError:
        return None, None
    dur = d.get('format', {}).get('duration')
    fps = (d.get('streams') or [{}])[0].get('r_frame_rate')
    return (float(dur) if dur not in (None, 'N/A') else None), fps


def fps_is_integer(fps):
    if not fps or '/' not in fps:
        return True
    num, den = fps.split('/')
    try:
        return float(num) / float(den) == round(float(num) / float(den))
    except (ValueError, ZeroDivisionError):
        return True


# ---------------------------------------------------------------- client

class OpusClip:
    def __init__(self, api_key=None, base_url=API_BASE, org_id=None, dry_run=False, transport=None,
                 put_file=None, get_file=None, sleep=time.sleep, clock=time.monotonic, min_interval=2.0,
                 out=None, err=None, key_env=KEY_ENV):
        self.api_key, self.base_url, self.org_id = api_key, base_url.rstrip('/'), org_id
        self.dry_run = dry_run
        self.transport = transport or urllib_transport
        self.put_file = put_file or stream_put
        self.get_file = get_file or stream_get
        self.sleep, self.clock, self.min_interval = sleep, clock, min_interval
        self._out, self._err = out, err
        self.key_env = key_env
        self._last = None
        self.dry_counter = 0
        self.api_host = urllib.parse.urlparse(self.base_url).netloc

    # -- plumbing
    @property
    def out(self):
        return self._out or sys.stdout

    @property
    def err(self):
        return self._err or sys.stderr

    def log(self, msg):
        print(msg, file=self.err, flush=True)

    def _throttle(self):
        if self._last is not None and self.min_interval:
            wait = self.min_interval - (self.clock() - self._last)
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def _url(self, path, query=None):
        url = self.base_url + path
        if query:
            url += '?' + urllib.parse.urlencode(query)
        return url

    def _headers(self, json_body, org=False, redact=False):
        h = {'Accept': 'application/json', 'User-Agent': 'gtavi-opusclip/1.0'}
        if json_body:
            h['Content-Type'] = 'application/json'
        h['Authorization'] = f'Bearer ${self.key_env}' if redact else f'Bearer {self.api_key}'
        if org and self.org_id:
            h['x-opus-org-id'] = self.org_id
        return h

    def print_request(self, method, url, headers, body=None, note=None):
        if note:
            print(f'# {note}', file=self.out)
        print(f'{method} {url}', file=self.out)
        for k, v in headers.items():
            print(f'{k}: {v}', file=self.out)
        if body is not None:
            print('', file=self.out)
            print(body if isinstance(body, str) else json.dumps(body, indent=2), file=self.out)
        print('', file=self.out)

    def _dry_answer(self, method, path):
        self.dry_counter += 1
        n = self.dry_counter
        if method == 'POST' and path == '/api/clip-projects':
            return {'id': f'DRYRUN_PROJECT_{n}', 'projectId': f'DRYRUN_PROJECT_{n}', 'stage': 'DRY_RUN'}
        if method == 'POST' and path == '/api/upload-links-v2':
            return {'url': '<url from step 1>', 'uploadId': '<uploadId from step 1>',
                    'requiredHeaders': {'<every header in requiredHeaders>': '<as returned>'}}
        if method == 'POST' and 'social-copy-jobs' in path:
            return {'data': {'jobId': f'DRYRUN_JOB_{n}'}}
        if method == 'POST' and 'post-tasks' in path:
            return {'data': {'postId': f'DRYRUN_POST_{n}'}}
        if method == 'POST' and 'publish-schedules' in path:
            return {'data': {'scheduleId': f'DRYRUN_SCHEDULE_{n}'}}
        if method == 'POST' and 'censor-jobs' in path:
            return {'jobId': f'DRYRUN_CENSOR_{n}', 'message': 'dry run'}
        return {'dryRun': True}

    def api(self, method, path, query=None, body=None, org=False, retries=5):
        url = self._url(path, query)
        if self.dry_run:
            self.print_request(method, url, self._headers(body is not None, org, redact=True), body)
            return self._dry_answer(method, path)
        if not self.api_key:
            raise UsageError(f'{self.key_env} is not set. Create a key at https://clip.opus.pro/dashboard '
                             '(API Access -> Create key; API access needs Pro Beta, Max or Business), then '
                             f'export {self.key_env}=... in your shell. Use --dry-run to preview requests without a key.')
        data = json.dumps(body).encode() if body is not None else None
        attempt = 0
        while True:
            self._throttle()
            resp = self.transport(method, url, self._headers(body is not None, org), data)
            if 200 <= resp.status < 300:
                return resp.data()
            payload = resp.data()
            if resp.status == 403 and isinstance(payload, dict) and payload.get('code') == 'API_MONTHLY_CAP_REACHED':
                raise CapReached(403, payload, f'monthly API cap reached; resets at {payload.get("reset_at")}. '
                                               f'Not retrying. More: {payload.get("upgrade_url", "https://www.opus.pro/contact-api")}')
            if resp.status == 429 and attempt < retries:
                wait = self._retry_wait(resp, attempt)
                why = 'concurrent-project limit' if resp.headers.get('x-cap-reason') == 'concurrent' else 'rate limit'
                self.log(f'429 ({why}); waiting {wait:.0f}s before retry {attempt + 1}/{retries}')
                self.sleep(wait)
                attempt += 1
                continue
            if resp.status >= 500 and method == 'GET' and attempt < min(retries, 3):
                self.log(f'HTTP {resp.status}; retrying in {5 * (attempt + 1)}s')
                self.sleep(5 * (attempt + 1))
                attempt += 1
                continue
            if resp.status == 401:
                raise ApiError(401, payload, f'401 Unauthorized: check {self.key_env} '
                                             '(API access needs Pro Beta, Max or Business)')
            raise ApiError(resp.status, payload)

    def _retry_wait(self, resp, attempt):
        ra = resp.headers.get('retry-after')
        if ra and re.fullmatch(r'\d+(\.\d+)?', ra.strip()):
            return min(120.0, max(1.0, float(ra)))
        reset = resp.headers.get('x-ratelimit-reset')
        if reset and re.fullmatch(r'\d+', reset.strip()):
            r = int(reset)
            wait = r - time.time() if r > 1_000_000_000 else r
            if 0 < wait <= 120:
                return float(wait) + 1
        if resp.headers.get('x-cap-reason') == 'concurrent':
            return 30.0 * (attempt + 1)
        return float(min(60, 5 * 2 ** attempt))

    # -- projects
    def create_project(self, body):
        return unwrap(self.api('POST', '/api/clip-projects', body=body))

    def project(self, project_id):
        return unwrap(self.api('GET', f'/api/clip-projects/{urllib.parse.quote(project_id)}'))

    def wait(self, project_id, interval=30, timeout=4 * 3600, quiet=False):
        t0 = self.clock()
        last = None
        while True:
            p = self.project(project_id)
            stage = (p or {}).get('stage')
            if stage != last and not quiet:
                self.log(f'{project_id}: {stage}')
                last = stage
            if (p or {}).get('error') or stage == 'STALLED':
                return p
            if stage == 'COMPLETE':
                return p
            if self.dry_run:
                return p
            if self.clock() - t0 > timeout:
                raise ApiError(0, p, f'timed out after {timeout}s waiting for {project_id} (last stage {stage})')
            self.sleep(interval)

    def clips(self, project_id, page_size=50, max_pages=20):
        out = []
        for page in range(1, max_pages + 1):
            d = unwrap(self.api('GET', '/api/exportable-clips', query={'q': 'findByProjectId', 'projectId': project_id,
                                                                      'pageNum': page, 'pageSize': page_size}, org=True))
            items = d if isinstance(d, list) else (d or {}).get('items', []) if isinstance(d, dict) else []
            out.extend(items)
            if len(items) < page_size or self.dry_run:
                break
        return sort_clips([normalize_clip(c, i) for i, c in enumerate(out)])

    def transcript(self, project_id):
        return self.api('GET', '/api/transcripts', query={'q': 'findByProjectId', 'projectId': project_id})

    def templates(self):
        return unwrap(self.api('GET', '/api/brand-templates', query={'q': 'mine'}))

    def usage(self):
        return unwrap(self.api('GET', '/api/api-usage', query={'q': 'mine'}))

    def share(self, project_id, public=True):
        return self.api('POST', f'/api/clip-projects/{urllib.parse.quote(project_id)}/update-visibility',
                        body={'visibility': 'PUBLIC' if public else 'DEFAULT'})

    # -- upload (V2, four steps)
    def upload(self, path):
        size = os.path.getsize(path)
        link = unwrap(self.api('POST', '/api/upload-links-v2', body={'usecase': 'LocalUpload'})) or {}
        url, upload_id = link.get('url'), link.get('uploadId')
        required = dict(link.get('requiredHeaders') or {})
        if self.dry_run:
            self.print_request('POST', '<url from step 1>',
                               {'<every header in requiredHeaders, exactly as returned>': '...', 'Content-Length': '0'},
                               note='step 2: start the resumable upload; read the Location response header (no API key on this host)')
            self.print_request('PUT', '<Location header from step 2>',
                               {'Content-Type': 'application/octet-stream', 'Content-Length': str(size)},
                               f'<{size} bytes of {os.path.basename(path)}>', note='step 3: upload the file bytes')
            return upload_id
        if not url or not upload_id:
            raise ApiError(0, link, 'upload-links-v2 answered without url/uploadId')
        headers = {**required, 'Content-Length': '0'}
        headers.pop('Authorization', None)
        resp = self.transport('POST', url, headers, b'')
        location = resp.headers.get('location')
        if not (200 <= resp.status < 300) or not location:
            raise ApiError(resp.status, resp.data(), 'could not start the resumable upload (no Location header)')
        self.log(f'uploading {os.path.basename(path)} ({size / 1e6:.1f} MB)')
        try:
            status = self.put_file(location, path, {'Content-Type': 'application/octet-stream'})
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, None, f'upload PUT failed with HTTP {e.code}')
        if status not in (200, 201):
            raise ApiError(status, None, f'upload PUT returned {status}')
        return upload_id

    # -- censor
    def censor(self, project_id, clip_id, beep=True):
        return self.api('POST', '/api/censor-jobs', body={'projectId': project_id, 'clipId': bare_clip_id(clip_id),
                                                          'options': {'beepSound': bool(beep)}})

    def censor_status(self, job_id):
        return self.api('GET', f'/api/censor-jobs/{urllib.parse.quote(job_id)}')

    # -- social
    def accounts(self):
        d = unwrap(self.api('GET', '/api/social-accounts', query={'q': 'mine'}))
        return d if isinstance(d, list) else []

    def social_copy(self, project_id, clip_id, account, sub_account=None, prompt=None, regenerate=False,
                    wait=True, interval=2.0, timeout=300):
        body = {'projectId': project_id, 'clipId': bare_clip_id(clip_id), 'postAccountId': account}
        if sub_account:
            body['subAccountId'] = sub_account
        if prompt:
            body['prompt'] = prompt
        if regenerate:
            body['forceRegenerate'] = True
        job = unwrap(self.api('POST', '/api/social-copy-jobs', body=body)) or {}
        job_id = job.get('jobId')
        if not wait or self.dry_run:
            return {'jobId': job_id, 'status': 'DRY_RUN' if self.dry_run else 'RUNNING'}
        t0 = self.clock()
        while True:
            d = unwrap(self.api('GET', f'/api/social-copy-jobs/{urllib.parse.quote(str(job_id))}')) or {}
            if d.get('status') in ('COMPLETED', 'FAILED'):
                d.setdefault('jobId', job_id)
                return d
            if self.clock() - t0 > timeout:
                raise ApiError(0, d, f'social copy job {job_id} did not finish in {timeout}s')
            self.sleep(interval)

    @staticmethod
    def post_body(project_id, clip_id, account, title, sub_account=None, description=None, privacy=None,
                  media_type=None, publish_at=None):
        if not title:
            raise UsageError('--title is required to post or schedule')
        if privacy and privacy not in PRIVACY:
            raise UsageError(f'--privacy must be one of {PRIVACY}')
        detail = {'title': title}
        if media_type:
            detail['mediaType'] = media_type
        custom = {}
        if description:
            custom['description'] = description
        if privacy:
            custom['privacy'] = privacy
        if custom:
            detail['custom'] = custom
        body = {'projectId': project_id, 'clipId': bare_clip_id(clip_id), 'postAccountId': account, 'postDetail': detail}
        if sub_account:
            body['subAccountId'] = sub_account
        if publish_at:
            body['publishAt'] = publish_at
        return body

    def post_now(self, body):
        return unwrap(self.api('POST', '/api/post-tasks', body=body))

    def schedule(self, body):
        return unwrap(self.api('POST', '/api/publish-schedules', body=body))

    def unschedule(self, schedule_id):
        return self.api('DELETE', f'/api/publish-schedules/{urllib.parse.quote(schedule_id)}')

    # -- downloads (never send the API key to the media host)
    def download_clips(self, clips, out_dir, preview=False):
        os.makedirs(out_dir, exist_ok=True)
        saved = []
        for i, c in enumerate(clips, 1):
            url = c.get('uriForPreview') if preview else (c.get('uriForExport') or c.get('uriForPreview'))
            name = f'{i:02d}_{slug(c.get("title"))}_{slug(c.get("clipId"), 24)}.mp4'
            dest = os.path.join(out_dir, name)
            entry = {**c, 'file': name}
            if not url:
                entry['error'] = 'no download URL on this clip'
            elif self.dry_run:
                self.print_request('GET', url, {'User-Agent': 'gtavi-opusclip/1.0'}, note=f'download -> {dest} (no API key sent)')
            elif os.path.exists(dest):
                self.log(f'exists, skipping: {dest}')
            else:
                self.log(f'downloading {name}')
                self.get_file(url, dest)
            saved.append(entry)
        if not self.dry_run:
            with open(os.path.join(out_dir, 'clips.json'), 'w') as w:
                json.dump(saved, w, indent=2)
        return saved


# ---------------------------------------------------------------- CLI

def _need_yes(args, what):
    if getattr(args, 'yes', False) or args.dry_run:
        return
    raise NeedsConfirmation(f'{what}\nNothing was sent. Re-run with --yes to send it, or with --dry-run to print the exact request.')


def _emit(args, data, human=None):
    if args.json or human is None:
        print(json.dumps(data, indent=2, default=str))
    else:
        human(data)


def _print_clips(clips):
    if not clips:
        print('no clips yet')
        return
    print(f'{"#":>2}  {"score*":>6}  {"dur":>5}  {"source in-out":<19}  {"clip id":<22}  title')
    for i, c in enumerate(clips, 1):
        sc = '-' if c.get('score') is None else f'{c["score"]:.0f}' if isinstance(c['score'], (int, float)) else str(c['score'])
        span = f'{hms(c["sourceStart"])}-{hms(c["sourceEnd"])}' if c.get('sourceStart') is not None else ''
        print(f'{i:>2}  {sc:>6}  {c["durationSec"]:>5.1f}  {span:<19}  {c["clipId"][:22]:<22}  {c["title"][:70]}')
    print('\n* score: the clip-level score field OpusClip returns (OpusClip\'s own CLI reads it; it is not in the public '
          'schema). The app shows it as the Virality Score (0-99), a prediction, not a promise.')


def _source_from_args(args, client):
    """Returns (videoUrl, seconds_to_bill_or_None, note)."""
    picks = [x for x in (args.url, args.upload_id, args.file) if x]
    if len(picks) != 1:
        raise UsageError('give exactly one of --url, --upload-id, --file')
    start, end = parse_time(args.start), parse_time(args.end)
    span = (end - start) if (start is not None and end is not None) else None
    if args.file:
        if not os.path.isfile(args.file):
            raise UsageError(f'no such file: {args.file}')
        size = os.path.getsize(args.file)
        if size > MAX_UPLOAD_BYTES:
            raise UsageError('file is over the 30 GB upload limit')
        dur, fps = probe_media(args.file)
        if dur and dur > MAX_SOURCE_SECONDS:
            raise UsageError(f'file is {dur / 3600:.1f} h; OpusClip takes up to 10 h. Cut windows first (cut_windows.py).')
        if fps and not fps_is_integer(fps) and not args.allow_fractional_fps:
            raise UsageError(f'file frame rate is {fps}; OpusClip local uploads need an integer frame rate. '
                             'cut_windows.py normalizes this, or pass --allow-fractional-fps to try anyway.')
        upload_id = client.upload(args.file)
        return upload_id, span if span is not None else dur, f'uploaded {os.path.basename(args.file)}'
    if args.url:
        host = urllib.parse.urlparse(args.url).netloc.lower()
        if 'kick.com' in host:
            client.log('note: the API source list names Twitch but not Kick; if this import fails, '
                       'cut the window locally (cut_windows.py) and use --file.')
        return args.url, span, 'url import'
    return args.upload_id, span, 'existing upload'


def cmd_create(args, client):
    if len([x for x in (args.url, args.upload_id, args.file) if x]) != 1:
        raise UsageError('give exactly one of --url, --upload-id, --file')
    if args.file and not os.path.isfile(args.file):
        raise UsageError(f'no such file: {args.file}')
    durations, odd = parse_durations(args.durations)
    if odd:
        client.log(f'note: clip duration ranges {", ".join(odd)} are not in the documented list '
                   f'({", ".join(f"{a}-{b}" for a, b in DOCUMENTED_DURATIONS)})')
    start, end = parse_time(args.start), parse_time(args.end)
    # validate everything before any upload happens
    build_project_body('validate://', args.title, args.template, args.model, args.genre, durations, args.prompt,
                       args.keywords, start, end, args.auto_hook, args.aspect, args.captions, args.emoji,
                       args.highlight, args.remove_fillers, args.lang, args.webhook, args.email)
    span_hint = (end - start) if start is not None and end is not None else None
    if args.file and span_hint is None:
        span_hint = probe_media(args.file)[0]
    credits = estimate_credits(span_hint)
    cost = f'about {credits} credits' if credits else 'the whole video length in credits (1 per minute, 10 minimum)'
    _need_yes(args, f'This creates an OpusClip project and spends {cost}.')
    video_url, seconds, note = _source_from_args(args, client)
    body = build_project_body(video_url, args.title, args.template, args.model, args.genre, durations, args.prompt,
                              args.keywords, start, end, args.auto_hook, args.aspect, args.captions, args.emoji,
                              args.highlight, args.remove_fillers, args.lang, args.webhook, args.email)
    project = client.create_project(body) or {}
    pid = project.get('projectId') or project.get('id')
    if client.dry_run:
        client.log(f'dry run: nothing was sent ({note}); a real run would cost {cost}')
    else:
        client.log(f'project {pid} created ({note}); stage {project.get("stage")}; estimated {cost}')
    result = {'projectId': pid, 'stage': project.get('stage'), 'estimatedCredits': credits, 'request': body}
    if args.wait and pid:
        p = client.wait(pid, interval=args.interval, timeout=args.timeout)
        result['stage'] = (p or {}).get('stage')
        result['error'] = (p or {}).get('error')
        if result['stage'] == 'COMPLETE' or client.dry_run:
            result['clips'] = client.clips(pid)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f'projectId: {pid}\nstage: {result["stage"]}\nestimated credits: {credits if credits else "whole video"}')
        if result.get('clips') is not None:
            _print_clips(result['clips'])
    return EXIT_API if result.get('error') or result.get('stage') == 'STALLED' else EXIT_OK


def cmd_status(args, client):
    p = client.project(args.project_id) or {}
    _emit(args, p, lambda d: print(f'{args.project_id}: stage {d.get("stage")}' + (f', error {d.get("error")}' if d.get('error') else '')))
    return EXIT_API if p.get('error') or p.get('stage') == 'STALLED' else EXIT_OK


def cmd_wait(args, client):
    p = client.wait(args.project_id, interval=args.interval, timeout=args.timeout) or {}
    _emit(args, p, lambda d: print(f'{args.project_id}: {d.get("stage")}' + (f' error: {d.get("error")}' if d.get('error') else '')))
    return EXIT_OK if p.get('stage') in ('COMPLETE', 'DRY_RUN') or client.dry_run else EXIT_API


def cmd_clips(args, client):
    clips = client.clips(args.project_id)
    if args.top:
        clips = clips[:args.top]
    if args.out:
        with open(args.out, 'w') as w:
            json.dump(clips, w, indent=2)
        client.log(f'wrote {args.out}')
    _emit(args, clips, _print_clips)
    return EXIT_OK


def cmd_download(args, client):
    clips = client.clips(args.project_id)
    if args.top:
        clips = clips[:args.top]
    saved = client.download_clips(clips, args.out, preview=args.preview)
    _emit(args, saved, lambda s: print(f'{len(s)} clip(s) -> {args.out}'))
    return EXIT_OK


def cmd_transcript(args, client):
    d = client.transcript(args.project_id)
    if args.out:
        with open(args.out, 'w') as w:
            json.dump(d, w, indent=2)
        client.log(f'wrote {args.out}')
        return EXIT_OK

    def human(d):
        paras = unwrap(d) or []
        for para in paras:
            for seg in (para if isinstance(para, list) else [para]):
                if isinstance(seg, dict):
                    print(f'[{hms(seg.get("start", 0))}] {seg.get("text", "")}')
    _emit(args, d, human)
    return EXIT_OK


def cmd_templates(args, client):
    if args.presets:
        _emit(args, list(PRESET_TEMPLATES), lambda d: print('\n'.join(d)))
        return EXIT_OK
    d = client.templates() or []

    def human(rows):
        for t in rows if isinstance(rows, list) else []:
            print(f'{t.get("templateId", "?"):<36} {"(default) " if t.get("isDefault") else ""}{t.get("name", "")}')
        print('\npresets: ' + ', '.join(PRESET_TEMPLATES))
    _emit(args, d, human)
    return EXIT_OK


def cmd_usage(args, client):
    d = client.usage() or {}

    def human(u):
        m, c = (u or {}).get('monthly') or {}, (u or {}).get('concurrent') or {}
        print(f'monthly: used {m.get("used")} of {m.get("limit")} (remaining {m.get("remaining")}, resets {m.get("reset_at")})')
        print(f'concurrent projects: {c.get("used")} of {c.get("limit")}')
        print('(api-usage is not in the public docs; OpusClip\'s own CLI calls it)')
    _emit(args, d, human)
    return EXIT_OK


def cmd_upload(args, client):
    if not os.path.isfile(args.file):
        raise UsageError(f'no such file: {args.file}')
    uid = client.upload(args.file)
    _emit(args, {'uploadId': uid}, lambda d: print(f'uploadId: {d["uploadId"]}'))
    return EXIT_OK


def cmd_share(args, client):
    _need_yes(args, f'This makes project {args.project_id} {"private (DEFAULT)" if args.private else "PUBLIC"} immediately.')
    d = client.share(args.project_id, public=not args.private)
    _emit(args, d, lambda _: print(f'{args.project_id}: visibility {"DEFAULT" if args.private else "PUBLIC"}'))
    return EXIT_OK


def cmd_censor(args, client):
    _need_yes(args, f'This starts a censor job on clip {args.clip_id} (bleeps or masks profanity).')
    d = client.censor(args.project_id, args.clip_id, beep=not args.no_beep)
    _emit(args, d, lambda x: print(f'censor job: {(x or {}).get("jobId")}'))
    return EXIT_OK


def cmd_censor_status(args, client):
    d = client.censor_status(args.job_id)
    _emit(args, d, lambda x: print(f'{args.job_id}: {(unwrap(x) or {}).get("status")}'))
    return EXIT_OK


def cmd_accounts(args, client):
    rows = client.accounts()

    def human(rs):
        if not rs:
            print('no connected social accounts (connect them in the OpusClip app)')
        for a in rs:
            print(f'{a.get("platform", "?"):<20} {a.get("extUserName", ""):<28} postAccountId={a.get("postAccountId")}'
                  + (f' subAccountId={a.get("subAccountId")}' if a.get('subAccountId') else ''))
    _emit(args, rows, human)
    return EXIT_OK


def cmd_copy(args, client):
    d = client.social_copy(args.project_id, args.clip_id, args.account, args.sub_account, args.prompt, args.regenerate)

    def human(x):
        print(f'status: {x.get("status")}\ntitle: {x.get("title", "")}\ndescription: {x.get("description", "")}\n'
              f'hashtags: {" ".join(x.get("hashtags") or [])}')
    _emit(args, d, human)
    return EXIT_OK if d.get('status') != 'FAILED' else EXIT_API


def _publish_at(text):
    from datetime import datetime, timezone
    s = text.strip().replace('Z', '+00:00')
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise UsageError('--at must be ISO 8601, e.g. 2026-11-20T17:00:00-05:00 or 2026-11-20T22:00:00Z')
    if dt.tzinfo is None:
        raise UsageError('--at needs a timezone offset (e.g. -05:00 or Z) so the time is unambiguous')
    dt = dt.astimezone(timezone.utc)
    if dt <= datetime.now(timezone.utc):
        raise UsageError('--at must be in the future')
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def cmd_post(args, client):
    body = client.post_body(args.project_id, args.clip_id, args.account, args.title, args.sub_account,
                            args.description, args.privacy, args.media_type)
    _need_yes(args, 'This PUBLISHES the clip now on the connected account (posts to X cost 3 credits per clip per account).\n'
                    + json.dumps(body, indent=2))
    d = client.post_now(body) or {}
    _emit(args, d, lambda x: print(f'postId: {x.get("postId")}'))
    return EXIT_OK


def cmd_schedule(args, client):
    when = _publish_at(args.at)
    body = client.post_body(args.project_id, args.clip_id, args.account, args.title, args.sub_account,
                            args.description, args.privacy, args.media_type, publish_at=when)
    _need_yes(args, f'This SCHEDULES the clip to publish at {when} (posts to X cost 3 credits per clip per account).\n'
                    + json.dumps(body, indent=2))
    d = client.schedule(body) or {}
    _emit(args, d, lambda x: print(f'scheduleId: {x.get("scheduleId")} (publishes {when})'))
    return EXIT_OK


def cmd_unschedule(args, client):
    _need_yes(args, f'This cancels scheduled post {args.schedule_id}.')
    d = client.unschedule(args.schedule_id)
    _emit(args, d, lambda _: print(f'cancelled {args.schedule_id}'))
    return EXIT_OK


def cmd_batch(args, client):
    """Create one project per job in a plan.json written by plan_jobs.py (resumable: done jobs are skipped)."""
    with open(args.plan) as f:
        plan = json.load(f)
    jobs = plan.get('jobs') or []
    todo = [j for j in jobs if not j.get('projectId') and not j.get('skip')]
    total = sum(j.get('estimatedCredits') or 0 for j in todo)
    _need_yes(args, f'This creates {len(todo)} OpusClip project(s) for about {total} credits in total '
                    f'(10-credit minimum per API project).')
    missing = [j.get('id') for j in todo if j.get('route') == 'upload' and not (j.get('file') and os.path.isfile(j['file']))]
    if missing and not client.dry_run:
        raise UsageError(f'job(s) {", ".join(map(str, missing))} need a local cut first: run cut_windows.py --plan {args.plan}')
    running = []
    for job in todo:
        while len(running) >= args.max_concurrent and not client.dry_run:
            still = []
            for pid in running:
                st = (client.project(pid) or {}).get('stage')
                if st not in ('COMPLETE', 'STALLED'):
                    still.append(pid)
            running = still
            if len(running) >= args.max_concurrent:
                client.log(f'{len(running)} projects still processing; waiting {args.interval}s (limit {args.max_concurrent})')
                client.sleep(args.interval)
        body = dict(job['request'])
        if job.get('route') == 'upload':
            path = job.get('file')
            if path and os.path.isfile(path):
                body['videoUrl'] = client.upload(path)
            elif client.dry_run:
                print(f'# job {job.get("id")}: the 4-step V2 upload of its local cut runs here '
                      f'(cut_windows.py --plan writes the file)\n', file=client.out)
                body['videoUrl'] = '<uploadId of the uploaded cut>'
            else:
                raise UsageError(f'job {job.get("id")}: cut file {path!r} not found; run cut_windows.py --plan {args.plan}')
        project = client.create_project(body) or {}
        pid = project.get('projectId') or project.get('id')
        job['projectId'] = pid
        job['stage'] = project.get('stage')
        running.append(pid)
        client.log(f'job {job.get("id")}: project {pid}')
        if not client.dry_run:
            with open(args.plan, 'w') as w:
                json.dump(plan, w, indent=2)
    if args.wait and not client.dry_run:
        for job in jobs:
            if job.get('projectId'):
                p = client.wait(job['projectId'], interval=args.interval, timeout=args.timeout) or {}
                job['stage'] = p.get('stage')
                job['error'] = p.get('error')
        with open(args.plan, 'w') as w:
            json.dump(plan, w, indent=2)
    def human(rows):
        for r in rows:
            print(f'{str(r.get("id")):<8} {str(r.get("route")):<7} {hms(r.get("start"))}-{hms(r.get("end"))}  '
                  f'{r.get("projectId") or "-":<24} {r.get("stage") or ""}')
    _emit(args, [{k: j.get(k) for k in ('id', 'route', 'start', 'end', 'projectId', 'stage', 'estimatedCredits')} for j in jobs],
          human)
    return EXIT_OK


def build_parser():
    p = argparse.ArgumentParser(prog='opusclip_api.py', description=__doc__.split('\n\n')[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter,
                                epilog='Run "opusclip_api.py <command> -h" for the options of a command. Docs: '
                                       'https://help.opus.pro/api-reference/overview')
    p.add_argument('--dry-run', action='store_true', help='print the exact HTTP requests, send nothing (no key needed)')
    p.add_argument('--json', action='store_true', help='machine-readable output')
    p.add_argument('--base-url', default=os.environ.get('OPUSCLIP_BASE_URL', API_BASE), help=argparse.SUPPRESS)
    p.add_argument('--org-id', default=os.environ.get('OPUSCLIP_ORG_ID'),
                   help='x-opus-org-id header for clip listing (needed if you belong to several orgs; env OPUSCLIP_ORG_ID)')
    p.add_argument('--key-env', default=KEY_ENV, help=f'environment variable that holds the API key (default {KEY_ENV})')
    p.add_argument('--min-interval', type=float, default=2.0, help='seconds between API requests (default 2.0 = 30/min)')
    sub = p.add_subparsers(dest='cmd', metavar='command')

    c = sub.add_parser('create', help='create a clipping project from a URL, an uploadId or a local file (spends credits)')
    src = c.add_argument_group('source (exactly one)')
    src.add_argument('--url', help='YouTube, Twitch, Kick, Vimeo, Drive, ... link (VOD must be finished processing)')
    src.add_argument('--upload-id', help='uploadId from a previous "upload"')
    src.add_argument('--file', help='local video (<=10 h, <=30 GB, integer frame rate); uploaded with the 4-step V2 flow')
    c.add_argument('--title', help='uploadedVideoAttr.title')
    c.add_argument('--start', help='range start (seconds or h:mm:ss); needs --end')
    c.add_argument('--end', help='range end (seconds or h:mm:ss); needs --start')
    c.add_argument('--model', default='ClipAnything', choices=MODELS, help='ClipAnything (default here; multimodal, takes prompts) or ClipBasic (API default)')
    c.add_argument('--prompt', help='customPrompt (ClipAnything only; prompting is a Pro/Business feature)')
    c.add_argument('--keywords', help='comma-separated topicKeywords (ClipBasic only)')
    c.add_argument('--genre', default='Gaming', help='curationPref.genre (default Gaming)')
    c.add_argument('--durations', default='0-30,30-60', help='clipDurations in seconds, e.g. 0-30,30-60 (always sent)')
    c.add_argument('--template', default='preset-fancy-Gameplay', help='brandTemplateId: a preset (see "templates --presets") or your own; "" for the account default')
    c.add_argument('--aspect', default='portrait', choices=ASPECTS, help='renderPref.layoutAspectRatio')
    c.add_argument('--lang', default='en', help='importPref.sourceLang (ISO-639 code or auto)')
    c.add_argument('--auto-hook', action='store_true', help='curationPref.enableAutoHook (off by default for API projects)')
    for name in ('captions', 'emoji', 'highlight'):
        g = c.add_mutually_exclusive_group()
        g.add_argument(f'--{name}', dest=name, action='store_true', default=None, help=f'force renderPref.enable{name.capitalize() if name != "captions" else "Caption"} on')
        g.add_argument(f'--no-{name}', dest=name, action='store_false', default=None, help='force it off (default: the template decides)')
    c.add_argument('--remove-fillers', action='store_true', help='renderPref.quickstartConfig.enableRemoveFillerWords')
    c.add_argument('--webhook', help='conclusionActions WEBHOOK url (signed; verify with verify_webhook())')
    c.add_argument('--email', help='conclusionActions EMAIL address')
    c.add_argument('--allow-fractional-fps', action='store_true', help='upload even if the frame rate is not an integer')
    c.add_argument('--wait', action='store_true', help='poll until COMPLETE, then list the clips')
    c.add_argument('--interval', type=int, default=30, help='poll interval in seconds')
    c.add_argument('--timeout', type=int, default=4 * 3600, help='give up waiting after this many seconds')
    c.add_argument('--yes', action='store_true', help='actually send it (spends credits)')
    c.set_defaults(fn=cmd_create)

    for name, fn, hlp in (('status', cmd_status, 'project details and stage'), ('wait', cmd_wait, 'poll a project until COMPLETE or STALLED')):
        s = sub.add_parser(name, help=hlp)
        s.add_argument('project_id')
        if name == 'wait':
            s.add_argument('--interval', type=int, default=30)
            s.add_argument('--timeout', type=int, default=4 * 3600)
        s.set_defaults(fn=fn)

    s = sub.add_parser('clips', help='list a project\'s clips (title, score, duration, source range), best first')
    s.add_argument('project_id')
    s.add_argument('--top', type=int, default=0)
    s.add_argument('--out', help='also save the list as JSON')
    s.set_defaults(fn=cmd_clips)

    s = sub.add_parser('download', help='download the rendered MP4s (uriForExport) of the best clips')
    s.add_argument('project_id')
    s.add_argument('--top', type=int, default=0)
    s.add_argument('--out', default='clips')
    s.add_argument('--preview', action='store_true', help='download the preview MP4 instead of the export')
    s.set_defaults(fn=cmd_download)

    s = sub.add_parser('transcript', help='source transcript of a project (billed range only)')
    s.add_argument('project_id')
    s.add_argument('--out')
    s.set_defaults(fn=cmd_transcript)

    s = sub.add_parser('templates', help='list your brand templates (or --presets for the documented preset IDs)')
    s.add_argument('--presets', action='store_true', help='print the documented preset IDs, no API call')
    s.set_defaults(fn=cmd_templates)

    s = sub.add_parser('usage', help='API usage: monthly credits used/limit and concurrent projects (unofficial endpoint)')
    s.set_defaults(fn=cmd_usage)

    s = sub.add_parser('upload', help='upload a local file (4-step V2 flow) and print its uploadId')
    s.add_argument('file')
    s.set_defaults(fn=cmd_upload)

    s = sub.add_parser('batch', help='create the projects in a plan.json from plan_jobs.py (spends credits; resumable)')
    s.add_argument('plan')
    s.add_argument('--max-concurrent', type=int, default=4, help='projects processing at once (Pro Beta/Max limit: 4)')
    s.add_argument('--wait', action='store_true')
    s.add_argument('--interval', type=int, default=30)
    s.add_argument('--timeout', type=int, default=4 * 3600)
    s.add_argument('--yes', action='store_true')
    s.set_defaults(fn=cmd_batch)

    s = sub.add_parser('share', help='make a project PUBLIC (or --private to set it back); takes effect immediately')
    s.add_argument('project_id')
    s.add_argument('--private', action='store_true')
    s.add_argument('--yes', action='store_true')
    s.set_defaults(fn=cmd_share)

    s = sub.add_parser('censor', help='bleep or mask profanity in one clip')
    s.add_argument('project_id')
    s.add_argument('clip_id')
    s.add_argument('--no-beep', action='store_true', help='mute instead of a beep sound')
    s.add_argument('--yes', action='store_true')
    s.set_defaults(fn=cmd_censor)
    s = sub.add_parser('censor-status', help='status of a censor job')
    s.add_argument('job_id')
    s.set_defaults(fn=cmd_censor_status)

    s = sub.add_parser('accounts', help='connected social accounts (postAccountId / subAccountId)')
    s.set_defaults(fn=cmd_accounts)

    s = sub.add_parser('copy', help='AI title, description and hashtags for one clip on one account (does not post)')
    s.add_argument('project_id')
    s.add_argument('clip_id')
    s.add_argument('--account', required=True, help='postAccountId')
    s.add_argument('--sub-account')
    s.add_argument('--prompt')
    s.add_argument('--regenerate', action='store_true')
    s.set_defaults(fn=cmd_copy)

    for name, fn, hlp in (('post', cmd_post, 'PUBLISH a clip now to a connected account'),
                          ('schedule', cmd_schedule, 'SCHEDULE a clip (publishAt, UTC)')):
        s = sub.add_parser(name, help=hlp)
        s.add_argument('project_id')
        s.add_argument('clip_id')
        s.add_argument('--account', required=True, help='postAccountId')
        s.add_argument('--sub-account')
        s.add_argument('--title', required=True)
        s.add_argument('--description')
        s.add_argument('--privacy', choices=PRIVACY)
        s.add_argument('--media-type', help='postDetail.mediaType, if the platform needs one')
        if name == 'schedule':
            s.add_argument('--at', required=True, help='ISO 8601 with offset, e.g. 2026-11-20T17:00:00-05:00')
        s.add_argument('--yes', action='store_true')
        s.set_defaults(fn=fn)

    s = sub.add_parser('unschedule', help='cancel a scheduled post')
    s.add_argument('schedule_id')
    s.add_argument('--yes', action='store_true')
    s.set_defaults(fn=cmd_unschedule)
    return p


def main(argv=None, client=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, 'fn', None):
        parser.print_help()
        return EXIT_USAGE
    if args.cmd == 'create' and args.template == '':
        args.template = None
    client = client or OpusClip(api_key=os.environ.get(args.key_env), base_url=args.base_url, org_id=args.org_id,
                                dry_run=args.dry_run, min_interval=args.min_interval, key_env=args.key_env)
    client.dry_run = client.dry_run or args.dry_run
    try:
        return args.fn(args, client)
    except NeedsConfirmation as e:
        print(str(e), file=sys.stderr)
        return EXIT_NEEDS_YES
    except UsageError as e:
        print(f'error: {e}', file=sys.stderr)
        return EXIT_USAGE
    except CapReached as e:
        print(f'error: {e}', file=sys.stderr)
        return EXIT_CAP
    except ApiError as e:
        print(f'API error: {e}', file=sys.stderr)
        return EXIT_API


if __name__ == '__main__':
    sys.exit(main())
