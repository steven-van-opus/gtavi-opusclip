#!/usr/bin/env python3
"""guard.py: a PreToolUse hook that makes Claude Code ask you before this plugin's opusclip_api.py or an
OpusClip MCP tool spends credits, publishes or changes your OpusClip account.

The hosted OpusClip MCP server posts, schedules and shares the moment the tool is called, and
creating projects spends credits. This hook turns those calls into a permission prompt that names
what is about to happen. Wired in hooks/hooks.json; you can also run it by hand:

  echo '{"tool_name":"mcp__plugin_gtavi-opusclip_opusclip__opusclip_create_post_task","tool_input":{}}' | python3 -I guard.py

Guarded:
  MCP tools   every OpusClip tool except the 15 documented read-only ones (whoami, get_usage,
              list_projects, list_clips, describe_clip, list_collections, list_clips_in_collection,
              list_brand_templates, list_social_accounts, list_scheduled_posts, get_transcript,
              get_social_copy_job, analyze_video, list_pinterest_boards, preview_clips). That covers
              posting, scheduling and sharing (immediate on mcp.opus.pro), submit_project and the
              media-generation jobs (credits), and any tool OpusClip adds later.
  Bash        each opusclip_api.py call whose subcommand is create, batch, post, schedule,
              unschedule, share or censor, unless --dry-run sits before that subcommand. Every call
              in a chained command (&&, ||, ;, |, subshells) is checked on its own, so a dry run in
              front of a real run doesn't hide it, and shell comments are ignored.
Not guarded: other tools that call OpusClip, such as the CLI in OpusClip's official opusclip@opus-skills
plugin. Prints nothing (and lets the normal permission flow decide) for everything else.
"""
import json
import re
import sys

READ_ONLY = {'whoami', 'get_usage', 'list_projects', 'list_clips', 'describe_clip', 'list_collections',
             'list_clips_in_collection', 'list_brand_templates', 'list_social_accounts', 'list_scheduled_posts',
             'get_transcript', 'get_social_copy_job', 'analyze_video', 'list_pinterest_boards', 'preview_clips'}
WHY = {'create_post_task': 'publishes the clip NOW on the connected account (X posts cost 3 credits each)',
       'schedule_publish': 'schedules a post that will publish automatically',
       'unschedule_publish': 'cancels a scheduled post',
       'share_project': 'changes who can see the project, immediately',
       'submit_project': 'creates an OpusClip project and spends credits (1 per minute processed, API minimum 10)',
       'create_censor_job': 'starts a censor job on a clip',
       'generat': 'starts a media-generation job that spends credits'}
OTHER = 'is not one of the documented read-only OpusClip tools, so it can spend credits, publish or change your projects'

SPENDING = {'create': 'creates an OpusClip project and spends credits',
            'batch': 'creates OpusClip projects and spends credits',
            'post': 'publishes a clip now (X posts cost 3 credits each)',
            'schedule': 'schedules a post that will publish automatically',
            'unschedule': 'cancels a scheduled post',
            'share': 'changes who can see a project, immediately',
            'censor': 'starts a censor job on a clip'}
SUBCOMMANDS = set(SPENDING) | {'status', 'wait', 'clips', 'download', 'transcript', 'templates', 'usage', 'upload',
                               'censor-status', 'accounts', 'copy'}      # every opusclip_api.py subcommand
VALUE_OPTS = {'--base-url', '--org-id', '--key-env', '--min-interval'}   # opusclip_api.py options that take a value
WORD = re.compile(r'''[^\s'",\[\];|&()]+|[;|&()]''')                    # shell words (or a Python argv list)


def strip_comments(cmd):
    """Drop shell comments: a # that starts a word outside quotes, up to the end of its line."""
    out, quote, i = [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if quote:
            if ch == '\\' and quote == '"' and i + 1 < len(cmd):
                out.append(cmd[i:i + 2])
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in '\'"':
            quote = ch
        elif ch == '\\' and i + 1 < len(cmd):
            out.append(cmd[i:i + 2])
            i += 2
            continue
        elif ch == '#' and (i == 0 or cmd[i - 1] in ' \t\n;&|()'):
            j = cmd.find('\n', i)
            if j < 0:
                break
            i = j
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def subcommand(words):
    """The opusclip_api.py subcommand in the words after the script name, and whether --dry-run comes first.
    Words that are neither options nor subcommands (an option's value, say) are skipped."""
    dry, i = False, 0
    while i < len(words):
        w = words[i]
        if w in ';|&()':
            return None, dry            # the command ended before a subcommand
        if w in SUBCOMMANDS:
            return w, dry
        if w.startswith('-'):
            dry = dry or w == '--dry-run'
            i += 2 if (w in VALUE_OPTS and '=' not in w) else 1
            continue
        i += 1
    return None, dry


def bash_reason(cmd):
    cmd = strip_comments(str(cmd)).replace('\\\n', ' ')     # comments first: a comment never continues a line
    parts = cmd.split('opusclip_api.py')
    for rest in parts[1:]:                  # every call is judged on its own
        what, dry = subcommand(WORD.findall(rest))
        if what in SPENDING and not dry:
            return f'This opusclip_api.py {what} call {SPENDING[what]}: {cmd.strip()[:300]}'
    return None


def mcp_reason(name, inp):
    tool = name.rsplit('__', 1)[-1]
    base = tool[len('opusclip_'):] if tool.lower().startswith('opusclip_') else tool
    if base in READ_ONLY:
        return None
    why = next((v for k, v in WHY.items() if k in base), OTHER)
    detail = json.dumps(inp, ensure_ascii=False)[:400]
    return f'OpusClip: {tool} {why}. Check it before you allow it: {detail}'


def decide(event):
    name = str(event.get('tool_name') or '')
    inp = event.get('tool_input')
    inp = inp if isinstance(inp, dict) else {}
    if name.startswith('mcp__') and 'opusclip' in name.lower():
        return mcp_reason(name, inp)
    if name == 'Bash':
        return bash_reason(inp.get('command') or '')
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    reason = decide(event) if isinstance(event, dict) else None
    if reason:
        print(json.dumps({'hookSpecificOutput': {'hookEventName': 'PreToolUse', 'permissionDecision': 'ask',
                                                 'permissionDecisionReason': reason}}))
    return 0


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0)
    sys.exit(main())
