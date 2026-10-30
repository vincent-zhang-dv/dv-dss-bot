"""One DSS ticket triage: the prompt, the locked-down Claude run, and what it cost.

The Slack bot runs one for each new ticket; ``python -m dss triage <key>`` runs one by hand.
"""

import csv
import os
import subprocess
import time
from datetime import datetime

from dss import runner
from dss.worker import ROOT

TIME_FORMAT = '%Y-%m-%d_%H-%M-%S'  # local time, as Cerberus names its telemetry logs
MAX_POST = 8000
PROMPT = """New ticket {key}, posted {posted} (local time).
Format with plain Markdown (paragraphs, lists, `code`); no tables wider than 4 columns.{post}"""
USAGE_KEYS = (
	'input_tokens',
	'output_tokens',
	'cache_creation_input_tokens',
	'cache_read_input_tokens',
)
USAGE_FILE = ROOT / 'out' / 'usage.csv'  # one row per triage
USAGE_COLUMNS = ('run_at', 'key', 'posted', 'turns', 'seconds', *USAGE_KEYS, 'cost_usd')
TRACE_DIR = ROOT / 'out' / 'trace'  # one file of tool calls per triage, with DSS_TRACE=1


def triage(key: str, posted: str, post: str = '', ask=runner.run_reply) -> dict:
	"""Claude's analysis of ticket *key*: `text`, `cost_usd`, `tokens`, `turns` and `seconds`.

	*posted* is the ticket time in TIME_FORMAT; the triage looks for the reporter's Cerberus
	session before it. *post* is the support-request workflow's message, when there is one.
	*ask* is :func:`dss.runner.run_reply` (faked in tests).
	"""
	prompt = PROMPT.format(key=key, posted=posted, post=workflow_post(post))
	return run(key, posted, prompt, ask)


def workflow_post(post: str) -> str:
	"""The support-request workflow's *post* as the end of a prompt; empty when there is none."""
	if not post:
		return ''
	return f'\n\nThe support-request workflow posted:\n{post[:MAX_POST]}'


def run(key: str, posted: str, prompt: str, ask=runner.run_reply) -> dict:
	"""Claude's answer to *prompt* about ticket *key*, with what it cost (see :func:`triage`).

	Each run adds a row to USAGE_FILE. With DSS_TRACE=1, its tool calls also go to
	TRACE_DIR/<key>_<run time>.jsonl.
	"""
	run_at = datetime.now().strftime(TIME_FORMAT)
	trace = None
	if os.environ.get('DSS_TRACE') == '1':
		TRACE_DIR.mkdir(parents=True, exist_ok=True)
		trace = TRACE_DIR / f'{key}_{run_at}.jsonl'
	started = time.monotonic()
	reply = ask(prompt, trace=trace)
	seconds = round(time.monotonic() - started)
	turns = int(reply.get('num_turns') or 0)
	answer = {'text': reply['result'].strip(), **spend(reply), 'turns': turns, 'seconds': seconds}
	row = {'run_at': run_at, 'key': key, 'posted': posted, 'turns': turns, 'seconds': seconds}
	usage = reply.get('usage') or {}
	for name in USAGE_KEYS:
		row[name] = int(usage.get(name) or 0)
	row['cost_usd'] = answer['cost_usd']
	log_usage(row)
	return answer


def log_usage(row: dict) -> None:
	"""Append one triage's *row* (USAGE_COLUMNS) to USAGE_FILE, with a header if it is new."""
	USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
	new = not USAGE_FILE.exists()
	with USAGE_FILE.open('a', newline='', encoding='utf-8') as file:
		writer = csv.DictWriter(file, USAGE_COLUMNS)
		if new:
			writer.writeheader()
		writer.writerow(row)


def spend(reply: dict) -> dict:
	"""Cost (USD at API prices) and tokens of one `claude -p` reply.

	On a claude.ai seat nothing is billed per answer; the cost still shows how much of the seat's
	usage it took, and it is the real bill once the bot runs on an API key or Bedrock.
	"""
	usage = reply.get('usage') or {}
	tokens = 0
	for key in USAGE_KEYS:
		tokens += int(usage.get(key) or 0)
	cost = float(reply.get('total_cost_usd') or 0)
	return {'cost_usd': round(cost, 6), 'tokens': tokens}


def explain(error: Exception) -> str:
	"""What went wrong with a triage, in words the reviewer can act on."""
	if isinstance(error, FileNotFoundError):
		return (
			'The Claude Code CLI is not installed here. Install it and run `claude` once to log in.'
		)
	if isinstance(error, subprocess.TimeoutExpired):
		return f'Claude took longer than {error.timeout:.0f} s.'
	text = str(error)
	if 'login' in text.lower() or 'api key' in text.lower():
		return 'Claude is not logged in here. Run `claude` once and log in.'
	return f'Claude failed: {text[:500]}'
