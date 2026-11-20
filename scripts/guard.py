#!/usr/bin/env python3
"""guard.py: a PreToolUse hook that makes Claude Code ask you before anything spends OpusClip credits or publishes.

The hosted OpusClip MCP server posts, schedules and shares the moment the tool is called, and
creating projects spends credits. This hook turns those calls into a permission prompt that names
what is about to happen. Wired in hooks/hooks.json; you can also run it by hand:

  echo '{"tool_name":"mcp__plugin_gtavi-opusclip_opusclip__opusclip_create_post_task","tool_input":{}}' | python3 -I guard.py

Guarded:
  MCP tools   opusclip_create_post_task, opusclip_schedule_publish, opusclip_unschedule_publish,
              opusclip_share_project (take effect immediately), opusclip_submit_project,
              opusclip_create_censor_job (spend credits), and the media-generation jobs
  Bash        opusclip_api.py create | batch | post | schedule | unschedule | share | censor
              when the command is not a --dry-run
Prints nothing (and lets the normal permission flow decide) for everything else.
"""
import json
import re
import sys

PUBLISH = {'create_post_task': 'publishes the clip NOW on the connected account (X posts cost 3 credits each)',
           'schedule_publish': 'schedules a post that will publish automatically',
           'unschedule_publish': 'cancels a scheduled post',
           'share_project': 'changes who can see the project, immediately'}
SPEND = {'submit_project': 'creates an OpusClip project and spends credits (1 per minute processed, API minimum 10)',
         'create_censor_job': 'starts a censor job on a clip',
         'generat': 'starts a media-generation job that spends credits'}
CLI = re.compile(r'opusclip_api\.py\b[^|;&]*?\b(create|batch|post|schedule|unschedule|share|censor)\b')


def decide(event):
    name = event.get('tool_name') or ''
    inp = event.get('tool_input') or {}
    m = re.match(r'^mcp__.+?__(?:opusclip_)?(\w+)$', name)
    if m and 'opusclip' in name.lower():
        tool = m.group(1)
        for key, why in {**PUBLISH, **SPEND}.items():
            if key in tool:
                detail = json.dumps(inp, ensure_ascii=False)[:400]
                return f'OpusClip: {tool} {why}. Check it before you allow it: {detail}'
    if name == 'Bash':
        cmd = str(inp.get('command') or '')
        hit = CLI.search(cmd)
        if hit and '--dry-run' not in cmd:
            what = hit.group(1)
            why = ('publishes or schedules a post' if what in ('post', 'schedule', 'unschedule', 'share')
                   else 'spends OpusClip credits')
            return f'This opusclip_api.py {what} call {why}: {cmd[:300]}'
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    reason = decide(event)
    if reason:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask',
                                                 'permissionDecisionReason': reason}}))
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)
    sys.exit(main())
