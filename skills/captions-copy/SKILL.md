---
name: captions-copy
description: Burned-in captions and per-platform post copy (TikTok, YouTube Shorts, Instagram Reels, X) for each clip, with the streamer credit, the #ad disclosure when a post is paid, campaign tags and length checks. Use after hooks are chosen, or when the user asks for captions, descriptions, hashtags or post text.
argument-hint: "[clips.json or one clip]"
---

# Captions and copy

## On-screen captions (OpusClip)

- Captions come from OpusClip's animated caption templates: editable text, font, colour, stroke and shadow, auto emoji and keyword highlights, 20+ languages. Keep them on for every clip; most people watch with the sound off.
- Rotate 3 or 4 caption looks across a page so it doesn't look copy-pasted. Use different templates or Brand Kits, different highlight colours and emoji on or off.
- Spell names right. If "IShowSpeed", "Kai Cenat" or character names come out wrong, fix them in the editor (`opusclip_edit_clip`, `replace_phrase`) or add a brand vocabulary (Pro and up).
- Bleep profanity with `opusclip_api.py censor` or `opusclip_create_censor_job`.

## Post copy, per platform

Run this for every clip and fix every warning it prints:

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/copy_kit.py" --hook "<hook from hooks-titles>" \
  --streamer "IShowSpeed" --source-platform YouTube --tags gta6,ishowspeed,gta6clips \
  [--campaign-tag "#TAG"] [--paid] [--extra "<one line of your own commentary>"] > gtavi/posts/<clip>.txt
```

What it builds:

| Platform | Copy | Limit it checks |
|---|---|---|
| TikTok | hook + credit + 3 to 5 hashtags | 2,200 characters (conservative) |
| YouTube Shorts | title (the hook, 100 characters or fewer) + description with credit and 2 or 3 hashtags | |
| Instagram Reels | hook + credit + 3 to 5 hashtags | |
| X | hook + credit, no hashtags by default | 280 characters (non-Premium) |

What it enforces:
- A credit line on every post: "🎥 IShowSpeed (YouTube)", or `--credit "twitch.tv/KaiCenat"`.
- `#ad` first when the post is paid or part of a paid campaign. Also turn on the platform's own label: X Paid Partnership, TikTok content disclosure, Instagram Paid partnership, the YouTube paid-promotion box.
- The tag the campaign brief requires, when you pass one.
- Warnings on affiliation or money wording ("official", "partnered", "guaranteed", "get rich", "autopilot", "while you sleep") and on leak or spoiler words.

## OpusClip's AI copy (optional)

OpusClip can draft a title, description and hashtags for one clip on one connected account:

```bash
python3 -I "${CLAUDE_PLUGIN_ROOT}/scripts/opusclip_api.py" copy <projectId> <clipId> --account <postAccountId>
```

Or use the MCP tools `opusclip_create_social_copy_job` and `opusclip_get_social_copy_job`. Treat the draft as a starting point: put it through `copy_kit.py` so the credit line and disclosure are right.

## Style

- First line: the hook. Second: one line of context or your own take. Then the credit, then the tags.
- Sentence case and short. No "link in bio" spam, and no engagement bait the clip doesn't deliver.
- Don't repeat the same caption on the same page within a week.
