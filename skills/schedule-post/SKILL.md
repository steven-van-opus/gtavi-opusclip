---
name: schedule-post
description: Schedule or post approved clips with OpusClip's social scheduler (REST API or MCP, each post only after the user's explicit yes), or build a posting calendar CSV for posting by hand. Spreads posts over TikTok, YouTube Shorts, Instagram Reels and X within OpusClip's per-platform limits. Use when clips have passed clip-rules and the user wants them out.
argument-hint: "[clips.json] [start date]"
---

# Schedule and post

Only clips that passed `clip-rules` get here. Posting is public and can't be undone cleanly, so confirm every post (or one explicit batch the user approved line by line) before it is sent.

## 1. Plan the calendar

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/post_calendar.py" --clips gtavi/clips/<projectId>/clips.json \
  --start <YYYY-MM-DD> --days 7 --tz <audience tz, e.g. America/New_York> \
  --per-day tiktok=3,shorts=2,reels=2,x=4 --window 09:00-23:00 --credit "<Streamer> (<Platform>)" \
  --out gtavi/posts/calendar.csv
```

- Best clips go first. Each clip goes once to each platform, staggered across the window.
- The `utc` column feeds the scheduler directly.
- Quality beats volume: a slot stays empty rather than getting a weak clip.

## 2a. OpusClip scheduler (Pro), via REST

Social posting through the API runs in this order:

```bash
S="${CLAUDE_PLUGIN_ROOT}/scripts"
python3 -I "$S/opusclip_api.py" accounts                                        # postAccountId / subAccountId
python3 -I "$S/opusclip_api.py" copy <projectId> <clipId> --account <postAccountId>  # optional AI copy draft
python3 -I "$S/opusclip_api.py" --dry-run schedule <projectId> <clipId> --account <id> \
  --at 2026-11-20T17:00:00-05:00 --title "<hook>" --description "<copy_kit output>"  # show the user
python3 -I "$S/opusclip_api.py" schedule ... --yes                                   # after a yes
python3 -I "$S/opusclip_api.py" unschedule <scheduleId> --yes                        # cancel
```

- `post` publishes immediately; prefer `schedule`.
- Posting to X costs 3 credits per clip per account.
- Limits in OpusClip's scheduler: X 150 a day (plus a cap shared by all OpusClip users), TikTok 4 a minute and 15 a day, Instagram 40 a day, YouTube about 15 a day (soft). A workspace can make at most 500 posts in any rolling 24 h.

## 2b. OpusClip MCP

`opusclip_schedule_publish`, `opusclip_create_post_task`, `opusclip_unschedule_publish` and `opusclip_share_project` take effect immediately on mcp.opus.pro. This plugin's hook asks before each call, but ask the user yourself first, with the clip, account, time and copy. (The claude.ai connector version sends an approval link instead.)

## 2c. By hand

Give the user `calendar.csv` and the copy files from `gtavi/posts/`. They post in the apps.

## 3. After each post

- Campaign clips: submit within the brief's window (some require it within 30 minutes of posting). Submission is the user's action in the campaign's site.
- Log the post right away:
  `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/scoreboard.py" --file gtavi/scoreboard.csv add --platform tiktok --url <post url> --streamer "<Streamer>" --hook "<hook>" --length <s> --clip <file>`
- Add the post URL to `gtavi/provenance.csv`.
