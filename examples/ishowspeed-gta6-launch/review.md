# EXAMPLE: frame-sheet review of the top picks

These sheets were made with `frame_sheet.py --moments moments.json --source qSyo5d-dVzA --top 10` during an earlier, partial scan of this stream, and reviewed with the `SKILL.md` rubric. The partial scan's top 10 are this full run's #1 to #9 and #11, with the same in and out times. The sheets aren't committed, because they are stream footage.

| # | In–out | What the sheet shows | Decision |
|---|---|---|---|
| 1 | 4:12:42–4:13:52 | Wading through a swamp; an alligator attacks; he shoots and escapes into the grass, facecam reacting | **keep**: bleep 1 word |
| 2 | 17:27:33–17:28:34 | A story cutscene: a long diner conversation with subtitles | drop: isolated cutscene |
| 3 | 8:50:24–8:51:32 | A mission with a gory close-up | drop: gore, story mission |
| 4 | 1:26:02–1:27:04 | Map, then a bike ride and a crash; leads into the road-rage fight at 1:27:28 | **keep**: extend the out point to cover the fight; bleep 2 |
| 5 | 12:18:03–12:19:04 | Nudity on screen (a shower scene) | drop: hard skip (the transcript flags missed it; the sheet caught it) |
| 6 | 17:36:14–17:37:13 | A fight between two women in swimwear | drop: sexualized content risk |
| 7 | 1:23:54–1:24:54 | Tries to steal a truck: "You can't steal this car?" | **keep**: bleep 4 |
| 8 | 20:21:24–20:22:34 | Guarded compound, the "Red Hulk" bursts out and fights the guards | **keep**: bleep 3 |
| 9 | 20:04:14–20:05:24 | Night; freeing a caged polar bear with activists | **keep, with care**: animal-attack violence follows; bleep 5 |
| 10 | 13:03:26–13:04:34 | Not sheeted yet (it only entered the top 10 in the full run) | review it before use |
| 11 | 12:50:24–12:51:24 | In-car mission dialogue with lots of profanity | drop: cutscene-like dialogue, 10 bleeps |

**Kept:** 5 of the 10 reviewed. Next, re-plan with the keepers only:
`python3 -I scripts/plan_jobs.py moments.json --ranks 1,4,7,8,9 --source-duration 85542 --credit "IShowSpeed (YouTube)"`

The VOD is longer than 10 hours, so every job routes `upload`: `cut_windows.py --plan`, then `opusclip_api.py batch --yes`.
