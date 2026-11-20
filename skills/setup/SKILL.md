---
name: setup
description: Set up the GTA 6 clip desk. Connects OpusClip (the bundled MCP server signs in with OAuth in the user's browser; scripts read an OPUSCLIP_API_KEY the user exports), checks ffmpeg, yt-dlp, whisper.cpp and its model, and creates the gtavi/ work folder. Use on first run, when a script reports a missing tool, or when OpusClip calls fail with 401 or 403.
---

# Set up the clip desk

Goal: every other skill in this plugin runs without surprises. Work through the steps in order. Sign-ins, keys, plan upgrades and purchases belong to the user: tell them exactly what to do and wait.

## 1. Check the toolchain

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/doctor.py"
```

For every `XX` line, show the user the `fix:` command and ask before you run an install. On macOS that is usually:

- `brew install ffmpeg yt-dlp whisper-cpp`
- the whisper model (about 550 MB, so ask first):
  `mkdir -p ~/.cache/whisper.cpp && curl -L -o ~/.cache/whisper.cpp/ggml-large-v3-turbo-q5_0.bin https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo-q5_0.bin`
- title cards and the countdown only: Node, Chrome and puppeteer-core.
  `npm install --prefix "${CLAUDE_PLUGIN_DATA}/node" puppeteer-core@23`, then run render steps with `RENDER_DEPS="${CLAUDE_PLUGIN_DATA}/node"`.

## 2. Connect OpusClip (either way works)

**Inside Claude Code (MCP).** This plugin ships OpusClip's hosted MCP server, `https://mcp.opus.pro/mcp`. Ask the user to run `/mcp`, choose this plugin's `opusclip` server and authenticate. Their browser opens OpusClip's OAuth page and they sign in themselves. Then call the MCP tools `opusclip_whoami` and `opusclip_get_usage` to confirm. Any account can connect and list the tools; calling them needs a Pro (Beta), Max or Business plan.

**From scripts (REST API).** `opusclip_api.py` reads `OPUSCLIP_API_KEY` from the environment. The user creates the key at https://clip.opus.pro/dashboard → API Access → Create key, and exports it in their own shell profile. Never ask them to paste a key into the chat, and never write one into a file in this repo or the project.

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/opusclip_api.py" usage     # monthly credits used/limit (unofficial endpoint)
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/opusclip_api.py" templates --presets
```

`401` means a missing or wrong key, or a plan without API access. `403 API_MONTHLY_CAP_REACHED` means the monthly API allowance is used up; the client stops instead of retrying.

Optional: OpusClip's official plugin adds its own CLI skill and a copy-video-style skill:
`/plugin marketplace add opus-pro/opus-skills`, then `/plugin install opusclip@opus-skills`.

## 3. Create the work folder

```bash
mkdir -p gtavi/scans gtavi/cuts gtavi/clips gtavi/posts
```

| Path | Holds |
|---|---|
| `gtavi/scans/<streamer>-<vod>/` | vodwatch output: transcript, loudness, `moments.md`, `sheets/`, `plan.json`, `report.md` |
| `gtavi/cuts/` | local windows for upload (sources over 10 h, Kick links, local files) |
| `gtavi/clips/<projectId>/` | OpusClip downloads and `clips.json` |
| `gtavi/posts/` | copy for each clip and `calendar.csv` |
| `gtavi/scoreboard.csv` | every post and its numbers |
| `gtavi/rules.md` | rules learned from the scoreboard and from campaign rejections |
| `gtavi/provenance.csv` | VOD id, in/out, fetch date and credit for every clip |

## 4. Tell the user what it costs

- Scanning runs on their machine, so compute is free.
- OpusClip charges 1 credit per minute of processed video. Every API project costs at least 10 credits. Pro Beta and Max API accounts get 30 requests a minute, 4 projects processing at once and 15 h (900 credits) a month.
- Free-plan clips carry a watermark, and many campaigns reject watermarked clips.
- Nothing goes to OpusClip and nothing is posted without the user's explicit yes. This plugin's hook (`hooks/hooks.json`) also asks before any call that spends credits or publishes.
