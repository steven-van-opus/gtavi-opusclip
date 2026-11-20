---
name: find-streams
description: Find live and recent GTA 6 streams and VODs worth clipping (IShowSpeed, Kai Cenat or any streamer) through a YouTube search, plus each watchlisted channel's YouTube streams and Twitch archives (Kick channels come back as links to open), with length, views and the next step for each. Read-only, downloads nothing. Use when the user asks what to clip, who is live, or for a VOD link.
argument-hint: "[streamer or search words]"
---

# Find GTA 6 streams and VODs

## Steps

1. Search, newest first:

   ```bash
   python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/find_streams.py" --query "gta 6 $ARGUMENTS" --max 15 --min-hours 1
   ```

   With no arguments, run it without `--query`; it searches "gta 6 live stream" and "gta 6 stream gameplay".
2. For regular checks, use a watchlist. Copy `${CLAUDE_PLUGIN_ROOT}/skills/find-streams/watchlist.example.json` to `gtavi/watchlist.json` and have the user confirm every handle on the platform itself, then:

   ```bash
   python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/find_streams.py" --watchlist gtavi/watchlist.json --max 5
   ```

   It lists each channel's latest YouTube streams and Twitch archives. Kick channels come back as links for the user to open.
3. Read the `next` line under each result:
   - **LIVE**: still streaming. OpusClip's app can't take a live stream, so note the channel and come back for the VOD. On YouTube a VOD can take 10 to 30 minutes after a short stream ends, and 2 to 12 hours after a long one, before it imports. OpusClip's Live Clipping is a private beta in Opus Labs; don't promise it.
   - **VOD ≤10h**: OpusClip can import the link. On anything longer than about an hour, run the `vodwatch` skill first so you only pay for the windows you want.
   - **VOD >10h**: too long for one OpusClip project. Run `vodwatch`, then `cut-windows`.
4. Show the user a short table with streamer, platform, status, length, views, link and the recommended next step. Rank by:
   1. the streamer's own channel first (credit and quality beat re-uploads);
   2. views;
   3. recency.

## Rules

- Read-only. Never download from this skill, and never sign in to YouTube, Twitch or Kick.
- Never run `yt-dlp -J` on a single long VOD. Its info JSON can be hundreds of MB; the script uses flat listings only.
- Skip anything labelled leaked, pre-release or "early access build".
- A streamer's GTA 6 VOD is their content. Before anything gets posted, the `clip-rules` skill decides how it's credited and transformed.
