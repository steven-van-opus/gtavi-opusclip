# EXAMPLE OUTPUT: a full scan of a 23-hour GTA 6 launch stream

> **This is an example.** It is real output from a full run, published to show what vodwatch produces. It is not a benchmark.

| | |
|---|---|
| Source | IShowSpeed's first GTA 6 stream on YouTube, `qSyo5d-dVzA` (23 h 45 min). Credit: **IShowSpeed (YouTube)**. Times and links in these files are in the post-live fragment timeline this run scanned; the processed VOD YouTube serves now plays them 144 s later (pass `--timeline-offset 144` to `frame_sheet.py` or `cut_windows.py`). |
| Scanned | the whole stream: 23.76 h, all 72 whisper chunks of the audio track (33,984 transcript segments) and 85,398 s of loudness |
| Ranked | 8,552 windows of 30 s (every 10 s); 46 strong candidate moments (non-overlapping peaks in the top 1% of windows) |
| Ranking time | about 3 s on a laptop, once the transcript exists. Download and transcription take most of the wall time. This transcript was built over several resumed runs, so there is no single wall time to quote; a fresh `vodwatch.py run` writes its own to `report.md`. |
| Run | Nov 20, 2026, with `rank_moments.py --duration 85542 --top 20 --excerpt-words 8 --eval known_moments.json` |

Files:
- `moments.md` and `moments.json`: the top 20 picks with in/out, score, the reasons, brand-safety flags, the first words said, and a timestamp link.
- `eval.md`: how many moments people had already clipped or timestamped (28, all inside the scanned span, listed in `known_moments.json`) show up in the ranking.
- `review.md`: what a frame-sheet review kept and dropped, and why.

## What the ranking found

**Recall against 28 known moments:** 2 in the top 5, 4 in the top 10, 7 in the top 20, 10 in the top 50 and 16 in the top 100. The top 100 is a review list cut down from 8,552 windows.

These known moments showed up near the top:

| Rank | At | Known moment |
|---|---|---|
| #1 | 4:12:42 | the alligator attack |
| #4 | 1:26:02 | the lead-in to the road-rage fight |
| #8 | 20:21:24 | the "Red Hulk" compound fight |
| #9 | 20:04:14 | freeing the polar bear |
| #13 | 14:12:54 | the first robbery |
| #15 | 13:59:13 | the dirt-bike race |
| #20 | 2:03:07 | "Jason had an apple the whole time" |

Misses: the launch countdown backflip (a silent visual moment on the facecam) and quieter story beats. That's why the skill asks for known timestamps and has a manual frame-sheet step.

## What review is for

The ranking finds candidates, not finished clips. Ten windows were checked on frame sheets (`review.md`): the run's #1 to #9 and #11. Five of the ten were dropped:
- two were cutscenes;
- one had gore;
- one showed nudity the audio flags missed;
- one was a fight in swimwear.

#10 (13:03:26) hasn't been sheeted yet, so it is neither kept nor dropped.

vodwatch shortens 24 hours to a list you can review in minutes. It does not replace the review.

*Stream frames are not committed to this repo. The sheets were made locally and deleted.*
