#!/usr/bin/env python3
"""copy_kit.py: per-platform captions for one clip, with the credit line, disclosure and length checks built in.

  python3 -I copy_kit.py --hook "Speed tried to hug the Red Hulk... it did not go well" \\
      --streamer IShowSpeed --source-platform YouTube --tags gta6,ishowspeed
  python3 -I copy_kit.py --hook "..." --streamer "Kai Cenat" --source-platform Twitch \\
      --credit "twitch.tv/KaiCenat" --campaign-tag "#KAI" --paid --json

What it enforces (warnings, not silent edits):
  * a credit line on every caption ("🎥 IShowSpeed (YouTube)", or your --credit text)
  * #ad first when the post is paid or part of a paid campaign (--paid); also switch on the
    platform's own label (X Paid Partnership, TikTok content disclosure, IG Paid partnership)
  * the campaign's required tag when you pass one (e.g. Reach briefs ask for the streamer's #TAG)
  * platform length limits (conservative defaults; platforms change them, so check)
  * phrases that imply affiliation or promise money ("official", "partnered", "guaranteed",
    "get rich", "while you sleep", "autopilot", "passive income") and leak/spoiler words
"""
import argparse
import json
import re
import sys

LIMITS = {  # conservative character limits; check the platform's current numbers
    'tiktok': {'caption': 2200, 'hashtags': (3, 5)},
    'shorts': {'title': 100, 'caption': 5000, 'hashtags': (2, 3)},
    'reels': {'caption': 2200, 'hashtags': (3, 5)},
    'x': {'caption': 280, 'hashtags': (0, 1)},
}
RISKY = [
    (r'\bofficial\b', 'reads as official: you are not Rockstar or the streamer'),
    (r'\bpartner(ed|ship)?\b(?!.*#ad)', 'implies a partnership'),
    (r'\b(endorsed|sponsored by (rockstar|ishowspeed|speed|kai))\b', 'implies endorsement'),
    (r'\bguarantee\w*\b', 'promises an outcome'),
    (r'\b(get rich|while you sleep|on autopilot|autopilot|passive income|easy money|millionaire)\b', 'money hype the FTC and TikTok treat as earnings claims'),
    (r'\b(leak\w*|datamine\w*|pre-?release|early access build)\b', 'leak or pre-release wording: Rockstar removes leaked footage'),
    (r'\b(ending|final mission|spoiler)\b', 'possible story spoiler: label it or skip it'),
    (r'\bmade with opusclip\b', 'only say this when OpusClip actually produced the clip'),
]


def credit_line(streamer, source_platform, credit=None):
    if credit:
        return credit if credit.startswith('🎥') else f'🎥 {credit}'
    return f'🎥 {streamer} ({source_platform})' if source_platform else f'🎥 {streamer}'


def hashtags(tags, lo_hi, campaign_tag=None):
    tags = [t.strip().lstrip('#') for t in (tags or []) if t.strip()]
    lo, hi = lo_hi
    out = []
    if campaign_tag:
        out.append(campaign_tag if campaign_tag.startswith('#') else '#' + campaign_tag)
    for t in tags:
        h = '#' + re.sub(r'\s+', '', t)
        if h.lower() not in (x.lower() for x in out):
            out.append(h)
    return out[:max(hi, 1 if campaign_tag else 0)]


def build(hook, streamer, source_platform, tags=None, credit=None, campaign_tag=None, paid=False, extra=''):
    credit_txt = credit_line(streamer, source_platform, credit)
    posts = {}
    for plat, lim in LIMITS.items():
        tags_p = hashtags(tags, lim['hashtags'], campaign_tag)
        lines = []
        if paid:
            lines.append('#ad')
        if plat == 'shorts':
            title = hook if len(hook) <= lim['title'] else hook[:lim['title'] - 1].rstrip() + '…'
            body = '\n'.join(filter(None, [('#ad ' if paid else '') + extra, credit_txt, ' '.join(tags_p)]))
            text = {'title': title, 'description': body.strip()}
            chars = {'title': len(title), 'description': len(body)}
        else:
            lines.append(hook)
            if extra:
                lines.append(extra)
            lines.append(credit_txt)
            if tags_p:
                lines.append(' '.join(tags_p))
            text = '\n\n'.join([lines[0] + ' ' + lines[1]] + lines[2:]) if paid else '\n\n'.join(lines)
            chars = len(text)
        warnings = check(text if isinstance(text, str) else text['title'] + '\n' + text['description'], lim, chars, plat,
                         paid, campaign_tag, credit_txt)
        posts[plat] = {'text': text, 'chars': chars, 'limit': lim, 'warnings': warnings}
    return {'credit': credit_txt, 'posts': posts,
            'reminders': ['Put the credit on screen too (top or bottom band), not only in the caption.',
                          'Paid campaign or free credits from a brand: #ad goes first AND switch on the platform disclosure label.',
                          'Read the campaign brief: required tags, no logos/watermarks, footage rules, submission window.']}


def check(text, lim, chars, plat, paid, campaign_tag, credit_txt):
    w = []
    cap = lim.get('caption')
    if isinstance(chars, int) and cap and chars > cap:
        w.append(f'{chars} characters is over the {cap} limit for {plat}')
    if isinstance(chars, dict) and chars['title'] > lim['title']:
        w.append(f'title over {lim["title"]} characters')
    for rx, why in RISKY:
        if re.search(rx, text, re.I) and not (why.startswith('implies a partnership') and paid):
            w.append(f'"{re.search(rx, text, re.I).group(0)}": {why}')
    if credit_txt not in text:
        w.append('credit line missing')
    if paid and not text.lstrip().startswith('#ad') and '#ad' not in text.split('\n')[0]:
        w.append('#ad must come first on a paid post')
    if campaign_tag and campaign_tag.lstrip('#').lower() not in text.lower():
        w.append(f'campaign tag {campaign_tag} missing')
    return w


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--hook', required=True, help='first line: what happens, why keep watching (no spoilers)')
    ap.add_argument('--streamer', required=True, help='who is on screen, e.g. IShowSpeed, Kai Cenat')
    ap.add_argument('--source-platform', default='', help='where the stream ran: YouTube, Twitch, Kick')
    ap.add_argument('--credit', help='your own credit text instead of the default, e.g. "twitch.tv/KaiCenat"')
    ap.add_argument('--tags', default='gta6', help='comma-separated hashtags without #')
    ap.add_argument('--campaign-tag', help='tag a campaign brief requires, e.g. "#LACY"')
    ap.add_argument('--extra', default='', help='an optional second line (context, your own commentary)')
    ap.add_argument('--paid', action='store_true', help='paid post or part of a paid campaign: #ad first')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    r = build(a.hook, a.streamer, a.source_platform, a.tags.split(','), a.credit, a.campaign_tag, a.paid, a.extra)
    if a.json:
        print(json.dumps(r, indent=2, ensure_ascii=False))
        return 0
    for plat, p in r['posts'].items():
        print(f'===== {plat} ({p["chars"]} chars)')
        if isinstance(p['text'], dict):
            print(f'TITLE: {p["text"]["title"]}\nDESCRIPTION:\n{p["text"]["description"]}')
        else:
            print(p['text'])
        for w in p['warnings']:
            print(f'  ! {w}')
        print()
    for rem in r['reminders']:
        print(f'- {rem}')
    return 1 if any(p['warnings'] for p in r['posts'].values()) else 0


if __name__ == '__main__':
    sys.exit(main())
