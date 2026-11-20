# GitHub "About" box

**Description** (350 characters max):

> Claude Code plugin that turns GTA 6 streams into ready-to-post 9:16 clips with OpusClip. It scans whole VODs locally (whisper.cpp), picks the moments, and sends them to OpusClip (MCP or API) for clipping, captions and a Virality Score. It also writes hooks and copy, credits the streamer, and schedules posts.

**Website:** `https://www.opus.pro`

**Topics:**

`claude-code` `claude-code-plugin` `agent-skills` `opusclip` `gta6` `clipping` `video-clipping` `twitch` `youtube-shorts` `tiktok` `whisper-cpp` `ffmpeg` `mcp`

**Social preview image:** `docs/img/hero.png` on a dark background (1280x640).

**Pinned description line for the README badge row:** Claude Code plugin · 16 skills · clips 1080x1920 · OpusClip API + MCP · GTA 6 Nov 19 · MIT

**Before publishing:**
- Replace every `[OWNER]` in `README.md`, `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json` and `LICENSE`.
- Decide who `[OWNER]` is and make the disclosure line match. The README's FAQ ("Who made this?") and Disclaimer ("Who made this") say the plugin was made for OpusClip's GTA 6 launch campaign. Sharpen it to the real relationship, for example "Built by [OWNER], part of OpusClip" for OpusClip itself or its staff, or "Made in a paid partnership with OpusClip (#ad)" for a paid or credited partner. Use the same wording, with #ad first where it applies, in every post that promotes the repo.
- Confirm the streamer handles in `skills/find-streams/watchlist.example.json`.
