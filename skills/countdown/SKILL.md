---
name: countdown
description: Build a 60-67 second "5 craziest GTA 6 stream moments" countdown Short (1080x1920) from real stream moments. OpusClip clips, reframes and captions each moment; Claude picks five, trims each to its payoff, opens a header band on the facecam/gameplay seam, adds the number headers, credits, cold-open title and closing question, and joins them with countdown.py. Use when the user wants a compilation, a countdown, a "top 5 moments" video or "the banger format".
argument-hint: "[streamers or VODs] [angle]"
---

# The "5 craziest moments" countdown

The prompt below is the whole format, word for word as it is shared. Follow `<inputs>`, build to `<direction>`
and `<structure>`, then run it with the steps under "How to build it".

```text
<inputs>
Ask me for, in one message:
1. The streamers and VODs (default: the IShowSpeed and Kai Cenat GTA 6 launch streams) and the angle, picked from: "5 craziest GTA 6 stream moments", "5 times GTA 6 broke <streamer>", "<streamer> vs <streamer>: 5 moments", "5 things GTA 6 NPCs did to streamers".
2. Where it goes (TikTok, Shorts, Reels) and whether it's for a paid campaign. If it is: #ad first, the platform's paid label on, the campaign's tag and footage rules.
3. My OpusClip setup: MCP connected or OPUSCLIP_API_KEY exported (both need Pro (limited API access), Max or Business), or the web app (ClipAnything needs Starter or up and prompting it needs Pro or up; Free adds a watermark). Before any project, tell me the credit estimate (1 credit = 1 minute of video processed) and wait for my yes.
If I give no VODs, run find-streams and pitch me 3 angles, with the real moments behind each.
</inputs>

<direction>
A 1080x1920, 30 fps Short / TikTok / Reel of 60-67 s, built only from real stream moments, every one credited on screen. No dead air: each item runs 10-14 s, cut in on the action and out right after the reaction peaks.
Who does what: OpusClip does the clipping. ClipAnything finds and cuts each moment, it gets reframed to 9:16 in the Gameplay layout (facecam on the top 30%, gameplay below) and OpusClip burns in animated captions with keyword highlights and emoji. On Pro and up, ask ClipAnything for the exact moment ("the gator lunges at him"), genre Gaming, 0-30 s clips through the API (0-1 min in the app); the Virality Score is a tiebreak, not proof. Claude does the countdown: picks the five, orders them, trims each to its payoff, opens the header band, adds the headers and joins them.
Text system, heavy white uppercase with a thick black stroke:
- Header band: slide the gameplay down 250 px to open a frosted band on the facecam/gameplay seam, so the header never covers a face or a caption. In it, a pink-to-orange number badge ("#3") over a short label ("6 STARS, THEN WASTED"), held for the whole item.
- Captions: each clip keeps the captions OpusClip burned in; never stack a second caption layer. A credit line at the bottom of every item: "🎥 @IShowSpeed", "🎥 @KaiCenat".
- In the band, the cold-open title for the first 2.5 s ("5 CRAZIEST GTA 6 STREAM MOMENTS", numbers in yellow, on screen from the first frame) and the closing question over #1's last 1.5 s ("WHICH ONE WAS CRAZIEST? 👇").
Sound: each clip's own audio, loudness-matched to -14 LUFS. No music bed, no voice-over.
Banned: a greeting or channel intro; Rockstar logos or key art, including stream overlays that carry them; spoilers (endings, story reveals, isolated cutscenes); slurs, NPC lines included; sexual content; unbleeped profanity; "official" anything; footage from before the game's release; two items from the same 5 minutes of one stream.
</direction>

<structure>
Length: 60-67 s.
0-2.5 s, cold open: the first seconds of #1 under the title, its most absurd beat.
Then #5 down to #1, each cut in 1-2 s before the action and out 0.5-1 s after the reaction peaks, on a finished word, never mid-word. Order: #5 the scroll-stopper that lands in its first 2 seconds; #4 and #3 a different streamer or situation from the item before; #2 the funniest reaction; #1 the biggest moment, the one people will argue about in the comments. Mix streamers (for example 3 Kai + 2 Speed), never the same streamer twice in a row. End on #1's reaction under the closing question. No outro card, no "follow for more".
Caption: the hook line, every streamer credit and 3-5 hashtags, #ad first if it's paid.
</structure>
```

## How to build it

`S="${CLAUDE_PLUGIN_ROOT}/scripts"`. Work in `gtavi/countdowns/<slug>/`.

1. **Moments.** Run `vodwatch` on each VOD (or take the user's timestamps), then `pick-moments`. Choose 5 plus 2
   spares, all at 7/10 or above, with no hard flags and the variety rules met. #1 must open on its most absurd
   beat, because its first 2.5 s become the cold open.
2. **OpusClip.** For each moment, `python3 -I "$S/plan_jobs.py" --ranks ...` with a moment-specific `--prompt`,
   then the `opusclip-clip` skill (dry run, credit estimate, the user's yes, `batch --yes --wait`, `clips`,
   `download`). Use one brand template with the Gameplay layout for all five. Keep the best returned clip per
   moment.
3. **Trim points.** For each clip, `python3 -I "$S/frame_sheet.py" --video <clip> --cols 6 --rows 4` and read
   OpusClip's transcript (`opusclip_api.py transcript <projectId>`). Set in and out so the item runs 10-14 s
   around the payoff, with the out point after the last word's end time (a word's end in the transcript, plus
   about 0.1 s), never inside a word: a cut mid-word sounds like a glitch. Two short spans of one clip are fine
   when the middle is dead air: join them into one file first (ffmpeg concat), because countdown.py takes one
   span per item.
4. **Open the header band** on every clip, so nothing OpusClip drew gets covered. Gameplay layout (facecam band
   across the top 576 px): cut the frame at the seam, slide the gameplay down 250 px and fill the gap with a
   heavily blurred, darkened copy of the clip's bottom 250 px, the strip the slide pushes out of frame. Never
   fill it from the gameplay's top edge: OpusClip's first caption line sits there, and its blurred ghost would
   show through the band as a second caption layer.

   ```bash
   ffmpeg -i clip.mp4 -filter_complex "[0:v]split=3[a][b][c];[a]crop=1080:576:0:0[cam];\
   [b]crop=1080:250:0:1670,gblur=sigma=36,eq=brightness=-0.25[band];[c]crop=1080:1094:0:576[game];\
   [cam][band][game]vstack=inputs=3[v]" -map "[v]" -map "0:a?" -c:a copy banded.mp4
   ```

   A clip with an inset facecam gets the same cut at y 576, below the inset: check on a frame sheet that the
   line crosses no face and no caption (move it a little if it does, and keep the same band height). The bottom
   250 px of gameplay drop out, so keep the action and the captions above y 1670; check on the sheet that no
   caption ghost shows in the band.
5. **Assemble.** `--header-y 0.383` puts the badge-over-label header (and the cold-open title) inside the band,
   from 585 px down. Keep labels to one line (about 20 characters).

   ```bash
   RENDER_DEPS="${CLAUDE_PLUGIN_DATA}/node" python3 -I "$S/countdown.py" \
     --title "5 CRAZIEST GTA 6 STREAM MOMENTS" --header-y 0.383 --cold-open 2.5 \
     --item "gtavi/clips/A/banded.mp4|<LABEL #5>|@KaiCenat|0:02|0:14" \
     --item "gtavi/clips/B/banded.mp4|<LABEL #4>|@IShowSpeed|0:09.8|0:21.8" \
     --item "...#3" --item "...#2" --item "...#1" \
     --out gtavi/countdowns/<slug>/countdown.mp4
   ```

   Use `--dry-run` first to check the plan. The first time, install puppeteer-core as the `setup` skill
   describes. Then the closing question: render it with `"$S/render.mjs" still` as a 1080x1920 transparent PNG
   whose band backdrop sits at y 576-826 (so it covers the #1 header), and overlay it on the last 1.5 s:
   `ffmpeg -i countdown.mp4 -i question.png -filter_complex "[0:v][1:v]overlay=0:0:enable='gte(t,<length - 1.5>)'" -c:a copy final.mp4`.
6. **Check.**
   - `python3 -I "$S/frame_sheet.py" --video gtavi/countdowns/<slug>/final.mp4 --cols 6 --rows 4` and Read the sheet.
   - Headers sit inside the band and cover no face and no caption; credits are readable; no stream-overlay
     logo shows anywhere; length 60-67 s; nothing banned on screen.
7. **Ship.** `captions-copy`, then `clip-rules`, then `schedule-post`. The JSON next to the MP4 lists the
   credit lines to paste.

Packaging note: the countdown wrapper (band, headers, credits, title, question) is made by Claude with
countdown.py and ffmpeg; the clips inside it come from OpusClip. Describe it that way if asked, and never call a
clip "made with OpusClip" unless OpusClip rendered it.
