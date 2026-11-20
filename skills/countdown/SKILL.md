---
name: countdown
description: Build a ~60-second "5 craziest GTA 6 stream moments" countdown Short (1080x1920) automatically. OpusClip clips, reframes and captions each moment; Claude picks five, trims each to its payoff, adds number headers, credit lines and a cold-open title, and joins them with countdown.py. Use when the user wants a compilation, a countdown or a "top 5 moments" video.
argument-hint: "[streamers or VODs] [angle]"
---

# The "5 craziest moments" countdown

This is the full prompt. Follow `<inputs>`, then build to `<direction>` and `<structure>`, using the steps at the end.

<inputs>
Ask me for all of this in one message:
1. The streamers and VODs. Default: the IShowSpeed and Kai Cenat GTA 6 launch streams. If I give none, run the find-streams skill and pitch me 3 angles, with the actual moments behind each.
2. The angle. Pick one:
   - "5 craziest GTA 6 stream moments"
   - "5 times GTA 6 broke <streamer>"
   - "<streamer> vs <streamer>: 5 moments"
   - "5 things GTA 6 NPCs did to streamers"
3. Where it goes (TikTok, Shorts, Reels), and whether it's for a paid campaign. If it is: #ad goes first, switch on the platform's paid label, add the campaign tag, and follow the campaign's footage rules.
4. My OpusClip setup: MCP connected, or OPUSCLIP_API_KEY exported, and my plan. Prompting needs Pro; the API needs Pro (Beta), Max or Business. Before creating any project, tell me the credit estimate and wait for my yes.
</inputs>

<direction>
A 1080x1920, 30 fps Short/TikTok/Reel of 55 to 65 s, built only from real stream moments, each credited on screen.

**Who does what**
- OpusClip does the heavy lifting. ClipAnything finds and cuts each moment, reframes it to 9:16 in the Gameplay layout (facecam 30% on top, gameplay 70% below), and burns in animated captions with keyword highlights.
- Claude assembles. It picks the five, orders them, trims each to its payoff, adds the countdown headers and credit lines, and joins them with countdown.py.

**Pace.** No dead air.
- Each item runs 9 to 12 s: cut in on the action, cut out right after the reaction peaks.
- Nothing is held for more than 3 s without movement or a caption change.

**Text system**
- Item header (countdown.py): a pink-to-orange number badge ("#3") over the label in heavy white uppercase with a black stroke ("SPEED VS THE ALLIGATOR"). It sits on the seam between facecam and gameplay (about 27% down) for the whole item.
- Captions: OpusClip's animated captions in the lower half, one caption look for the whole video. Rotate looks between videos, never within one.
- Credit line at the bottom of every item: "🎥 IShowSpeed (YouTube)", "🎥 twitch.tv/KaiCenat".
- Cold-open title for the first 2.5 s, over #1's best second: "5 CRAZIEST GTA 6 STREAM MOMENTS", with the numbers in yellow.

**Banned**
- a greeting or channel-name intro;
- Rockstar logos or key art;
- spoilers: endings, story reveals, isolated cutscenes;
- slurs, NPC lines included; sexual content; unbleeped profanity;
- "official" anything;
- any footage from before the game's public release;
- two items from the same 5 minutes of one stream.
</direction>

<structure>
**Length:** 55 to 65 s.

**0 to 2.5 s, cold open:** #1's single most absurd second, under the title.

**Then #5 down to #1, about 10 to 12 s each:**

| Slot | What goes there |
|---|---|
| #5 | the scroll-stopper: it has to land in its first 2 seconds |
| #4, #3 | variety: a different streamer or situation from the item before |
| #2 | the funniest reaction |
| #1 | the biggest moment, the one people will argue about in the comments |

**Order rules**
- Mix streamers (for example 3 Speed + 2 Kai). Never put two items from the same streamer next to each other if it can be avoided.
- Each item's in point is 1 to 2 s before the action starts; its out point is 0.5 to 1 s after the reaction peaks.
- End on #1's reaction: no outro card, no "follow for more".

**Caption:** the hook line, every streamer credit, and 3 to 5 hashtags (from copy_kit.py). #ad goes first if the post is paid.
</structure>

## How to build it

1. **Moments.** Run `vodwatch` on each VOD (or take the user's timestamps), then `pick-moments`. Choose 5 plus 2 spares, all at 7/10 or above, with no hard flags and the variety rules met.
2. **OpusClip.** For each moment, use `plan_jobs.py --ranks ...` with a moment-specific `--prompt`, then the `opusclip-clip` skill (dry run, credit estimate, the user's yes, `batch --yes --wait`, `clips`, `download`). Keep the best returned clip for each moment.
3. **Trim points.** For each clip, run `frame_sheet.py --video <clip> --cols 6 --rows 4` and read OpusClip's transcript (`opusclip_api.py transcript <projectId>`). Then set in and out so the item runs 9 to 12 s around the payoff.
4. **Assemble.**

   ```bash
   RENDER_DEPS="${CLAUDE_PLUGIN_DATA}/node" python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/countdown.py" \
     --title "5 CRAZIEST GTA 6 STREAM MOMENTS" \
     --item "gtavi/clips/A/01.mp4|<LABEL #5>|IShowSpeed (YouTube)|0:01.5|0:12" \
     --item "gtavi/clips/B/02.mp4|<LABEL #4>|twitch.tv/KaiCenat|0:03|0:14" \
     --item "...#3" --item "...#2" --item "...#1" \
     --out gtavi/posts/countdown-<date>.mp4
   ```

   Use `--dry-run` first to check the plan. The first time, install puppeteer-core as the `setup` skill describes.
5. **Check.**
   - Run `frame_sheet.py --video gtavi/posts/countdown-<date>.mp4 --cols 6 --rows 4` and Read the sheet.
   - Headers must not cover the facecam or the captions, and credits must be readable.
   - Length 55 to 65 s; nothing banned on screen.
6. **Ship.** `captions-copy`, then `clip-rules`, then `schedule-post`. The JSON next to the MP4 lists the credit lines to paste.

Packaging note: the countdown wrapper is made by countdown.py, and the clips inside it come from OpusClip. Describe it that way if asked.
