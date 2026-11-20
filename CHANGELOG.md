# Changelog

## 1.1.0 (2026-11-20)

YouTube now serves the GTA 6 launch VODs as processed files (yt-dlp protocol `https` instead of `http_dash_segments`), and a processed VOD answers every 2-second "fragment" request with the whole file. Downloads are now routed by how YouTube serves each VOD.

- `vodwatch.py` asks yt-dlp for the protocol (`--print protocol`, read-only) before every audio download:
  - still fragments: `ytfrag.py track`, as before;
  - processed: yt-dlp `-f 139/ba/w`, then the mono 16 kHz extract. If `ytfrag.py` finds the VOD processed after all, vodwatch asks again and carries on with yt-dlp;
  - Twitch, Kick and other links: `-f ba/w`, never full-quality video.

  `run.json` (`audio`) and `report.md` (`- Audio:`) say which way the audio came. A run that finds fragments left from before the VOD was processed says they can't be resumed.
- `ytfrag.py` asks for the protocol before its first request and stops on a processed VOD. A reply over 16 MB (no 2-second fragment is that big) stops the download after one round. Both exit with status 3. `window` now writes `<out>.part` and renames it only when ffmpeg succeeds, like `track`, and removes `<out>.parts/` when the VOD turns out to be processed (also when the video track had come through before the audio came back as a whole file).
- `cut_windows.py` asks before every cut, and so does `frame_sheet.py`, which cuts through it. Fragments go to `ytfrag.py window`; processed VODs to `yt-dlp --download-sections` with the H.264 + AAC renditions first (`299+140` and down the ladder; `134/133` for frame sheets). On ytfrag's exit 3 the cut goes straight to yt-dlp instead of trying every format.
- Timeline shift. A scan of a VOD's 2-second fragments keeps their timeline, and the processed VOD can play it later: IShowSpeed's launch VOD `qSyo5d-dVzA` plays everything 144 s later (measured by audio cross-correlation), so 1.1.0's sheets and cuts of a fragment scan would have landed 2 min 24 s early.
  - `timeline.py` (new) finds the shift for a scan folder: 0 for scans whose audio came through yt-dlp, ffmpeg, Twitch or Kick; for a fragment scan (or a 1.0.0 run.json) of a VOD YouTube has processed since, a minute of format 139 at two moments 10+ minutes apart, cross-correlated with the scan's `audio.m4a`. Both must match clearly and agree within 1 s; the result is kept in `run.json` as `timeline_offset`. A weak or disagreeing match, or a fragment scan without its `audio.m4a`, stops and asks for a re-scan or `--timeline-offset`.
  - `vodwatch.py run`, `frame_sheet.py --moments` and `cut_windows.py --plan` add the shift before they fetch anything; `--timeline-offset SECONDS` sets it by hand on `frame_sheet.py` and `cut_windows.py` (also for single windows) and on `plan_jobs.py`. Sheet tiles keep the scan's times; `sheet.json` records the shift.
  - `plan_jobs.py` moves the plan onto a shift kept in `run.json` (`scanStart`/`scanEnd` keep the scan's times) and records `plan["timeline"]`. `opusclip_api.py batch` holds back `url` jobs from a fragment scan nobody has checked until `timeline.py --plan` has run; dry runs say so.
  - When `vodwatch.py` switches a folder to yt-dlp, `transcript.jsonl` and `loudness.tsv` rows from the fragment timeline are set aside (`*.fragment-timeline.*`) instead of being resumed on the processed audio. `report.md` gets a `- Timeline:` line.
- `scan_frags.py` drops a chunk whose transcription finishes after the track was assembled, so no 20-minute span is written to `transcript.jsonl` twice.
- Docs: the `vodwatch`, `cut-windows` and `opusclip-clip` skills, and the README's How it works and Scripts sections (edited in place), cover both ways YouTube serves a VOD and the timeline shift.
- Tests: `tests/test_download_routing.py` and `tests/test_timeline.py`, offline: a fake `yt-dlp` and `whisper-cli` on PATH, mocked HTTP, a local fragment server, and a fake processed VOD that plays the scan's audio 37 s later.

## 1.0.0 (2026-11-19)

GTA 6 launch-day release.

- 16 skills: `clip-desk`, `setup`, `find-streams`, `vodwatch`, `pick-moments`, `cut-windows`, `opusclip-clip`, `hooks-titles`, `captions-copy`, `clip-rules`, `campaigns`, `schedule-post`, `scoreboard`, `launch-day`, `countdown` and `clipping-page`.
- Harness:
  - OpusClip's hosted MCP server (`https://mcp.opus.pro/mcp`, OAuth), bundled in `.mcp.json`;
  - `scripts/opusclip_api.py`, a stdlib REST client for the documented API. It covers create from a URL, an uploadId or a file (V2 upload); status, wait, clips, download, transcript and censor; social copy, post, schedule and unschedule. `--dry-run` and `--yes` gates are built in.
- `hooks/hooks.json` with `scripts/guard.py`: Claude Code asks before this plugin's `opusclip_api.py` or an OpusClip MCP tool spends credits or publishes. MCP: every OpusClip tool except the 15 documented read-only ones. Bash: each `opusclip_api.py` call in a command is checked on its own, so a chained dry run or a `# --dry-run` comment doesn't hide a real run. Other tools, such as the official plugin's CLI, aren't covered.
- Whole-VOD scanning:
  - `vodwatch.py` (resolve, audio, scan, rank, frame sheets, plan, report);
  - `rank_moments.py` (local-baseline loudness, jumps, rarity-weighted reaction cues, speech rate, music and loop penalties, brand-safety flags, recall evaluation);
  - `frame_sheet.py`, `plan_jobs.py` (10 h routing, credit estimate) and `cut_windows.py` (frame-rate normalization).
- Tested tools carried over: `ytfrag.py`, `scan_vod.py`, `scan_frags.py` and `render.mjs`. They gained `--help`, model lookup through `$WHISPER_MODEL` and `~/.cache/whisper.cpp`, and a portable puppeteer-core lookup through `$RENDER_DEPS`.
- Safe resume: a transcript chunk counts as done only with its `chunk_done` marker (lines of a chunk cut off mid-write are removed and the chunk runs again), and `ytfrag.py track` and `vodwatch.py` write the audio to `<file>.part` and rename it only when ffmpeg succeeds, deleting fragments after that.
- `opusclip_api.py batch` treats a project with an error as finished and stops waiting for a free slot after `--timeout`; `clips` and `download` take `--plan plan.json` to report clip times in the stream (`vodStart`/`vodEnd`) for provenance.
- Packaging and growth: `countdown.py`, `copy_kit.py`, `post_calendar.py`, `scoreboard.py`, `campaign_math.py`, `find_streams.py` and `doctor.py`.
- README art (original mascot, banner, diagram, badges), with sources in `docs/src/`.
