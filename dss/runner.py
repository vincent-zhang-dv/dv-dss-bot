"""Locked-down headless Claude for DSS triage: only the ticket and the `dss` MCP server.

`claude -p` (the user's login) runs with none of the user's setup: no settings, CLAUDE.md,
auto-memory, hooks, skills, plugins or other MCP servers, no built-in tools (no Read), and no
session is kept. It gets the triage system prompt (`triage.md`) and the `dss` tools; dontAsk
denies everything else.

Copied from dv-auto-setup-agent `dv_mcp/claude.py` (commit b3f182e), cut down to triage.
"""

import json
import os
import subprocess
from pathlib import Path

from dss.worker import ROOT

MODEL = 'claude-opus-5-5'
EFFORT = 'high'  # low, medium, high, xhigh or max
TIMEOUT_S = 900  # a triage reads Jira, telemetry and code
RUN_DIR = ROOT / 'out' / 'claude_run'  # empty working folder: no CLAUDE.md to discover
SYSTEM = Path(__file__).with_name('triage.md')


def command(model: str = MODEL) -> list[str]:
	"""The `claude -p` command line: the triage prompt and the `dss` tools, nothing else."""
	server = ['run', '-q', '--directory', str(ROOT), 'python', '-m', 'dss', 'mcp']
	mcp = {'mcpServers': {'dss': {'command': 'uv', 'args': server}}}
	line = ['claude', '-p', '--output-format', 'json', '--model', model, '--effort', EFFORT]
	line += ['--setting-sources', '', '--strict-mcp-config', '--mcp-config', json.dumps(mcp)]
	line += ['--tools', '', '--allowedTools', 'mcp__dss__*', '--permission-mode', 'dontAsk']
	line += ['--system-prompt', SYSTEM.read_text(encoding='utf-8'), '--disable-slash-commands']
	line += ['--no-session-persistence']
	return line


def run_reply(prompt: str, model: str = MODEL, timeout: float = TIMEOUT_S) -> dict:
	"""The whole `claude -p` JSON reply to *prompt*: `result`, `total_cost_usd`, `usage`, ...

	The prompt goes on stdin. Raises RuntimeError when claude fails or ends without an answer
	(turn limit, error), and FileNotFoundError when the `claude` CLI is not installed.
	"""
	RUN_DIR.mkdir(parents=True, exist_ok=True)
	done = subprocess.run(
		command(model),
		input=prompt,
		capture_output=True,
		encoding='utf-8',
		cwd=RUN_DIR,
		timeout=timeout,
		check=False,
		env={**os.environ, 'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1'},
	)
	if done.returncode:
		raise RuntimeError(f'claude -p failed ({done.returncode}): {done.stderr or done.stdout}')
	reply = json.loads(done.stdout)
	if reply.get('is_error') or 'result' not in reply:
		raise RuntimeError(f'claude -p ended without an answer: {reply.get("subtype", "error")}')
	return reply
