---
name: campaigns
description: Read-only research of clipping campaigns that pay per 1,000 views for GTA 6 and streamer clips (Content Rewards, Clipster, AffiliateNetwork, agency briefs). Records rates, minimums, caps, pools, platforms, geography and rules, flags unverified or regulated-sponsor campaigns, and runs honest payout math. Never signs up, logs in or submits. Use when the user asks where clips can earn or which campaigns to join.
argument-hint: "[streamer or game]"
---

# Clipping campaigns finder (read-only)

## Hard limits

Read public pages only (WebSearch, WebFetch). Never:
- create an account, sign in, join a campaign or accept terms;
- submit a clip;
- use the user's sessions.

Joining is the user's decision and the user's click.

## 1. Where to look

| Marketplace | Notes |
|---|---|
| Content Rewards (contentrewards.com; payouts via Whop) | Discover page and campaign pages; Creator Terms for fees and timing |
| Clipster (clipster.com) | General Campaign Rules |
| AffiliateNetwork (affiliatenetwork.com) | explore page |
| Agency briefs, e.g. Reach (reachclipping.com) | briefs for specific streamers |

Search `"<streamer> GTA 6 clipping campaign"` and the streamer's own channels. A program counts as official only when the streamer's own channel says so.

## 2. Record each campaign in `gtavi/campaigns.md`

| field | example |
|---|---|
| name, organisation, verified? | "Kai Cenat – $10,000 GTA 6 Pool", Clipped & Shipped, not verified |
| model | CPM / pool / per-post |
| rate | $ per 1,000 views |
| minimum payout → views needed | $10 → 10,000 views |
| cap per clip → views where pay stops | $500 → 500,000 views |
| budget or pool, creators, views so far | |
| platforms | TikTok, Shorts, Reels, X |
| geography | e.g. 50%+ of the audience in US/CA/AU/UK/NZ |
| rules | required tag, footage source, no logos, submission window |
| sponsor category | skip gambling, betting, crypto and cannabis |
| checked at (UTC) | |

## 3. Honest payout math

```bash
S="${CLAUDE_PLUGIN_ROOT}/scripts"
python3 -I "$S/campaign_math.py" cpm --cpm 1 --min-payout 10 --cap 500 --fee 0.10 --dist 770x3000,30x50000,10x1000000
python3 -I "$S/campaign_math.py" pool --budget 10000 --total-views 100000000 --views 1000000
python3 -I "$S/campaign_math.py" target --net 1000 --cpm 1 --fee 0.10
```

Always show the naive "views × CPM" number next to the real one. Label it "illustrative, not a promise". Most clips earn $0.

## 4. Context (snapshot, Nov 19 2026; re-check before quoting)

- Across 67 live CPM campaigns on Content Rewards, the median rate was about $1 per 1,000 views. Most paid between $0.35 and $3.
- Minimums ran $2 to $35 per clip, and caps $35 to $500.
- Content Rewards keeps 10% of CPM and pool payouts.
- Reach's GTA pools required 400K to 750K total views to qualify at all.
- About $70K in GTA VI campaign budgets was listed on that one marketplace on launch day.
- No official Speed or Kai clipping program was confirmed. The Kai pool on Content Rewards is run by an unverified agency.

## 5. Recommend

Shortlist 3 to 5 campaigns that fit the user's streamers, platforms and audience geography, with the math for each. Name the rules each clip must follow and add them to `clip-rules`. If a campaign is a regulated sponsor, unverified or unclear, say so plainly.
