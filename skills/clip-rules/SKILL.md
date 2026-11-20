---
name: clip-rules
description: The pass/fail gate every clip goes through before it is posted or submitted to a campaign. It covers credit to the streamer, transformation, no leaks or spoilers, brand safety, campaign brief rules, platform originality and disclosure rules, no implied affiliation, and a provenance log. Use before scheduling or posting, when a campaign rejects a clip, or when the user asks what is allowed.
argument-hint: "[clips.json or clip]"
---

# Clip rules: the gate before anything goes out

Run every clip through this table. One FAIL means it doesn't get posted. Write the result into `gtavi/posts/<clip>.rules.md`.

| # | Rule | How to check |
|---|---|---|
| 1 | **Credit the streamer** on screen and in the caption: handle and platform ("🎥 IShowSpeed (YouTube)", "🎥 twitch.tv/KaiCenat"). Credit is courtesy, not a licence. | look at the frame sheet and the copy |
| 2 | **Transform it**: captions, a hook, 9:16 framing, your own cut, commentary or context. No raw re-uploads; keep excerpts short (about 20 to 60 s). YouTube, TikTok, Instagram and Facebook all demote or demonetize unoriginal reposts. | compare with the source window |
| 3 | **No leaks**: post-release footage only (the first unlock was New Zealand, 11:00 UTC, Nov 18 2026). Nothing datamined or pre-release, and no "how to play early" region or VPN tricks. | provenance timestamp |
| 4 | **No spoilers**: no endings, major story reveals, isolated cutscenes or cutscene compilations; Rockstar's posting policy, as reported, removes these. Show the streamer's reaction over gameplay instead. Label story moments "spoiler" in the caption if you post them at all. | frame sheet |
| 5 | **Brand safe**: no slurs (NPC lines included), no sexual content or nudity, no gore in the first seconds, nothing personal on screen (donations, chat names). Profanity bleeped or trimmed. | scan flags + listen |
| 6 | **Campaign brief followed** (if any): footage source, required name or tag (Reach's Lacy brief asked for the streamer's name and #LACY), no logos or watermarks (Free-plan OpusClip clips are watermarked), audience geography, submission window (Reach asked within 30 minutes of posting), posts kept up as long as required. | the brief, saved in `gtavi/campaigns.md` |
| 7 | **Platform rules**: no bots, engagement pods, bought views or self quote-posts; no geo-spoofing; no mass duplicate uploads. | your posting plan |
| 8 | **No implied affiliation**: never "official", "partnered", or anything that reads as Rockstar, Take-Two or the streamer talking. Page bios say "Fan clips. Not affiliated with Rockstar Games or the streamers shown." | copy + bio |
| 9 | **Disclosure**: `#ad` first plus the platform's paid-partnership label whenever the post is paid, part of a paid campaign, or made with free credits from a brand. | copy |
| 10 | **Provenance logged**: VOD id, in/out, fetch date, credit, and where it was posted (`gtavi/provenance.csv`). Take a clip down promptly when asked. | the CSV |

## Money talk

- Clipping campaigns pay per 1,000 views, after minimums, caps and fees. Most clips earn nothing.
- Don't promise income, and don't repost other people's "I made $X" screenshots.
- Use `campaign_math.py` for honest numbers, labelled illustrative.

## When a campaign rejects a clip

Copy the reason into `gtavi/rules.md` as a new rule, and add a row to the table above if it's a new kind of check.

Not legal advice. For paid promotion that uses streamer or game footage, get a legal check.
