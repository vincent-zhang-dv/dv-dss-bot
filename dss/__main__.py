"""DSS triage CLI: `python -m dss <command>`.

	python -m dss triage DSS-1234 [--posted 2026-10-21_09-30-00] [--post-file post.txt]
	                                                                 # one triage, printed; no Slack
	python -m dss eval out/eval/golden.jsonl out/eval/<run> [--judge] # score saved answers
	python -m dss slack                                              # the bot (see dss.slack)
	python -m dss mcp                                                # the tools over stdio (MCP)

Settings come from the environment. A `.env` file at the repo root (copy `.env.example`) fills
in the ones that are not set.
"""

import argparse
import functools
import os
import sys
from datetime import datetime
from pathlib import Path

from dss import runner
from dss.cerb import START_TIME
from dss.jira import ISSUE_KEY
from dss.triage import TIME_FORMAT, explain, triage
from dss.worker import ROOT


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
	one.add_argument('key', help='ticket key, e.g. DSS-1234')
	one.add_argument(
		'--posted',
		default='',
		help='ticket time as YYYY-MM-DD_HH-MM-SS (local), for an older ticket; default now',
	)
	one.add_argument(
		'--post-file',
		type=Path,
		help="a text file with the support-request workflow's Slack post (summary, client, DVT)",
	)
	one.add_argument('--model', default=runner.MODEL)
	evaluate = commands.add_parser('eval', help='score saved answers against the golden set')
	evaluate.add_argument('golden', type=Path, help='golden rows, e.g. out/eval/golden.jsonl')
	evaluate.add_argument('run_dir', type=Path, help='folder with <key>.md and <key>_*.jsonl')
	evaluate.add_argument(
		'--judge', action='store_true', help='also ask Haiku whether each answer found the outcome'
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
	elif args.command == 'eval':
		from dss import eval as scoring

		try:
			scores = scoring.run(args.golden, args.run_dir, use_judge=args.judge)
		except (OSError, ValueError, RuntimeError) as error:  # bad file, or a lookup not set up
			raise SystemExit(f'cannot score: {error}') from error
		for result in scores:
			print(scoring.summary(result))
	else:
		if not ISSUE_KEY.fullmatch(args.key):
			parser.error(f'{args.key!r} is not an issue key like DSS-1234')
		if args.posted and not START_TIME.fullmatch(args.posted):
			parser.error(f'--posted must look like 2026-10-21_09-30-00, not {args.posted!r}')
		posted = args.posted
		if not posted:
			posted = datetime.now().strftime(TIME_FORMAT)
		post = ''
		if args.post_file:
			try:
				post = args.post_file.read_text(encoding='utf-8')
			except OSError as error:
				parser.error(f'cannot read --post-file: {error}')
		ask = functools.partial(runner.run_reply, model=args.model)
		try:
			answer = triage(args.key, posted, post, ask=ask)
		except Exception as error:  # one readable line instead of a traceback
			raise SystemExit(explain(error)) from error
		print(answer['text'])
		usage = f'{answer["turns"]} turns · {answer["tokens"]:,} tokens'
		usage += f' · ≈ ${answer["cost_usd"]:.2f} · {answer["seconds"]} s'
		print(f'\n{usage}', file=sys.stderr)


if __name__ == '__main__':
	main()
