---
name: pick-moments
description: Decide which stream moments become clips, using a scoring rubric (hook, payoff, reaction, readability at 9:16, novelty) and a brand-safety and rights check (slurs, sexual content, spoilers, leaks, profanity). Verifies each candidate on a frame sheet. Use after vodwatch, or whenever the user has candidate timestamps and asks which ones to clip.
argument-hint: "[scan folder or timestamps]"
---

# Pick moments

Inputs: `gtavi/scans/<slug>/moments.md` and `moments.json` from vodwatch, plus `sheets/`. The user's own timestamps or fan comments work too; sheet them with `frame_sheet.py --source <id> --start --end`.

## 1. Look before you score

For each candidate, Read its sheet (12 frames in time order, left to right, top to bottom) and the excerpt in `moments.md`. Write one line saying what actually happens. If you can't tell from the sheet, fetch a closer sheet over a narrower window (`--start/--end` of 20 s).

## 2. Score (0 to 2 each, 10 max)

| Criterion | 2 | 1 | 0 |
|---|---|---|---|
| Hook | something odd or loud in the first 2 s | it gets going within 5 s | slow start |
| Payoff | a clear payoff inside 20 to 45 s | a payoff, but long | no payoff |
| Reaction | the streamer visibly or audibly loses it | mild reaction | none, or the facecam is hidden |
| Readability at 9:16 | readable in the Gameplay layout (facecam 30% on top, gameplay 70% below) | needs a manual crop | UI-heavy or tiny action |
| Novelty | a launch-day first, a glitch, NPC chaos, the streamer's name in the game | familiar but fun | generic driving or menus |

Keep moments scoring 7 or more with no hard flag. Order the keepers by score, then by variety (don't post three car crashes in a row).

## 3. Brand safety and rights (any hard flag = skip)

**Hard skip**
- Slurs, including NPC or in-game dialogue (the scanner flags them; listen to the window to confirm).
- Sexual content or nudity: strip-club scenes, a focus on bikinis, crude in-game phone posts.
- Gore in the first seconds.
- Doxxing or personal info on screen: donation messages, chat with names or addresses.
- Pre-release or leaked material.
- Story spoilers: endings, major reveals, isolated cutscenes or cutscene compilations. Show the streamer's reaction over gameplay instead.
- Sponsors in regulated categories on screen: gambling, crypto, cannabis.

**Fixable**
- Profanity: bleep it with OpusClip's auto censor (Pro and up; `opusclip_api.py censor`) or trim around it.
- Licensed music playing on stream: trim it, or keep it under the voice and expect a claim.
- Drug references: decide case by case and lean toward skipping.

**Rights**
- Footage from after the game's public release only (the first unlock was New Zealand, 11:00 UTC, Nov 18 2026).
- Credit the streamer on every clip.
- Never imply the streamer or Rockstar endorses the page.

## 4. Write `gtavi/scans/<slug>/picks.md`

| # | in | out | what happens | hook idea | score | flags | decision |
|---|---|---|---|---|---|---|---|

Then re-run `plan_jobs.py --ranks <kept ranks>` (see the vodwatch skill) and show the user the plan and its credit estimate before anything is sent.

## Learning loop

Before scoring, read `gtavi/rules.md` if it exists. It holds the patterns the scoreboard and campaign rejections taught us, for example "Speed reaction clips beat driving clips 2x on TikTok" or "a campaign rejected a clip for the logo in the corner". Apply them as tie-breakers.
