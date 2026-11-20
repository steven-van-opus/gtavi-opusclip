---
name: opusclip-clip
description: Send picked stream windows to OpusClip and bring back ready-to-post 9:16 clips. Uses ClipAnything, genre Gaming, the Gameplay layout preset (facecam 30% on top, gameplay 70% below), captions and a moment-specific prompt, through the OpusClip MCP tools or the REST script. Polls the projects, lists clips with titles and scores, downloads them and bleeps profanity. Use when the user wants to clip a VOD or a plan with OpusClip.
argument-hint: "[plan.json | VOD url + time range]"
---

# Clip with OpusClip

Credits are the user's money. Show the exact requests and the credit estimate, and get a clear yes before anything is created.

## Settings for GTA streams

| Setting | Value | Why |
|---|---|---|
| `curationPref.model` | `ClipAnything` | Multimodal: it reads visual, audio and sentiment cues, so it works on gameplay with little dialogue, and it takes prompts (Pro and Business). |
| `curationPref.genre` | `Gaming` | A documented genre value. |
| `curationPref.customPrompt` | specific to the moment, e.g. "the alligator attack and his reaction, from the first sighting to the escape" | A specific prompt beats "funny moments". |
| `curationPref.clipDurations` | `[[0,30],[30,60]]` | Always send it; the API has rejected requests without it. |
| `curationPref.range` | the window from `plan.json` | Only the range is processed and billed. |
| `brandTemplateId` | `preset-fancy-Gameplay`, or the user's Brand Kit with the Gameplay layout | The API has no separate gameplay-layout flag. |
| `renderPref.layoutAspectRatio` | `portrait` | 9:16 |
| `importPref.sourceLang` | `en` (or `auto`) | |

## Path A: MCP tools (interactive)

Requires `/mcp` to be authenticated (see the `setup` skill).

1. Jobs routed `url` (sources up to 10 h): `opusclip_submit_project` with the VOD URL and the plan's window, using the parameter names the tool's schema shows (URL, range or start/end, model, prompt, clip durations, brand template). If `plan.json` has `"timeline": {"state": "unchecked"}` (or `"pending"`), run `timeline.py --plan` first, once YouTube has processed the VOD (see `vodwatch`).

   Jobs routed `upload` (over 10 h, Kick, local files), which includes every GTA 6 launch VOD: run `cut_windows.py --plan` first. Then either use Path B, where `opusclip_api.py batch` does the 4-step V2 upload, or call `opusclip_create_upload_link`, upload the cut exactly as that tool's response says, and pass the returned upload id to `opusclip_submit_project`. The help center lists the MCP submit tool's sources as YouTube or Vimeo, so test a Twitch link before relying on it.
2. Check progress with `opusclip_list_projects`, then `opusclip_list_clips` and `opusclip_describe_clip`.
3. `opusclip_preview_clips` to look at them.
4. `opusclip_export_clip` for the HD file. List and describe return preview URLs only.
5. Fixes go through `opusclip_edit_clip` (it supports `dryRun`; operations include `set_captions`, `trim_section`, `remove_pauses` and `set_style`, and undo works for 72 h). For bleeps use `opusclip_create_censor_job`.

The hook asks before `opusclip_submit_project`, because it spends credits, and before every other OpusClip tool that isn't read-only (`opusclip_create_upload_link` included). Still ask the user yourself first, with the credit estimate.

## Path B: REST script (automation, batches)

Needs `OPUSCLIP_API_KEY` in the environment.

```bash
S="${CLAUDE_PLUGIN_ROOT}/scripts"
python3 -I "$S/cut_windows.py" --plan gtavi/scans/<slug>/plan.json            # first, if any job routes "upload"
python3 -I "$S/opusclip_api.py" --dry-run batch gtavi/scans/<slug>/plan.json   # exact requests, sends nothing
python3 -I "$S/opusclip_api.py" batch gtavi/scans/<slug>/plan.json --yes --wait # after the user says yes
python3 -I "$S/opusclip_api.py" clips <projectId> --top 10 --plan gtavi/scans/<slug>/plan.json    # title, score*, length, VOD in/out
python3 -I "$S/opusclip_api.py" download <projectId> --top 5 --out gtavi/clips/<projectId> --plan gtavi/scans/<slug>/plan.json
python3 -I "$S/opusclip_api.py" censor <projectId> <clipId> --yes              # bleep profanity
```

For a single window with no plan:

```bash
python3 -I "$S/opusclip_api.py" create --url "<VOD url>" --start 4:11:30 --end 4:21:30 \
  --prompt "<moment-specific prompt>" --title "<Streamer> GTA 6 4:11:30" --yes --wait
```

Behaviour of `batch`:
- No more than 4 projects process at once.
- It backs off on `429`, stops on `403 API_MONTHLY_CAP_REACHED` (tell the user the `reset_at` date), and saves project ids into the plan so a re-run resumes.
- `upload` jobs need `cut_windows.py --plan` first.
- `url` jobs from a scan of a VOD's 2-second fragments are held back until `python3 -I "$S/timeline.py" --plan <plan.json>` has moved them onto the processed VOD's timeline (vodwatch's `report.md` says when).

## Check every clip before it moves on

1. `frame_sheet.py --video gtavi/clips/<projectId>/01_*.mp4 --out /tmp/c1.jpg`, then Read it:
   - Is the facecam in the top band and the action readable?
   - If the automatic layout cut off the facecam, a kill feed or the minimap, fix the framing in the OpusClip editor. Facecams in these streams are often a small box at the edge of the 16:9 frame.
2. Are the captions correct? Do names spell right? Add a brand vocabulary in OpusClip (Pro and up) if they keep failing.
3. Is the score high and does the clip deliver? The `score` field is what OpusClip's own CLI reads; the app calls it the Virality Score (0 to 99). It is a prediction, not a promise.
4. Write the credit line into `clips.json` (for example `"credit": "IShowSpeed (YouTube)"`). VOD time = job start in plan.json + clip time; log that in `gtavi/provenance.csv`. OpusClip's clip times for an `upload` job are inside the uploaded cut, not the stream; `download --plan` writes `vodStart`/`vodEnd` into `clips.json` for you. Then hand off to `hooks-titles`, `captions-copy` and `clip-rules`.

## Never

- Post or schedule from this skill. That is `schedule-post`, behind `clip-rules`.
- Share a project publicly without asking.
- Caption a clip "made with OpusClip" unless OpusClip actually produced it.
