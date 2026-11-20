---
name: launch-day
description: An hour-by-hour playbook for a big game launch or marathon stream day (built around GTA 6, Nov 19 2026), covering prep, watching the live streams, scanning VODs as they land, the first wave of clips, compilations and the daily loop. Use when a launch or a huge stream is coming up, or the user asks what to do today.
argument-hint: "[date or event]"
---

# Launch-day playbook

## What happened at GTA 6's launch (Streams Charts figures)

- **Rolling unlock.** GTA 6 unlocked at local midnight country by country: New Zealand first, at 11:00 UTC on Nov 18 2026, and the US at 05:00 UTC on Nov 19.
- **Record audience.** The game peaked at 7.91M concurrent viewers, the biggest game launch in streaming history, with 125M hours watched in the first 24 h.
- **IShowSpeed.** The most-watched GTA 6 streamer on day one (4.47M hours watched). His first GTA 6 stream ran 23 h 45 min.
- **Kai Cenat.** Live almost nonstop from the NZ unlock, in parts of about 12 h each on YouTube.

Biggest takeaway: there is far more footage than anyone can watch. **Finding the moments fast is the edge.**

## T-24 h: prep

- [ ] `setup` skill passes (doctor, OpusClip MCP or API key, work folder).
- [ ] Watchlist of 5 to 10 streamers, handles confirmed (`find-streams`).
- [ ] Campaign research done and briefs saved (`campaigns`). Rules copied into `clip-rules`.
- [ ] 3 or 4 caption looks ready in OpusClip (Brand Kits or templates). Gameplay layout chosen.
- [ ] Page bios carry "Fan clips. Not affiliated with Rockstar Games or the streamers shown."
- [ ] Calendar slots set (`post_calendar.py`), with the audience time zone decided.

## T0 to T+12 h: streams are live

- Every hour, `find_streams.py --watchlist gtavi/watchlist.json --live-only`. Note which streams are live, and when.
- OpusClip can't take a live stream. Note rough timestamps of big moments (from watching, fan comments or other clippers' posts) for later frame sheets.
- When a part ends and YouTube or Twitch has processed the VOD (YouTube: 10 to 30 min for short streams, 2 to 12 h for long ones), start `vodwatch` on it.
  - Use `--captions youtube` when the auto-captions are up and speed matters.
  - Use whisper when quality matters.

## First wave: the first 6 hours after each VOD

1. `vodwatch` → `pick-moments`: 5 to 8 moments per VOD, clean ones first (no hard flags).
2. `opusclip-clip`: one project per window, with moment-specific prompts. Confirm credits.
3. `hooks-titles` → `captions-copy` → `clip-rules` → `schedule-post`.
4. Speed matters more than polish for the first clip of a moment. Post the best clean one first, then variations later.

## Day 2 to 7: the second wave

- Countdown compilations (`countdown` skill): "5 craziest", "5 times NPCs…", streamer vs streamer.
- "Firsts": first police chase, first heist, a name in the game, the first glitch.
- Re-scan the long VODs for the moments the first pass skipped. Fan comments with timestamps are gold: sheet them.
- Run `scoreboard` daily: double down on the streamers, moment types and hook styles that win, and drop slots that don't.

## Default cadence (adjust with the scoreboard)

| Platform | Clips per page per day |
|---|---|
| TikTok | 3 |
| YouTube Shorts | 2 |
| Instagram Reels | 2 |
| X | 4 |

Quality beats volume: an empty slot is better than a weak or risky clip.

## Don't

- Post leaked or pre-release footage, or promote region and VPN tricks for early access.
- Post endings or story spoilers.
- Imply Speed, Kai, Rockstar or OpusClip endorse the page.
- Promise earnings.
- Use bots, engagement pods or bought views.
