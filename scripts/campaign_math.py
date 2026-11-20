#!/usr/bin/env python3
"""campaign_math.py: what a clipping campaign actually pays, with minimums, caps, fees and pools.

"Views x CPM" overstates payouts. Most campaigns pay nothing until a clip clears a minimum, stop
paying at a cap, and the platform keeps a fee. Pools split a fixed budget by share of views.

  # CPM campaign: $1 per 1,000 views, $10 minimum per clip, $500 cap, 10% fee
  python3 -I campaign_math.py cpm --cpm 1 --min-payout 10 --cap 500 --fee 0.10 --views 3000,50000,1000000
  # a month of clips with a realistic spread (count x views each)
  python3 -I campaign_math.py cpm --cpm 1 --min-payout 10 --cap 500 --fee 0.10 --dist 770x3000,30x50000,10x1000000
  # pool: a $10,000 pool, 100M qualifying views in total, your clips got 1M
  python3 -I campaign_math.py pool --budget 10000 --total-views 100000000 --views 1000000 --fee 0.10
  # views needed to net a target at a given rate
  python3 -I campaign_math.py target --net 1000 --cpm 1 --fee 0.10

Illustrative math, not a promise. Most clips earn $0. Rates, minimums and caps are set by each
campaign and change often; read the brief.
"""
import argparse
import json
import sys

DISCLAIMER = ('Illustrative math, not a promise. Most clips earn $0. Rates, minimums and caps are set by each '
              'campaign and change often; read the brief.')


def clip_payout(views, cpm, min_payout=0.0, cap=None):
    """Gross payout for one clip: views/1000*cpm, zero below the minimum, capped at the cap."""
    gross = views / 1000.0 * cpm
    if gross < min_payout:
        return 0.0
    if cap is not None and gross > cap:
        return float(cap)
    return gross


def parse_views(text):
    out = []
    for part in str(text).split(','):
        part = part.strip().lower().replace('_', '')
        if not part:
            continue
        mult = 1
        if part.endswith('k'):
            mult, part = 1000, part[:-1]
        elif part.endswith('m'):
            mult, part = 1_000_000, part[:-1]
        out.append(int(float(part) * mult))
    return out


def parse_dist(text):
    """'770x3000,30x50000' -> [(770, 3000), (30, 50000)]"""
    out = []
    for part in str(text).split(','):
        part = part.strip()
        if not part:
            continue
        n, v = part.lower().split('x', 1)
        out.append((int(n), parse_views(v)[0]))
    return out


def cpm_report(dist, cpm, min_payout, cap, fee):
    rows, total_views, naive, gross = [], 0, 0.0, 0.0
    for n, v in dist:
        each = clip_payout(v, cpm, min_payout, cap)
        rows.append({'clips': n, 'views_each': v, 'payout_each': round(each, 2), 'subtotal': round(each * n, 2),
                     'note': 'below the minimum' if each == 0 and v else ('capped' if cap is not None and v / 1000 * cpm > cap else '')})
        total_views += n * v
        naive += n * v / 1000.0 * cpm
        gross += each * n
    net = gross * (1 - fee)
    return {'rows': rows, 'clips': sum(n for n, _ in dist), 'total_views': total_views,
            'naive_views_x_cpm': round(naive, 2), 'gross': round(gross, 2), 'fee': fee, 'net': round(net, 2),
            'net_vs_naive': round(net / naive, 3) if naive else None, 'disclaimer': DISCLAIMER}


def pool_report(budget, total_views, views, fee):
    share = views / total_views if total_views else 0.0
    gross = budget * share
    return {'budget': budget, 'total_views': total_views, 'your_views': views, 'share': round(share, 6),
            'gross': round(gross, 2), 'net': round(gross * (1 - fee), 2),
            'effective_cpm': round(gross / views * 1000, 4) if views else None, 'disclaimer': DISCLAIMER}


def target_report(net, cpm, fee):
    paid_views = net / (1 - fee) / cpm * 1000 if cpm else None
    return {'net_target': net, 'cpm': cpm, 'fee': fee, 'paid_views_needed': round(paid_views) if paid_views else None,
            'per_day_30': round(paid_views / 30) if paid_views else None,
            'note': 'paid views only: views below a minimum or above a cap do not count', 'disclaimer': DISCLAIMER}


def money(x):
    return f'${x:,.2f}'


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0], epilog=__doc__.split('\n\n', 1)[1],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='mode')
    c = sub.add_parser('cpm', help='per-1,000-views campaign with minimum, cap and fee')
    c.add_argument('--cpm', type=float, required=True, help='dollars per 1,000 views')
    c.add_argument('--min-payout', type=float, default=0.0, help='a clip earns nothing below this')
    c.add_argument('--cap', type=float, help='most one clip can earn')
    c.add_argument('--fee', type=float, default=0.10, help='platform fee on payouts (Content Rewards: 0.10)')
    g = c.add_mutually_exclusive_group(required=True)
    g.add_argument('--views', help='comma-separated views per clip, e.g. 3000,50k,1m')
    g.add_argument('--dist', help='count x views, e.g. 770x3000,30x50000,10x1000000')
    p = sub.add_parser('pool', help='fixed budget split by share of views')
    p.add_argument('--budget', type=float, required=True)
    p.add_argument('--total-views', type=float, required=True, help='qualifying views across all creators')
    p.add_argument('--views', type=float, required=True, help='your qualifying views')
    p.add_argument('--fee', type=float, default=0.10)
    t = sub.add_parser('target', help='paid views needed to net a target')
    t.add_argument('--net', type=float, required=True)
    t.add_argument('--cpm', type=float, required=True)
    t.add_argument('--fee', type=float, default=0.10)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)
    if not a.mode:
        ap.print_help()
        return 2
    if a.mode == 'cpm':
        dist = parse_dist(a.dist) if a.dist else [(1, v) for v in parse_views(a.views)]
        r = cpm_report(dist, a.cpm, a.min_payout, a.cap, a.fee)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0
        print(f'rate {money(a.cpm)}/1K views, minimum {money(a.min_payout)} per clip, cap '
              f'{money(a.cap) if a.cap is not None else "none"}, fee {a.fee:.0%}\n')
        print(f'{"clips":>6} {"views each":>12} {"pays each":>11} {"subtotal":>11}  note')
        for row in r['rows']:
            print(f'{row["clips"]:>6} {row["views_each"]:>12,} {money(row["payout_each"]):>11} {money(row["subtotal"]):>11}  {row["note"]}')
        print(f'\n{r["clips"]} clips, {r["total_views"]:,} views')
        print(f'naive views x CPM: {money(r["naive_views_x_cpm"])}')
        print(f'actual gross:      {money(r["gross"])}')
        print(f'after {a.fee:.0%} fee:     {money(r["net"])}'
              + (f'  ({r["net_vs_naive"]:.0%} of the naive number)' if r['net_vs_naive'] is not None else ''))
    elif a.mode == 'pool':
        r = pool_report(a.budget, a.total_views, a.views, a.fee)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0
        print(f'pool {money(a.budget)} over {a.total_views:,.0f} qualifying views; yours {a.views:,.0f} ({r["share"]:.4%})')
        print(f'gross {money(r["gross"])}, after fee {money(r["net"])}, effective {money(r["effective_cpm"] or 0)} per 1K views')
        print('pools often require a minimum total (Reach GTA pools: 400K-750K views) before you qualify at all')
    else:
        r = target_report(a.net, a.cpm, a.fee)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0
        print(f'to net {money(a.net)} at {money(a.cpm)}/1K after a {a.fee:.0%} fee you need about '
              f'{r["paid_views_needed"]:,} PAID views (~{r["per_day_30"]:,} a day over 30 days)')
        print(r['note'])
    print('\n' + DISCLAIMER)
    return 0


if __name__ == '__main__':
    sys.exit(main())
