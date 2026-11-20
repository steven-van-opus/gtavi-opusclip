---
name: cut-windows
description: Cut exact time windows out of a stream VOD or a local file, ready to upload to OpusClip. Uses ytfrag.py while a YouTube VOD is still served as 2-second fragments (which yt-dlp can't cut), yt-dlp once YouTube has processed it and for Twitch, Kick and other links, and ffmpeg for local files, then normalizes the frame rate for upload. Use when a source is over OpusClip's 10-hour limit, is a Kick link, or the user wants frame-exact in and out points.
argument-hint: "[plan.json | source start end]"
---

# Cut windows

## When this is needed

OpusClip imports links up to 10 h long and processes only the `range` you send, so a source of 10 h or less usually needs no cut.

Cut locally when:
- the source is longer than 10 h (a 24-hour launch stream, for example);
- it is a Kick link (the API's source list names Twitch but not Kick);
- it is a local file;
- the user wants exact edges.

`plan_jobs.py` already routes these jobs as `upload`.

## Cut every upload job in a plan

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/cut_windows.py" --plan gtavi/scans/<slug>/plan.json
```

It writes each job's `file` and `fps` back into the plan. Then the `opusclip-clip` skill runs `opusclip_api.py batch`, which uploads with the 4-step V2 flow.

Before the first cut it puts the plan on the VOD as YouTube serves it now. A scan of a VOD's 2-second fragments keeps their timeline, which the processed VOD can play later (IShowSpeed's launch VOD: 144 s). So for a plan from such a scan, once YouTube has processed the VOD, `timeline.py` measures the shift once (a minute of the processed audio cross-correlated with the scan folder's `audio.m4a`, kept in `run.json`) and moves every window by it; `scanStart`/`scanEnd` keep the scan's times. A weak match stops and asks for a re-scan; `--timeline-offset SECONDS` sets the shift by hand.

## Cut one window

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/cut_windows.py" --source <youtube id | url | file> \
  --start 4:13:54 --end 4:16:54 --out gtavi/cuts/speed-alligator.mp4 [--height 1080]
```

`--start`/`--end` are times as the VOD plays now. For times from a fragment scan of a VOD YouTube has processed since, add the shift: `--timeline-offset 144` for IShowSpeed's launch VOD (`timeline.py <scan folder>` prints it for any scan).

| Source | Method |
|---|---|
| YouTube VOD still served as 2-second fragments (protocol `http_dash_segments`, usual right after a stream; `yt-dlp --download-sections` fails on these) | `ytfrag.py window`. It fetches only the fragments it needs, rotates throttled signed URLs and retries. |
| processed YouTube VOD (protocol `https`), Twitch, Kick and other links | `yt-dlp --download-sections "*start-end" --force-keyframes-at-cuts`. For YouTube the H.264 + AAC renditions come first (`299+140`, then `137+140`, and so on). |
| local file | `ffmpeg -ss/-to`, re-encoded so the cut is frame-exact |

For YouTube the script asks yt-dlp for the protocol before every cut (`yt-dlp --print protocol`, nothing downloaded). YouTube processes a VOD into whole files some time after the stream (the launch-day VODs took about a day), and from then on it answers every fragment request with the entire file. `ytfrag.py` checks again before its first request and exits with status 3 on a processed VOD, or after a reply over 16 MB; the cut then goes to yt-dlp.

After every cut, the script checks the frame rate. OpusClip local uploads need an integer frame rate, so 59.94 or 29.97 fps is re-encoded to 60 or 30.

## Checks

- `ffprobe -v error -show_entries format=duration:stream=width,height,r_frame_rate -of compact <file>`: the length should match out minus in, at 1080p.
- Make a frame sheet of the cut (`frame_sheet.py --video <file> --out <file>.jpg`) and Read it to confirm the right moment.
- Add a row to `gtavi/provenance.csv`: VOD id, in, out, UTC fetch time, file, credit.
- Disk: 1080p windows are large. Delete the cuts once OpusClip has the uploads.
- Never run `yt-dlp -J` on a 24-hour VOD; its info JSON can be hundreds of MB.
