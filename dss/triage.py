"""One DSS ticket triage: the prompt, the locked-down Claude run, and what it cost.

The Slack bot runs one for each new ticket; ``python -m dss triage <key>`` runs one by hand.
"""

import subprocess
import time

from dss import runner

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


def triage(key: str, posted: str, post: str = '', ask=runner.run_reply) -> dict:
	"""Claude's analysis of ticket *key*: `text`, `cost_usd`, `tokens` and `seconds`.

	*posted* is the ticket time in TIME_FORMAT; the triage looks for the reporter's Cerberus
	session before it. *post* is the support-request workflow's message, when there is one.
	*ask* is :func:`dss.runner.run_reply` (faked in tests).
	"""
	if post:
		post = f'\n\nThe support-request workflow posted:\n{post[:MAX_POST]}'
	started = time.monotonic()
	reply = ask(PROMPT.format(key=key, posted=posted, post=post))
	seconds = round(time.monotonic() - started)
	return {'text': reply['result'].strip(), **spend(reply), 'seconds': seconds}


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
