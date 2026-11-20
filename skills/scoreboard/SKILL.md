---
name: scoreboard
description: Track every posted clip (platform, streamer, hook style, length, campaign, views, payouts, approvals and rejections) in a CSV, report what is working, and turn the patterns into rules the other skills follow. Use daily after posting, when the user asks how clips are doing, or before picking the next batch.
argument-hint: "[add | update | report]"
---

# Scoreboard and learning loop

The ledger is `gtavi/scoreboard.csv`, a plain CSV. Views are numbers the user reads off the platforms; never log in, scrape or use their sessions.

## Log

```bash
S="${CLAUDE_PLUGIN_ROOT}/scripts"
python3 -I "$S/scoreboard.py" --file gtavi/scoreboard.csv add --platform tiktok --url <url> \
  --streamer "IShowSpeed" --hook "<hook>" --length 34 --clip <file> --campaign "<campaign or none>"
python3 -I "$S/scoreboard.py" --file gtavi/scoreboard.csv update <url> --views 48210 --likes 3100
python3 -I "$S/scoreboard.py" --file gtavi/scoreboard.csv update <url> --status approved   # or rejected, paid
python3 -I "$S/scoreboard.py" --file gtavi/scoreboard.csv update <url> --paid 4.20 --notes "round 1 payout"
```

Update views at about 24 h, 72 h and 7 days. Record the actual payouts, not estimates.

## Report

```bash
python3 -I "$S/scoreboard.py" --file gtavi/scoreboard.csv report
```

It prints:
- totals;
- per-platform, per-streamer, per-hook-style and per-campaign medians;
- top posts;
- "next" lessons, which are patterns in the user's own numbers once there are 3 or more posts per group.

## Close the loop (weekly, or after every 20 posts)

1. Turn lessons into rules in `gtavi/rules.md`, each with the date and the evidence. For example:
   - "Speed tried-to hooks: 2.3x median views vs statement hooks on TikTok (14 vs 9 posts, Nov 20–27)."
   - "Rejected by <campaign>: watermark in corner → only post Starter+ exports to that campaign."
2. `pick-moments` reads `gtavi/rules.md` to break ties, and `hooks-titles` prefers the winning hook styles.
3. Stop or rework what isn't working: a streamer, platform or time slot with consistently low medians.
4. Keep it honest: a few hits carry most of the views. Don't extrapolate one viral clip into a monthly figure.

## For a weekly recap post

Quote only numbers from the scoreboard, label them as your page's results, and include the credit to the streamers whose moments made them.
