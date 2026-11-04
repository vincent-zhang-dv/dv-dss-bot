"""DSS triage CLI: `python -m dss <command>`.

	python -m dss triage DSS-1234 [--posted 2026-10-21_09-30-00] [--post-file post.txt]
	                              [--thread <permalink or ts>]       # one triage, printed; no Slack
	python -m dss trial DSS-1234 [--posted ...] [--post-file ...] [--thread ...]
	                                                                 # the same as a checked record
	python -m dss eval out/eval/golden.jsonl out/eval/<run> [--judge] # score saved answers
	python -m dss cerberus                                           # set up the bot's Cerberus
	python -m dss slack                                              # the bot (see dss.slack)
	python -m dss mcp                                                # the tools over stdio (MCP)

Settings come from the environment. A `.env` file at the repo root (copy `.env.example`) fills
in the ones that are not set.
"""

import argparse
import functools
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dss import runner
from dss.cerb import START_TIME
from dss.jira import ISSUE_KEY
from dss.slack import TRIAGE_DELAY_S
from dss.thread import TS
from dss.triage import TIME_FORMAT, explain, triage
from dss.worker import ROOT

PERMALINK = re.compile(r'/p(\d+)(\d{6})(?:[?#].*)?$')  # .../archives/<channel>/p<ts digits>


def load_env(path: Path = ROOT / '.env') -> None:
	"""KEY=VALUE lines of *path* into the environment; what is already set wins."""
	if not path.exists():
		return
	for line in path.read_text(encoding='utf-8').splitlines():
		key, sep, value = line.partition('=')
		key, value = key.strip(), value.strip().strip('"\'')
		if sep and key and value and not key.startswith('#'):
			os.environ.setdefault(key, value)


def main() -> None:
	parser = argparse.ArgumentParser(prog='dss')
	commands = parser.add_subparsers(dest='command', required=True)
	one = commands.add_parser('triage', help='triage one ticket and print the analysis (no Slack)')
	add_ticket_arguments(one)
	one.add_argument('--model', default=runner.MODEL)
	trial = commands.add_parser(
		'trial', help='triage one ticket as a checked record, print the DM and thread (no Slack)'
	)
	add_ticket_arguments(trial)
	evaluate = commands.add_parser('eval', help='score saved answers against the golden set')
	evaluate.add_argument('golden', type=Path, help='golden rows, e.g. out/eval/golden.jsonl')
	evaluate.add_argument('run_dir', type=Path, help='folder with <key>.md and <key>_*.jsonl')
	evaluate.add_argument(
		'--judge', action='store_true', help='also ask Haiku whether each answer found the outcome'
	)
	commands.add_parser(
		'cerberus', help="set up or update the bot's own Cerberus copies and show what they hold"
	)
	commands.add_parser('slack', help='run the Slack bot')
	commands.add_parser('mcp', help='serve the triage tools over stdio (MCP)')
	args = parser.parse_args()
	load_env()
	if args.command == 'mcp':  # stdout is the MCP protocol from here on: print nothing
		from dss.server import build

		build().run()
	elif args.command == 'slack':
		from dss.slack import main as bot

		bot()
	elif args.command == 'cerberus':
		from dss import code

		try:
			lines = code.setup()
		except Exception as error:  # one readable line instead of a traceback
			raise SystemExit(f'cannot set up Cerberus: {error}') from error
		for line in lines:
			print(line)
	elif args.command == 'eval':
		from dss import eval as scoring

		try:
			scores = scoring.run(args.golden, args.run_dir, use_judge=args.judge)
		except (OSError, ValueError, RuntimeError) as error:  # bad file, or a lookup not set up
			raise SystemExit(f'cannot score: {error}') from error
		for result in scores:
			print(scoring.summary(result))
	elif args.command == 'trial':
		from dss import record

		posted, post = ticket_input(parser, args)
		try:
			result = record.trial(args.key, posted, post, thread_ts=args.thread or '')
		except Exception as error:  # one readable line instead of a traceback
			raise SystemExit(explain(error)) from error
		print(f'{result["dm"]}\n\n--- thread reply ---\n\n{result["thread"]}')
		check = 'passed'
		if result['problems']:
			check = f'{len(result["problems"])} problems'
		print(f'\n{usage_line(result)}\nrecord check: {check}', file=sys.stderr)
		print(f'saved to {result["file"]}', file=sys.stderr)
	else:
		posted, post = ticket_input(parser, args)
		ask = functools.partial(runner.run_reply, model=args.model)
		try:
			answer = triage(args.key, posted, post, ask=ask, thread_ts=args.thread or '')
		except Exception as error:  # one readable line instead of a traceback
			raise SystemExit(explain(error)) from error
		print(answer['text'])
		print(f'\n{usage_line(answer)}', file=sys.stderr)


def add_ticket_arguments(command: argparse.ArgumentParser) -> None:
	"""The ticket key, its time and the workflow's post, for `triage` and `trial`."""
	command.add_argument('key', help='ticket key, e.g. DSS-1234')
	command.add_argument(
		'--posted',
		default='',
		help='ticket time as YYYY-MM-DD_HH-MM-SS (local), for an older ticket; default now',
	)
	command.add_argument(
		'--post-file',
		type=Path,
		help="a text file with the support-request workflow's Slack post (summary, client, DVT)",
	)
	command.add_argument(
		'--thread',
		type=thread_ts,
		help="the workflow post's Slack permalink or ts; Claude reads its thread as the bot would",
	)


def thread_ts(value: str) -> str:
	"""The Slack ts of a post, from its permalink or the ts itself."""
	value = value.strip()
	if TS.fullmatch(value):
		return value
	link = PERMALINK.search(value)
	if not link:
		raise argparse.ArgumentTypeError(f'{value!r} is not a Slack permalink or a ts')
	return f'{link[1]}.{link[2]}'


def ticket_input(parser: argparse.ArgumentParser, args: argparse.Namespace) -> tuple[str, str]:
	"""The checked ticket time and the workflow's post; bad input stops here.

	The time defaults to the post's (`--thread`), else now. With `--thread`, `slack_thread` leaves
	out the replies that came after the bot would have started (DSS_THREAD_UNTIL).
	"""
	if not ISSUE_KEY.fullmatch(args.key):
		parser.error(f'{args.key!r} is not an issue key like DSS-1234')
	if args.posted and not START_TIME.fullmatch(args.posted):
		parser.error(f'--posted must look like 2026-10-21_09-30-00, not {args.posted!r}')
	posted = args.posted
	if args.thread:
		os.environ['DSS_THREAD_UNTIL'] = f'{float(args.thread) + TRIAGE_DELAY_S:.6f}'
		if not posted:
			posted = datetime.fromtimestamp(float(args.thread)).strftime(TIME_FORMAT)
	if not posted:
		posted = datetime.now().strftime(TIME_FORMAT)
	post = ''
	if args.post_file:
		try:
			post = args.post_file.read_text(encoding='utf-8')
		except OSError as error:
			parser.error(f'cannot read --post-file: {error}')
	return posted, post


def usage_line(answer: dict) -> str:
	usage = f'{answer["turns"]} turns · {answer["tokens"]:,} tokens'
	return usage + f' · ≈ ${answer["cost_usd"]:.2f} · {answer["seconds"]} s'


if __name__ == '__main__':
	main()
