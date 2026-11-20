---
name: vodwatch
description: Watch an entire stream VOD (hours long) on the user's machine and find the moments worth clipping. Downloads only the audio, transcribes it with whisper.cpp, scores every 30-second window on loudness spikes, reaction words and speech, checks the top picks on frame sheets, and writes an OpusClip job plan with a credit estimate. Use for "scan this VOD", "find the best moments in this stream", or any VOD longer than an hour.
argument-hint: "<VOD url or YouTube id> [number of clips]"
---

# vodwatch: the whole VOD, scanned locally

## 1. Prepare

- From `$ARGUMENTS` take the source (VOD URL, YouTube id or local file) and the number of clips (default 8).
- Choose the folder `gtavi/scans/<streamer>-<id>/`.
- Check the whisper model with `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py"`.
- Tell the user what to expect:
  - Only the audio track is downloaded, and it is stored as 32 kbps mono: about 350 MB for a 24-hour stream.
  - whisper.cpp large-v3-turbo ran at about 29× real time on the Apple Silicon Mac this plugin was built on, so 24 h of audio takes under an hour there. Slower machines can pass `--model` with a smaller model.

## 2. Run

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/vodwatch.py" run "<source>" --clips <N> \
  --out gtavi/scans/<slug> --credit "<Streamer> (<Platform>)"
```

- For a multi-hour VOD, run it in the background. It resumes: finished audio fragments and finished 20-minute transcript chunks are kept.
- The steps are resolve → audio (ytfrag.py for YouTube post-live VODs, yt-dlp or ffmpeg otherwise) → loudness and transcript (`scan_vod.py`) → ranking (`rank_moments.py`) → frame sheets (`frame_sheet.py`) → OpusClip plan (`plan_jobs.py`) → `report.md`.
- Faster path: `--captions youtube` ranks on YouTube's auto-captions when they exist and skips whisper. They have no punctuation, so the ranking is a little weaker.
- If the stream is still live, the script stops. Scan the VOD once it is finished.

## 3. Review, don't trust blindly

1. Read `gtavi/scans/<slug>/moments.md`. Each candidate has in/out times, a score, the reasons ("volume spike +5.4 dB", reaction words, exclamations) and flags (profanity to bleep, slurs, sexual or drug terms, music).
2. Read every `sheets/NN_*.jpg` (12 frames across the moment) and apply the `pick-moments` rubric. Drop menus, loading screens, AFK stretches, cutscenes, spoilers and anything with a hard flag.
3. The scan hears; it can't see. A silent visual moment, such as a backflip on facecam, can be missed. If the user or fan comments name a timestamp, sheet it directly:
   `python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/frame_sheet.py" --source <id> --start h:mm:ss --end h:mm:ss --out gtavi/scans/<slug>/sheets/manual.jpg`
4. Re-plan with only the keepers:

   ```bash
   python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/plan_jobs.py" gtavi/scans/<slug>/moments.json --ranks 1,3,4,7 \
     --source-duration <seconds> --credit "<Streamer> (<Platform>)" --title-prefix "<Streamer>" \
     --out gtavi/scans/<slug>/plan.json --cut-dir gtavi/cuts
   ```

## 4. Hand off

- Show the user the picks (time, what happens, flags) and the plan: number of jobs, route (`url` = OpusClip imports the link with a time range; `upload` = cut locally and upload, for sources over 10 h or Kick), and estimated credits.
- Ask before anything is sent, then continue with the `opusclip-clip` skill.
- Log provenance (VOD id, in/out, scan date, credit) in `gtavi/provenance.csv`.

## Numbers for the launch post

`report.md` lists hours scanned, windows scored, strong moments found, wall time per step and compute cost ($0, all local). Quote them exactly as written there.
