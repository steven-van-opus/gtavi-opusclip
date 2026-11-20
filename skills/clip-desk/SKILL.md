---
name: clip-desk
description: Run the whole GTA 6 clipping desk in one go, from a streamer name or a VOD link to approved, credited, captioned 9:16 clips ready to schedule. It chains find-streams, vodwatch, pick-moments, cut-windows, opusclip-clip, hooks-titles, captions-copy, clip-rules and schedule-post, and stops for the user's approval at each checkpoint. Use for "clip Speed's GTA 6 stream", "make clips from this VOD" or "run the desk".
argument-hint: "<VOD url | streamer name> [number of clips]"
---

# Clip desk: stream in, clips out

Run the skills in order and keep a short status line for the user at each step. **Stop at the three checkpoints.** Credits and posts belong to the user.

## 0. Setup check

If `gtavi/` doesn't exist, or `doctor.py` reports a required tool missing, run the `setup` skill first.

## 1. Source

- **`$ARGUMENTS` is a URL or id:** use it.
- **`$ARGUMENTS` is a streamer name:** run `find-streams`, pick their newest finished GTA 6 VOD (the streamer's own channel), and show the user which one you picked.
- **Still live:** wait for the VOD. OpusClip can't take a live stream.

## 2. Scan

Run the `vodwatch` skill:

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/vodwatch.py" run "<source>" --clips <N> --out gtavi/scans/<slug> --credit "<Streamer> (<Platform>)"
```

For long VODs, run it in the background and report progress from `report.md` and `run.json`.

## 3. Pick: checkpoint 1

1. Run `pick-moments`: read `moments.md`, Read every frame sheet, score the candidates and drop the hard flags.
2. Re-plan the keepers with `plan_jobs.py --ranks ...`.
3. Show the user:
   - the picks (in/out, what happens, flags, hook idea);
   - the plan: jobs, the route (url + range, or cut + upload) and the estimated credits.
4. **Wait for a yes.**

## 4. Clip

1. If any job routes `upload`, run `cut-windows` (`cut_windows.py --plan`).
2. Run `opusclip-clip`:
   - dry run first;
   - then `batch --yes --wait`, or over MCP `opusclip_submit_project` per `url` job (`upload` jobs go through `opusclip_create_upload_link` first; see `opusclip-clip`);
   - then `clips`, `download --plan` to `gtavi/clips/<projectId>/` (clip times in the stream), and bleep where needed.
3. Frame-check every downloaded clip (`frame_sheet.py --video`).

## 5. Package: checkpoint 2

1. `hooks-titles`: 3 hooks per clip; choose one.
2. `captions-copy`: `copy_kit.py` per clip, saved to `gtavi/posts/`.
3. `clip-rules`: the pass/fail table per clip.
4. Show the user each clip with its hook, copy, credit and rules result. **Wait for approval, clip by clip.**

## 6. Ship: checkpoint 3

1. `schedule-post`: build the calendar, then schedule with OpusClip (each post confirmed) or hand the user the CSV.
2. Log every post with `scoreboard`. Write provenance rows.

## 7. Wrap up

Report back:
- the clips made, their scheduled times and the credits used;
- the numbers from `report.md` (hours scanned, moments found, wall time);
- the next step: update views in the scoreboard at 24 h, and run `scoreboard report` after 3 days.

## Never

- Sign in or sign up for the user.
- Print or store API keys.
- Spend credits or post without a yes.
- Post without the credit line.
- Promise earnings.
