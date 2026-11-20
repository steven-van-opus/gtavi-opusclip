---
name: hooks-titles
description: Write the first-two-seconds hook and the on-screen title for each clip (3 options per clip, no clickbait, no spoilers), and decide between OpusClip's Auto headline, Viral Hook or a rendered title card. Use after clips come back from OpusClip, or when the user asks for hooks, titles or thumbnail text.
argument-hint: "[clips.json or clip description]"
---

# Hooks and on-screen titles

## Write 3 options per clip

Rules:
- Write from what actually happens in the clip: read its frame sheet and transcript first.
- 7 words or fewer on screen. Name the streamer and the thing.
- No spoilers in the hook or thumbnail (story moments in launch week especially).
- No "official", and nothing that reads as Rockstar or the streamer talking.
- One emoji at most.

| Pattern | Example shape |
|---|---|
| tried-to | "<Streamer> tried to <verb> the <thing>… it did not go well" |
| vs | "<Streamer> vs a <thing>" |
| the game knew | "GTA 6 put <Streamer>'s <thing> in the game" |
| POV | "POV: you <situation> on day one" |
| wait for it | "Wait for the <thing>" (only when the payoff comes within 8 s) |
| first time | "<Streamer>'s first <event> in GTA 6" |
| question | "Why did the <NPC> do that?" |

Pick one for the clip and keep the other two as A/B options for reposts. `scoreboard.py` tags each hook's style automatically, so the learning loop can compare them.

## Put it on the clip

| Option | What it does | Cost | Availability |
|---|---|---|---|
| OpusClip Auto headline (formerly Auto hook) | One-line AI hook over the first seconds of the top 10 clips. | | Off by default for API projects; turn it on with `opusclip_api.py create --auto-hook`. |
| OpusClip Viral Hook | Template opening: animated text, zoom and sound effects. | 20 credits per clipping run, or 2 per hook in the editor. | Pro and up. Say the cost before using it. |
| Your own title card | Render with `render.mjs` (`RENDER_DEPS="${CLAUDE_PLUGIN_DATA}/node" node "${CLAUDE_PLUGIN_ROOT}/scripts/render.mjs" still --html card.html --out card.png --width 1080 --height 1920 --transparent`; the `setup` skill installs puppeteer-core there), then overlay with ffmpeg (`overlay=0:0`, first 2 s). | | Keep it inside the top band so it doesn't cover the facecam. |
| Edit in OpusClip | `opusclip_edit_clip` text-overlay operations (MCP), or the OpusClip editor. | | |

## Thumbnails and covers

A frame where the streamer's face shows the reaction, plus 2 to 4 words of the hook. Don't use Rockstar key art or logos.

## Output

Add `hook`, `hook_alts` and `title_card` to each entry in `clips.json`. `captions-copy` uses the hook as the caption's first line.
