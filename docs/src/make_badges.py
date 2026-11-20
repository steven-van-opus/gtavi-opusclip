#!/usr/bin/env python3
"""Static shields-style badges for the README (no network, no third-party badge service).

  python3 -I docs/src/make_badges.py docs/badges
"""
import os
import sys
from xml.sax.saxutils import escape

PINK = '#e0247a'
BADGES = [
    ('claude-code', 'Claude Code', 'plugin', PINK),
    ('skills', 'skills', '16', PINK),
    ('clips', 'clips', '1080x1920', PINK),
    ('opusclip', 'OpusClip', 'API + MCP', '#7b2ff7'),
    ('gta6', 'GTA 6', 'Nov 19', PINK),
    ('license', 'license', 'MIT', PINK),
]
# approximate Verdana 11px advance widths
NARROW = set('iljI.,:;|!\' ')
WIDE = set('mwMW@%')


def text_width(s):
    w = 0.0
    for ch in s:
        if ch in NARROW:
            w += 3.6
        elif ch in WIDE:
            w += 9.6
        elif ch.isupper() or ch.isdigit():
            w += 7.3
        elif ch in '+-x':
            w += 7.0
        else:
            w += 6.4
    return w


def badge(label, value, color):
    lw, vw = int(text_width(label) + 12), int(text_width(value) + 12)
    total = lw + vw
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{total}" height="20" role="img" aria-label="{escape(label)}: {escape(value)}">
<title>{escape(label)}: {escape(value)}</title>
<linearGradient id="s" x2="0" y2="100%"><stop offset="0" stop-color="#bbb" stop-opacity=".1"/><stop offset="1" stop-opacity=".1"/></linearGradient>
<clipPath id="r"><rect width="{total}" height="20" rx="3" fill="#fff"/></clipPath>
<g clip-path="url(#r)"><rect width="{lw}" height="20" fill="#555"/><rect x="{lw}" width="{vw}" height="20" fill="{color}"/><rect width="{total}" height="20" fill="url(#s)"/></g>
<g fill="#fff" text-anchor="middle" font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">
<text x="{lw / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{escape(label)}</text><text x="{lw / 2:.1f}" y="14">{escape(label)}</text>
<text x="{lw + vw / 2:.1f}" y="15" fill="#010101" fill-opacity=".3">{escape(value)}</text><text x="{lw + vw / 2:.1f}" y="14">{escape(value)}</text>
</g></svg>
'''


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else 'docs/badges'
    os.makedirs(out, exist_ok=True)
    for name, label, value, color in BADGES:
        with open(os.path.join(out, f'{name}.svg'), 'w') as w:
            w.write(badge(label, value, color))
    print(f'wrote {len(BADGES)} badges to {out}')


if __name__ == '__main__':
    main()
