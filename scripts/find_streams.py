#!/usr/bin/env python3
"""find_streams.py: find live and recent GTA 6 streams and VODs worth scanning. Read-only: nothing is downloaded.

  python3 -I find_streams.py                                     # YouTube search, newest first
  python3 -I find_streams.py --query "gta 6 kai cenat" --query "gta 6 ishowspeed" --max 10
  python3 -I find_streams.py --watchlist watchlist.json          # each channel's latest streams and VODs
  python3 -I find_streams.py --from-json saved.json              # parse a saved yt-dlp --flat-playlist -J result

Uses yt-dlp's flat listings only (titles, durations, view counts, live status): never `yt-dlp -J` on a
single 24-hour VOD, its info JSON can be hundreds of MB. Kick channels are listed as links to open.

The last column says what to do next:
  LIVE                   still streaming: OpusClip can't take a live stream; come back for the VOD
  VOD <=10h              OpusClip can import the link (use a time range to save credits)
  VOD >10h               too long for OpusClip in one go: /vodwatch it, then cut the windows and upload
"""
import argparse
import json
import shutil
import subprocess
import sys
import urllib.parse

DEFAULT_QUERIES = ['gta 6 live stream', 'gta 6 stream gameplay']
TEN_HOURS = 36000


def ytdlp_flat(target, limit):
    cmd = ['yt-dlp', '--flat-playlist', '-J', '--no-warnings', '--playlist-end', str(limit), target]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip()[-300:] or f'yt-dlp failed on {target}')
    return json.loads(out.stdout or '{}')


def entries_of(d):
    for e in (d or {}).get('entries') or []:
        if e and e.get('_type') == 'playlist':
            yield from entries_of(e)
        elif e:
            yield e


def classify(e):
    ls = (e.get('live_status') or '').lower()
    dur = e.get('duration')
    if ls == 'is_live':
        return 'LIVE', 'still streaming: wait for the VOD (OpusClip cannot take a live stream)'
    if ls == 'is_upcoming':
        return 'UPCOMING', 'scheduled'
    if dur and dur > TEN_HOURS:
        return 'VOD', f'VOD >10h ({dur / 3600:.1f} h): /vodwatch, cut windows, upload'
    if dur and (dur >= 3600 or ls in ('was_live', 'post_live')):
        return 'VOD', 'VOD <=10h: OpusClip URL import with a time range (vodwatch it first if it is long)'
    if dur:
        return 'VIDEO', 'short video, likely a highlight or re-upload: find and credit the original stream'
    return 'VIDEO', 'length unknown'


def search_target(query, n, sort):
    """Newest first through YouTube's own upload-date sort; relevance through ytsearchN."""
    if sort == 'date':
        return 'https://www.youtube.com/results?' + urllib.parse.urlencode({'search_query': query}) + '&sp=CAI%253D'
    return f'ytsearch{n}:{query}'


def normalize(e, platform='youtube', channel=None):
    url = e.get('url') or e.get('webpage_url') or ''
    if platform == 'youtube' and e.get('id') and not url.startswith('http'):
        url = f'https://www.youtube.com/watch?v={e["id"]}'
    status, nxt = classify(e)
    return {'platform': platform, 'status': status, 'hours': round((e.get('duration') or 0) / 3600, 2) or None,
            'views': e.get('view_count'), 'channel': e.get('channel') or e.get('uploader') or channel or '',
            'title': (e.get('title') or '').strip(), 'url': url, 'next': nxt}


def from_watchlist(path, limit):
    rows, links = [], []
    for ch in json.load(open(path)):
        name = ch.get('name') or '?'
        if ch.get('youtube'):
            handle = ch['youtube'] if ch['youtube'].startswith('http') else f'https://www.youtube.com/{ch["youtube"]}/streams'
            try:
                rows += [normalize(e, 'youtube', name) for e in entries_of(ytdlp_flat(handle, limit))]
            except Exception as ex:
                links.append(f'{name} YouTube: {handle} ({ex})')
        if ch.get('twitch'):
            url = f'https://www.twitch.tv/{ch["twitch"]}/videos?filter=archives&sort=time'
            try:
                rows += [normalize(e, 'twitch', name) for e in entries_of(ytdlp_flat(url, limit))]
            except Exception as ex:
                links.append(f'{name} Twitch: {url} ({ex})')
            links.append(f'{name} live on Twitch? https://www.twitch.tv/{ch["twitch"]}')
        if ch.get('kick'):
            links.append(f'{name} Kick VODs (open in a browser): https://kick.com/{ch["kick"]}/videos')
    return rows, links


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--query', action='append', help='YouTube search (repeatable); default: GTA 6 stream searches')
    ap.add_argument('--max', type=int, default=15, help='results per query or channel')
    ap.add_argument('--sort', default='date', choices=('date', 'relevance'), help='search order (default: newest first)')
    ap.add_argument('--watchlist', help='JSON list of {"name","youtube":"@handle","twitch":"login","kick":"slug"}')
    ap.add_argument('--from-json', help='parse a saved `yt-dlp --flat-playlist -J` result instead of searching')
    ap.add_argument('--min-hours', type=float, default=0.0, help='hide videos shorter than this (live streams always show)')
    ap.add_argument('--live-only', action='store_true')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    rows, links = [], []
    if a.from_json:
        rows = [normalize(e) for e in entries_of(json.load(open(a.from_json)))]
    else:
        if not shutil.which('yt-dlp'):
            print('error: yt-dlp is not installed (brew install yt-dlp)', file=sys.stderr)
            return 2
        if a.watchlist:
            r, links = from_watchlist(a.watchlist, a.max)
            rows += r
        if a.query or not a.watchlist:
            for q in a.query or DEFAULT_QUERIES:
                try:
                    rows += [normalize(e) for e in entries_of(ytdlp_flat(search_target(q, a.max, a.sort), a.max))]
                except Exception as ex:
                    links.append(f'search "{q}" failed: {ex}')
    seen, out = set(), []
    for r in rows:
        if r['url'] in seen:
            continue
        seen.add(r['url'])
        if a.live_only and r['status'] != 'LIVE':
            continue
        if r['status'] != 'LIVE' and a.min_hours and (r['hours'] or 0) < a.min_hours:
            continue
        out.append(r)
    out.sort(key=lambda r: (r['status'] != 'LIVE', -(r['views'] or 0)))
    if a.json:
        print(json.dumps({'results': out, 'links': links}, indent=2))
        return 0
    print(f'{"status":<8} {"hours":>5} {"views":>11}  {"channel":<20} title / url / next')
    for r in out:
        views = f'{r["views"]:,}' if isinstance(r['views'], int) else '-'
        hours = f'{r["hours"]:.1f}' if r['hours'] else '-'
        print(f'{r["status"]:<8} {hours:>5} {views:>11}  {r["channel"][:20]:<20} {r["title"][:70]}\n'
              f'{"":<29}{r["url"]}\n{"":<29}-> {r["next"]}')
    for l in links:
        print(f'* {l}')
    if not out:
        print('nothing found; try --query with a streamer name, or --watchlist')
    return 0


if __name__ == '__main__':
    sys.exit(main())
