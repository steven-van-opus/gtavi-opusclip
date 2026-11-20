# Changelog

## 1.0.0 (2026-11-19)

GTA 6 launch-day release.

- 16 skills: `clip-desk`, `setup`, `find-streams`, `vodwatch`, `pick-moments`, `cut-windows`, `opusclip-clip`, `hooks-titles`, `captions-copy`, `clip-rules`, `campaigns`, `schedule-post`, `scoreboard`, `launch-day`, `countdown` and `clipping-page`.
- Harness:
  - OpusClip's hosted MCP server (`https://mcp.opus.pro/mcp`, OAuth), bundled in `.mcp.json`;
  - `scripts/opusclip_api.py`, a stdlib REST client for the documented API. It covers create from a URL, an uploadId or a file (V2 upload); status, wait, clips, download, transcript and censor; social copy, post, schedule and unschedule. `--dry-run` and `--yes` gates are built in.
- `hooks/hooks.json` with `scripts/guard.py`: Claude Code asks before any OpusClip call that spends credits or publishes.
- Whole-VOD scanning:
  - `vodwatch.py` (resolve, audio, scan, rank, frame sheets, plan, report);
  - `rank_moments.py` (local-baseline loudness, jumps, rarity-weighted reaction cues, speech rate, music and loop penalties, brand-safety flags, recall evaluation);
  - `frame_sheet.py`, `plan_jobs.py` (10 h routing, credit estimate) and `cut_windows.py` (frame-rate normalization).
- Tested tools carried over: `ytfrag.py`, `scan_vod.py`, `scan_frags.py` and `render.mjs`. They gained `--help`, model lookup through `$WHISPER_MODEL` and `~/.cache/whisper.cpp`, and a portable puppeteer-core lookup through `$RENDER_DEPS`.
- Packaging and growth: `countdown.py`, `copy_kit.py`, `post_calendar.py`, `scoreboard.py`, `campaign_math.py`, `find_streams.py` and `doctor.py`.
- README art (original mascot, banner, diagram, badges), with sources in `docs/src/`.
