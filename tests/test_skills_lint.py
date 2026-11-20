"""Every skill has valid frontmatter, a matching name, and only references scripts that exist."""
import json
import os
import re
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
SKILLS = os.path.join(ROOT, 'skills')


def read(path):
    with open(path, errors='ignore') as f:
        return f.read()


def frontmatter(path):
    text = read(path)
    m = re.match(r'^---\n(.*?)\n---\n', text, re.S)
    fields = {}
    for line in (m.group(1).splitlines() if m else []):
        if ':' in line and not line.startswith(' '):
            k, v = line.split(':', 1)
            fields[k.strip()] = v.strip().strip('"')
    return fields, text


class TestSkills(unittest.TestCase):
    def test_sixteen_skills_with_frontmatter(self):
        dirs = sorted(d for d in os.listdir(SKILLS) if os.path.isdir(os.path.join(SKILLS, d)))
        self.assertEqual(len(dirs), 16, dirs)
        for d in dirs:
            with self.subTest(skill=d):
                fm, text = frontmatter(os.path.join(SKILLS, d, 'SKILL.md'))
                self.assertEqual(fm.get('name'), d)
                self.assertGreater(len(fm.get('description', '')), 80)
                self.assertLessEqual(len(fm.get('description', '')), 1536)
                self.assertLess(len(text.splitlines()), 500)

    def test_referenced_scripts_exist(self):
        for d in os.listdir(SKILLS):
            p = os.path.join(SKILLS, d, 'SKILL.md')
            if not os.path.exists(p):
                continue
            text = read(p)
            for rel in re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}/([\w./-]+)', text):
                with self.subTest(skill=d, path=rel):
                    self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), f'{d} references missing {rel}')
            for script in re.findall(r'\$S/(\w+\.py)', text):
                self.assertTrue(os.path.exists(os.path.join(ROOT, 'scripts', script)), f'{d} references missing {script}')

    def test_manifests(self):
        plugin = json.loads(read(os.path.join(ROOT, '.claude-plugin', 'plugin.json')))
        market = json.loads(read(os.path.join(ROOT, '.claude-plugin', 'marketplace.json')))
        self.assertEqual(plugin['name'], 'gtavi-opusclip')
        self.assertEqual(market['plugins'][0]['name'], plugin['name'])
        self.assertEqual(market['plugins'][0]['source'], './')
        mcp = json.loads(read(os.path.join(ROOT, '.mcp.json')))
        self.assertEqual(mcp['mcpServers']['opusclip'], {'type': 'http', 'url': 'https://mcp.opus.pro/mcp'})
        hooks = json.loads(read(os.path.join(ROOT, 'hooks', 'hooks.json')))
        self.assertIn('PreToolUse', hooks['hooks'])

    def test_no_secrets_committed(self):
        # the unit tests use obviously fake keys on purpose, so the tests folder is not scanned
        pat = re.compile(r'(sk[-_][A-Za-z0-9]{16,}|opk_[A-Za-z0-9]{16,}|OPUSCLIP_API_KEY\s*=\s*["\']?[A-Za-z0-9_-]{12,})')
        for base, dirs, files in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in ('.git', 'node_modules', '__pycache__', 'tests')]
            for f in files:
                if f.endswith(('.png', '.jpg', '.svg')):
                    continue
                text = read(os.path.join(base, f))
                self.assertIsNone(pat.search(text), os.path.join(base, f))


if __name__ == '__main__':
    unittest.main()
