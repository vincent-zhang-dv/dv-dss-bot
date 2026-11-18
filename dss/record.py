"""Trial mode: one triage as a checked record (`dss-triage/3`) instead of free Markdown.

Code reads the `dss_ticket` block before Claude starts. Claude follows the same steps and rules as
the bot (`triage.md`) but answers with the `triage` and `class` blocks (`triage_record.md`), as
claude's structured output against :func:`answer_schema`. Code
then checks the record against `docs/triage_record.schema.json`, checks in the run's trace that
past issues were searched before the investigation, and renders the reviewer's DM (the decision)
and its thread reply (the proof). The design is on the DSS Triage Flow page.

The Slack bot triages every new ticket this way. `python -m dss trial <key>` runs one by hand.
Each is saved in TRIAL_DIR; the bot's own record of what it posted is in out/answers.
"""

import functools
import json
import ntpath
import os
import re
import string
import traceback
from datetime import datetime
from pathlib import Path

import jsonschema  # type: ignore[import-untyped]

from dss import cerb, code, cutoff, jira, matlab, runner, triage
from dss.worker import ROOT

SCHEMA_ID = 'dss-triage/3'
SCHEMA_FILE = ROOT / 'docs' / 'triage_record.schema.json'
KNOWN_CAUSES_FILE = ROOT / 'docs' / 'known_causes.json'
ANSWER_FILE = Path(__file__).with_name('triage_record.md')  # replaces triage.md's answer section
ANSWER_HEADING = 'Answer in Markdown'  # where triage.md's answer section starts
TRIAL_DIR = ROOT / 'out' / 'trial'  # one file per trial; never out/answers, which the bot reads
RECORD_FIELDS = {**jira.TICKET_FIELDS, 'created': 'created'}
JIRA_TIME = '%Y-%m-%dT%H:%M:%S.%f%z'  # 2026-10-22T10:00:00.000-0700
WORKFLOWS = {'PE', 'WI', 'CI', 'FF'}
WORKFLOW_FEATURES = {'PE': 'pe_', 'WI': 'wi_', 'CI': 'wi_', 'FF': 'ff_'}  # schema feature prefixes
PROMPT = """New ticket {key}, posted {posted} (local time).

The DSS ticket record, read by code before you started:
{ticket}{notes}{thread}{post}"""
WORDS = {
	'not_cerberus': 'not Cerberus',
	'prior_art': 'past issues',
	'gate_a': 'Gate A',
	'gate_b': 'Gate B',
	'gate_c': 'Gate C',
	'it': 'IT',
}
HISTORY_TOOLS = {'code_log_s', 'code_log', 'code_branches'}  # triage.md step 2's git searches
# An evidence ref that names a tool call, such as code_read:cerberus/plots.py:120 (evidence_gaps)
TOOL_REF = re.compile(
	r'((?:slack|jira|cerb|code|matlab|vizapp|confluence)_[a-z_]+)(?::(.*))?', re.DOTALL
)
LINE_REF = re.compile(r'([^\s:;,]+):(\d+)(?:-(\d+))?')  # one path:line or path:first-last
LINE_TOOLS = {'code_read', 'matlab_read'}  # each call reads at most code.MAX_LINES lines
GREP_TOOLS = {'code_grep', 'matlab_grep'}  # each hit shows one line, path:line:text
GREP_HIT = re.compile(r'^([^\s:]+):(\d+):', re.MULTILINE)
# A tool whose answer code put in the record without a traced call, and the fact that holds it
RECORD_TOOLS = {'cerb_find_project': 'project', 'cerb_prod_version': 'prod_version'}
# Reading code or project files is investigation (steps 4 to 7); past issues are searched before
INVESTIGATION_TOOLS = {
	'code_read',
	'matlab_read',
	'vizapp_read',
	'cerb_read_text',
	'cerb_read_sheet',
	'cerb_client_info',
}
CODE_SPAN = re.compile(r'(`[^`\n]*`)')
MARKDOWN_MARK = re.compile(r'([\\*_~<>\[\]])')  # shown as typed once escaped: \cerberus, __init__
MAX_PROBLEMS = 10  # schema problems listed in the DM
PROBLEM_CHARS = 300
RAW_CHARS = 3000  # of a refused answer, in the thread
TRIAL_LABEL = 'Trial · draft by Claude, for you only'
LIST_ITEM = re.compile(r'\s*(\d+\.|-) ')  # a list item, nested ones too
LINKED_KEY = re.compile(r'\b(?:DSS|DTN)-[0-9]+\b')  # linked to Jira in the post and the thread
DSS_NUMBER = re.compile(r'DSS-([0-9]+)')
ARGUMENT_CHARS = 120  # of a tool call's arguments, in a leak line
# Added to a replay's prompt (python -m dss replay): what the tools hide, and the answer's block
REPLAY = """

This is a replay of an old ticket: every tool answers as of {posted}, when it was posted, and
leaves out what came later (thread replies, Jira comments and changes, later tickets, commits,
releases and branches, later sessions, log lines and files). Settle it as the bot would have then.
- Read the thread first. It shows the request, its first reply "Screenshot of error:" with the
  analyst's screenshot (what the analyst saw), and anything the analyst added before anyone else
  replied; nobody else's replies.
- `cut_at_ticket` marks a session or log read only up to the ticket time: nothing in it is later.
- A project file changed since is listed with `changed_after_ticket`, and reading it says it is
  the file as it is now.
- Compare the project files with what the screenshot and the description show. When a file
  differs from them:
  - Marked `changed_after_ticket`: the analyst likely fixed it there. Write the answer as the
    ticket stood then, as if it were not fixed, and add the file and what was fixed (the value
    then, from the screenshot or description, and the value now) to `replay.fixed_later`.
  - Not marked: the file was like this when the ticket was posted, so nothing was fixed there.
    Say the analyst may have looked at another file, run or version.
- Jira searches test only fields a ticket keeps once filed (project, key, type, created,
  reporter, summary, description) with absolute dates.
- If anything you read looks like it comes from after {posted} (an answer, a fix, a later date),
  say what, and the tool call it came through, in `replay.leaks`.
- After {calls} tool calls the tools stop answering: then answer with what you have."""


def trial(
	key: str,
	posted: str,
	post: str = '',
	ask=None,
	thread_ts: str = '',
	label: str = TRIAL_LABEL,
	ticket_link: str = '',
	analyst: str = '',
	replay: dict | None = None,
	folder: Path | None = None,
	trace: Path | None = None,
	budget: float = 0,
) -> dict:
	"""One trial triage of *key*, checked, rendered and saved in TRIAL_DIR.

	*posted*, *post* and *thread_ts* are as for :func:`dss.triage.triage`. *ask* defaults to
	:func:`dss.runner.run_reply` with the record system prompt for the ticket's workflow, the
	answer schema and *budget* (faked in tests). *label* starts
	the DM's footer, the headline's key links to *ticket_link* (else to Jira), and *analyst* heads
	the analyst's todos (else the Jira reporter); the Slack bot gives its own label, the DSS post
	and the analyst's Slack name. Returns what was saved: `ticket`, `notes` (what
	code could not read), `record` (None when the answer had no JSON), `problems` (empty when the
	record passed the check), `dm` and `thread` (Markdown), Claude's raw `text`, its cost, and
	`file`.

	A replay (:mod:`dss.replay`) gives *replay*, code's part of the record's `replay` block
	(cutoff, build and code_ref), and saves in its own *folder* with its own *trace* file.
	"""
	if not jira.ISSUE_KEY.fullmatch(key):
		raise ValueError(f'{key!r} is not an issue key like DSS-1234')
	ticket, notes = build_ticket(key, posted)
	if ask is None:
		ask = functools.partial(
			runner.run_reply,
			system=system_prompt(ticket['workflow']),
			schema=answer_schema(replay is not None),
			budget=budget,
		)
	notes += code.refresh() + matlab.refresh()
	asked = prompt(key, posted, ticket, notes, post, thread_ts)
	if replay is not None:
		asked += replay_prompt()
	if trace is None:
		trace = triage.TRACE_DIR / f'{key}_{datetime.now().strftime(triage.TIME_FORMAT)}.jsonl'
	answer = triage.run(key, posted, asked, ask, trace=trace)
	record, crash = None, ''
	gaps: dict[str, str] = {}
	facts = None
	if replay is not None:
		facts = {**replay, 'leaks_found': traced_leaks(trace)}
	try:
		record, problems = assemble(ticket, answer['text'], facts)
		if record is not None:
			problems += order_problems(record, traced_tools(trace))
		if record is None or problems:
			dm, thread = render_refused(key, problems, answer['text'], ticket_link)
			if facts is not None:  # a leak is said even when the answer failed its check
				said = {**facts, 'fixed_later': [], 'leaks': []}
				dm = _joined([dm, *_leak_lines(said)])
				thread = _joined([thread, *_code_leak_lines(said)])
		else:
			try:
				gaps = evidence_gaps(record, traced_calls(trace))
			except Exception as error:  # noqa: BLE001 - this check never costs a checked answer
				notes.append(f'evidence refs not checked against the trace: {error}')
			dm, thread = render(record, notes, links(record, notes), ticket_link, analyst, gaps)
			if gaps:
				# the blank line keeps the line out of a quoted reply above it
				dm += f'\n\n**Evidence to check:** {_gap_count(gaps)}, marked in the thread'
	except Exception as error:  # noqa: BLE001 - a paid answer is never lost to a bug in this code
		crash = traceback.format_exc()
		problems = [f'the answer could not be checked or rendered: {type(error).__name__}: {error}']
		dm, thread = render_refused(key, problems, answer['text'], ticket_link)
	usage = f'{answer["tokens"]:,} tokens · ≈ ${answer["cost_usd"]:.2f} · {answer["seconds"]} s'
	# the blank line keeps the footer out of a quoted reply above it
	dm += f'\n\n_{label} · {usage}_'
	if folder is None:
		folder = TRIAL_DIR
	folder.mkdir(parents=True, exist_ok=True)
	path = folder / f'{key}_{datetime.now().strftime(triage.TIME_FORMAT)}.json'
	result = {
		'key': key,
		'posted': posted,
		'post': post,
		'thread_ts': thread_ts,
		'ticket': ticket,
		'notes': notes,
		'record': record,
		'problems': problems,
		'evidence_gaps': gaps,
		'dm': dm,
		'thread': thread,
		**answer,
		'trace': str(trace),
		'file': str(path),
	}
	if crash:
		result['crash'] = crash
	path.write_text(json.dumps(result, indent=1), encoding='utf-8')
	return result


def system_prompt(workflow: str = 'unknown') -> str:
	"""triage.md's steps and rules, with ANSWER_FILE's record answer in place of its Markdown one.

	The known causes are those that can apply to a *workflow* ticket (:func:`for_workflow`). In
	a replay, known causes learned only from the replayed ticket or later ones are left out, and
	so is any line naming the replayed ticket or a later one: they would give the answer away.
	The general rules stay. The answer's fields are described in :func:`answer_schema`.
	"""
	steps = runner.SYSTEM.read_text(encoding='utf-8')
	cut = steps.find(ANSWER_HEADING)
	if cut < 0:
		raise ValueError(f'{runner.SYSTEM.name} has no "{ANSWER_HEADING}" section to replace')
	causes = []
	for cause in _json_file(KNOWN_CAUSES_FILE):
		if for_workflow(cause, workflow) and not learned_later(cause):
			causes.append(f'- `{cause["slug"]}`: {cause["cause"]}')
	answer = string.Template(ANSWER_FILE.read_text(encoding='utf-8')).substitute(
		known_causes='\n'.join(causes),
	)
	return _without_later_tickets(steps[:cut] + answer)


def for_workflow(cause: dict, workflow: str) -> bool:
	"""Whether the known *cause* can apply to a *workflow* ticket: not when its feature belongs
	to another workflow (WORKFLOW_FEATURES). Every cause can, for a combo or unknown workflow."""
	own = WORKFLOW_FEATURES.get(workflow)
	if own is None:
		return True
	feature = cause.get('feature') or ''
	for prefix in set(WORKFLOW_FEATURES.values()):
		if feature.startswith(prefix):
			return prefix == own
	return True


def answer_schema(replay: bool = False) -> dict:
	"""The schema claude checks Claude's answer against: the record's `triage` and `class`
	blocks, and in a *replay* the `fixed_later` and `leaks` of its `replay` block.

	Each field's description is what Claude reads about that field. claude asks Claude again
	until the answer fits, so a record that fails :func:`check` is rare.
	"""
	defs = _schema()['$defs']
	properties = {'triage': defs['triage'], 'class': defs['class']}
	if replay:
		given = defs['replay']['properties']
		properties['replay'] = {
			'type': 'object',
			'required': ['fixed_later', 'leaks'],
			'additionalProperties': False,
			'properties': {'fixed_later': given['fixed_later'], 'leaks': given['leaks']},
		}
	shape = {
		'type': 'object',
		'required': list(properties),
		'additionalProperties': False,
		'properties': properties,
	}
	return _inlined(shape, defs)


def _inlined(node, defs: dict):
	"""*node* with each `$ref` to one of *defs* replaced by that definition, so the schema
	claude gets stands alone."""
	if isinstance(node, list):
		return [_inlined(item, defs) for item in node]
	if not isinstance(node, dict):
		return node
	inlined = {}
	ref = node.get('$ref')
	if ref:
		inlined.update(_inlined(defs[ref.removeprefix('#/$defs/')], defs))
	for key, value in node.items():
		if key != '$ref':
			inlined[key] = _inlined(value, defs)
	return inlined


def _without_later_tickets(prompt: str) -> str:
	"""*prompt* without the lines that name the replayed DSS ticket or a later one; all of it
	outside a replay."""
	replayed = DSS_NUMBER.fullmatch(cutoff.key())
	if not replayed:
		return prompt
	kept = []
	for line in prompt.split('\n'):
		later = False
		for number in DSS_NUMBER.findall(line):
			if int(number) >= int(replayed[1]):
				later = True
		if not later:
			kept.append(line)
	return '\n'.join(kept)


def learned_later(cause: dict) -> bool:
	"""Whether, in a replay, the known *cause* was learned only from the replayed DSS ticket or
	later ones: it names DSS tickets, and none is older. False outside a replay."""
	replayed = DSS_NUMBER.fullmatch(cutoff.key())
	if not replayed or not cause.get('dss_tickets'):
		return False
	for key in cause['dss_tickets']:
		found = DSS_NUMBER.fullmatch(key)
		if found and int(found[1]) < int(replayed[1]):
			return False
	return True


def replay_prompt() -> str:
	"""The end of a replay's prompt: what the tools hide as of the cutoff, and the `replay` block
	the answer adds (REPLAY)."""
	then = cutoff.when()
	posted = 'the ticket time'
	if then is not None:
		posted = cutoff.shown(then)
	calls = os.environ.get('DSS_REPLAY_MAX_CALLS') or 'a set number of'
	return REPLAY.format(posted=posted, calls=calls)


def build_ticket(key: str, posted: str) -> tuple[dict, list[str]]:
	"""The `dss_ticket` block of *key*, read by code, and notes on what could not be read.

	*posted* is the ticket time in TIME_FORMAT. A source that fails leaves its facts null and adds
	a note, so a down index never stops the triage. `prod_version` is today's prod, which is the
	prod at filing time only for a new DSS ticket.
	"""
	notes: list[str] = []
	created = datetime.strptime(posted, triage.TIME_FORMAT).astimezone()
	ticket: dict = {
		'key': key,
		'created': created.isoformat(timespec='seconds'),
		'reporter': 'unknown',
		'dvt': None,
		'project': None,
		'workflow': 'unknown',
		'toolbox': None,
		'cerberus_version': None,
		'prod_version': None,
		'last_error': None,
		'terminal_log': None,
	}
	try:
		issue = jira.get_issue(key, RECORD_FIELDS)
	except Exception as error:  # noqa: BLE001 - Claude reads Jira itself and reports the failure
		notes.append(f'Jira: {error}')
		issue = {}
	if issue.get('created'):
		ticket['created'] = _jira_time(issue['created'])
	if issue.get('reporter'):
		ticket['reporter'] = str(issue['reporter'])
	if issue.get('toolbox'):
		ticket['toolbox'] = str(issue['toolbox'])
	ticket['dvt'] = _dvt(issue.get('dvt'))
	if ticket['dvt'] is not None:
		ticket['project'] = _project(ticket['dvt'], issue.get('project_path'), notes)
	elif issue:
		notes.append('the DSS ticket has no DVT number')
	if ticket['project']:
		ticket['workflow'] = workflow(ticket['project'])
		_last_session(ticket, posted, notes)
	try:
		ticket['prod_version'] = cerb.prod_version()
	except Exception as error:  # noqa: BLE001 - a missing fact is noted, not fatal
		notes.append(f'prod version: {error}')
	return ticket, notes


def workflow(project: str) -> str:
	"""The workflow of a project folder named DVT<n>_<date>_<type>_<well>.

	PE, WI, CI or FF; `combo` for a joined type such as CI-PE; `unknown` for any other name.
	"""
	parts = ntpath.basename(project).split('_')
	if len(parts) < 3:
		return 'unknown'
	kinds = parts[2].upper().split('-')
	if len(kinds) == 1 and kinds[0] in WORKFLOWS:
		return kinds[0]
	if len(kinds) > 1 and set(kinds) <= WORKFLOWS:
		return 'combo'
	return 'unknown'


def prompt(
	key: str, posted: str, ticket: dict, notes: list[str], post: str = '', thread_ts: str = ''
) -> str:
	"""The trial's prompt: the bot's ticket and time, the `dss_ticket` block, the Slack thread
	and the post."""
	return PROMPT.format(
		key=key,
		posted=posted,
		ticket=json.dumps(ticket, indent=1),
		notes=triage.noted(notes),
		thread=triage.slack_thread(thread_ts),
		post=triage.workflow_post(post),
	)


def assemble(ticket: dict, text: str, replay: dict | None = None) -> tuple[dict | None, list[str]]:
	"""The record made of *ticket* and Claude's answer *text*, and what is wrong with it.

	The record is None when *text* holds no JSON object. In a replay, *replay* is code's part of
	the `replay` block, and the answer's `replay` block gives the rest.
	"""
	try:
		answer = parse(text)
	except ValueError as error:
		return None, [str(error)]
	record = {'schema': SCHEMA_ID, 'dss_ticket': ticket}
	blocks = {'triage', 'class'}
	for block in ('triage', 'class'):
		if block in answer:
			record[block] = answer[block]
	if replay is not None:
		blocks.add('replay')
		given = answer.get('replay')
		if not isinstance(given, dict):
			given = {}
		record['replay'] = {
			**replay,
			'fixed_later': given.get('fixed_later') or [],
			'leaks': given.get('leaks') or [],
		}
	problems = check(record)
	extra = sorted(set(answer) - blocks)
	if extra:
		problems.append(f'the answer has blocks the record does not take: {", ".join(extra)}')
	return record, problems


def parse(text: str) -> dict:
	"""The JSON object in Claude's answer *text*, with or without a code fence around it."""
	start = text.find('{')
	end = text.rfind('}')
	if start < 0 or end < start:
		raise ValueError('the answer has no JSON object')
	try:
		return json.loads(text[start : end + 1])
	except json.JSONDecodeError as error:
		raise ValueError(f'the answer is not valid JSON: {error}') from error


def check(record: dict) -> list[str]:
	"""What is wrong with *record* by the schema, one line each; empty when it passes."""
	problems = []
	for error in _validator().iter_errors(record):
		where = '.'.join(str(part) for part in error.absolute_path) or 'record'
		problems.append(f'{where}: {error.message}'[:PROBLEM_CHARS])
	return problems


def traced_calls(trace: Path) -> list[dict]:
	"""The calls of a run that got an answer, Claude's and code's (`by: code`), from its *trace*;
	none when it has none. A call that failed or was held back read nothing, so it is left out."""
	if not trace.exists():
		return []
	calls = []
	for line in trace.read_text(encoding='utf-8').splitlines():
		if line.strip():
			call = json.loads(line)
			if not call.get('blocked') and 'error' not in call:
				calls.append(call)
	return calls


def evidence_gaps(record: dict, calls: list[dict]) -> dict[str, str]:
	"""The evidence refs of *record* that the run's *calls* (:func:`traced_calls`) do not show,
	each with why. Only what the trace settles is said:

	- a ref that names a tool no call of which got an answer; code's reads count, and so does a
	  fact code put in the record (RECORD_TOOLS);
	- a `code_read` or `matlab_read` ref to one file and line (or line range) that no read of the
	  run shows: a `code_read` or `matlab_read` of that file over the line, or a grep hit on it
	  (Claude names the reading tool loosely); a ref that lists several is not taken apart;
	- a `terminal_log` ref when the record has no terminal log and Claude read none.

	`computed` and every other ref are left alone.
	"""
	ticket = record['dss_ticket']
	ran = set()
	log = ticket.get('terminal_log')
	log_read = isinstance(log, dict) and bool(log.get('found'))
	lines_read: dict[str, list[tuple[str, int, int]]] = {}  # file name: (path, first, last)
	for call in calls:
		tool = call['tool']
		ran.add(tool)
		if tool == 'cerb_read_text' or (tool == 'cerb_last_session' and call.get('by') != 'code'):
			log_read = True
		if tool in LINE_TOOLS:
			arguments = call.get('arguments') or {}
			path = _path_key(arguments.get('path'))
			first = max(int(arguments.get('start') or 1), 1)
			last = first + code.MAX_LINES - 1
			if arguments.get('end'):
				last = min(last, int(arguments['end']))
			lines_read.setdefault(ntpath.basename(path), []).append((path, first, last))
		if tool in GREP_TOOLS:
			for hit in GREP_HIT.finditer(call.get('result') or ''):
				found = _path_key(hit[1])
				line = int(hit[2])
				lines_read.setdefault(ntpath.basename(found), []).append((found, line, line))
	for tool, fact in RECORD_TOOLS.items():
		if ticket.get(fact):
			ran.add(tool)
	gaps = {}
	for item in record['triage'].get('evidence') or []:
		ref = item['ref']
		if ref.startswith('terminal_log'):
			if not log_read:
				gaps[ref] = 'the record has no terminal log and this run read none'
			continue
		named = TOOL_REF.fullmatch(ref)
		if not named:
			continue
		tool = named[1]
		if tool not in ran and not (tool in LINE_TOOLS and ran & LINE_TOOLS):
			gaps[ref] = f'no `{tool}` call of this run got an answer'
			continue
		cited = LINE_REF.fullmatch(named[2] or '')
		if tool not in LINE_TOOLS or not cited:
			continue
		path = _path_key(cited[1])
		ends = [int(cited[2])]
		if cited[3]:
			ends.append(int(cited[3]))
		same_file = False
		unread = set(ends)
		for read_path, first, last in lines_read.get(ntpath.basename(path), []):
			if not _same_file(path, read_path):
				continue
			same_file = True
			for line in ends:
				if first <= line <= last:
					unread.discard(line)
		if not same_file:
			gaps[ref] = 'no read or search of this run shows that file'
		elif len(unread) == 1:
			gaps[ref] = f'no read or search of this run shows line {min(unread)} of that file'
		elif unread:
			lines = f'lines {min(unread)} and {max(unread)}'
			gaps[ref] = f'no read or search of this run shows {lines} of that file'
	return gaps


def traced_leaks(trace: Path) -> list[str]:
	"""What code found in a replay's tool answers that may come from after the ticket (the
	`leaks` of its *trace*, :func:`dss.cutoff.leaks`), one line per tool call."""
	if not trace.exists():
		return []
	found = []
	for line in trace.read_text(encoding='utf-8').splitlines():
		if not line.strip():
			continue
		call = json.loads(line)
		if not call.get('leaks'):
			continue
		arguments = []
		for name, value in call['arguments'].items():
			arguments.append(f'{name}={value}')
		shown = ', '.join(arguments)[:ARGUMENT_CHARS]
		found.append(f'{call["tool"]}({shown}): {"; ".join(call["leaks"])}')
	return found


def traced_tools(trace: Path) -> list[str]:
	"""The tools a run called, in order, from its *trace*; none when it called none. A call the
	server held back until past issues were searched read nothing, so it is left out."""
	if not trace.exists():
		return []
	tools = []
	for line in trace.read_text(encoding='utf-8').splitlines():
		if line.strip():
			call = json.loads(line)
			if not call.get('blocked'):
				tools.append(call['tool'])
	return tools


def order_problems(record: dict, tools: list[str]) -> list[str]:
	"""Where the run's *tools* break triage.md's order: past DSS and DTN tickets (`jira_search`)
	and git history are searched before the investigation reads code or project files. A ticket
	that is not a Cerberus issue needs no git search."""
	before = []
	for tool in tools:
		if tool in INVESTIGATION_TOOLS:
			break
		before.append(tool)
	problems = []
	if 'jira_search' not in before:
		problems.append(
			'trace: no jira_search for past DSS and DTN tickets before the investigation'
		)
	status = ''
	answer = record.get('triage')
	if isinstance(answer, dict):  # a malformed block is already a schema problem
		status = answer.get('status', '')
	if status != 'not_cerberus' and not HISTORY_TOOLS & set(before):
		problems.append(
			'trace: no git history search (code_log_s, code_log or code_branches) before the '
			'investigation'
		)
	return problems


def render(
	record: dict,
	notes: list[str],
	links: dict[str, str] | None = None,
	ticket_link: str = '',
	analyst: str = '',
	gaps: dict[str, str] | None = None,
) -> tuple[str, str]:
	"""The DM (the decision) and its thread reply (the proof) of a checked *record*, in Markdown.

	Each field is shown once, and empty fields are left out. *notes* are what code could not read,
	and *gaps* (:func:`evidence_gaps`) mark the evidence the run's trace does not show.
	Claude's text is escaped (:func:`_text`), so paths and names show as written. Each name in
	*links* (:func:`links`) is linked below the headline, whose key links to *ticket_link*. The
	reply to the analyst links only Jira tickets: analysts cannot open Cerberus on GitHub.
	*analyst* (a Slack name) heads the analyst's todos.
	"""
	decision, proof = _decision(record, ticket_link, analyst), _proof(record, notes, gaps)
	if not links:
		return decision, proof
	tickets = {}
	for name, url in links.items():
		if LINKED_KEY.fullmatch(name):
			tickets[name] = url
	lines = decision.split('\n')
	for index in range(1, len(lines)):  # the headline keeps only its key's link
		if lines[index].startswith('> '):  # the reply
			lines[index] = _linked(lines[index], tickets)
		else:
			lines[index] = _linked(lines[index], links)
	return '\n'.join(lines), _linked(proof, links)


def links(record: dict, notes: list[str]) -> dict[str, str]:
	"""Where the names in *record* link: DSS and DTN keys to Jira, and Cerberus commits, branches,
	PRs and releases to GitHub (:func:`dss.code.web_links`). When git fails, the reason is added to
	*notes* and the post goes out without GitHub links."""
	text = '\n'.join(_strings(record))
	found = {}
	if jira.setting('JIRA_URL'):
		for key in LINKED_KEY.findall(text):
			found[key] = jira.browse_url(key)
	try:
		found.update(code.web_links(text))
	except Exception as error:  # noqa: BLE001 - a link is a convenience
		notes.append(f'GitHub links not added: {error}')
	return found


def render_refused(
	key: str, problems: list[str], text: str, ticket_link: str = ''
) -> tuple[str, str]:
	"""The DM listing why the answer was refused, and a thread reply with the answer as given."""
	lines = [f'**{_key(key, ticket_link)} · the trial answer failed the record check**']
	for problem in problems[:MAX_PROBLEMS]:
		lines.append(f'- {_text(problem)}')
	if len(problems) > MAX_PROBLEMS:
		lines.append(f'- and {len(problems) - MAX_PROBLEMS} more')
	thread = f"**Claude's answer as given**\n```\n{text[:RAW_CHARS]}\n```"
	if len(text) > RAW_CHARS:
		thread += f'\n(cut at {RAW_CHARS:,} of {len(text):,} characters; the whole answer is saved)'
	return '\n'.join(lines), thread


def _decision(record: dict, ticket_link: str = '', analyst: str = '') -> str:
	"""The post: headline, the bot's summary, each side's todos, and the draft reply last.

	The analyst's questions are their "Answer" todos, and what is not proved is the developers'
	"Confirm" todos, so each is said once. A side with nothing to do is left out. *analyst* is the
	analyst's Slack name; the Jira reporter's name stands in without it.
	"""
	ticket, answer = record['dss_ticket'], record['triage']
	key = _key(ticket['key'], ticket_link)
	head = f'**{key} · {_text(answer["headline"])} · {_words(answer["status"])}**'
	lines = [head]
	if record.get('replay'):
		lines.extend(_leak_lines(record['replay']))
	lines.append(f'**Bot Summary:** {_text(answer["what"])}')
	analyst_todos = list(answer['actions']['analyst'])
	for question in answer.get('questions') or []:
		analyst_todos.append(f'Answer: {question["ask"]}')
	developer_todos = list(answer['actions']['developers'])
	for check in answer.get('not_proved') or []:
		if check.lower().startswith('confirm '):  # "Confirm: Confirm the ..." says it twice
			check = check[len('confirm ') :]
		developer_todos.append(f'Confirm: {check}')
	_todos(lines, f'{analyst or ticket["reporter"]} Todos', analyst_todos)
	_todos(lines, 'Data Dev Todos', developer_todos)
	if answer.get('reply'):
		lines.append(f'**Draft Reply:**\n{_quote(answer["reply"])}')
	return _joined(lines)


def _leak_lines(replay: dict) -> list[str]:
	"""What a replay could not keep from Claude, for the post: each file whose version now shows
	the fix (the answer is written as if it were not fixed), each possible leak Claude saw with the
	tool call it came through, and how many tool answers code flagged (listed in the thread,
	:func:`_code_leak_lines`)."""
	lines = []
	for fixed in replay['fixed_later']:
		lines.append(f'**Fixed later in a file:** {_text(fixed["file"])}: {_text(fixed["fix"])}')
	for leak in replay['leaks']:
		lines.append(f'**Possible leak:** {_text(leak["what"])} (via {_text(leak["via"])})')
	flagged = len(replay['leaks_found'])
	if flagged:
		answers = f'{flagged} tool answers'
		if flagged == 1:
			answers = '1 tool answer'
		lines.append(f'**Possible leaks found by code:** {answers}, listed in the thread')
	return lines


def _code_leak_lines(replay: dict) -> list[str]:
	"""The tool answers code flagged in a replay (:func:`dss.cutoff.leaks`), for the thread."""
	lines: list[str] = []
	_bullets(lines, 'Possible leaks found by code', replay['leaks_found'])
	return lines


def _todos(lines: list[str], title: str, items: list[str]) -> None:
	"""*items* numbered under *title*, or nothing when there are none. The list is not nested in
	a bullet, as Slack numbers a nested list with letters."""
	if not items:
		return
	lines.append(f'**{_text(title)}:**')
	for number, item in enumerate(items, 1):
		lines.append(f'{number}. {_text(item)}')


def _proof(record: dict, notes: list[str], gaps: dict[str, str] | None = None) -> str:
	"""The thread: why, past issues, evidence, what the questions settle, details, also found and
	confidence. The class, labels, escalate and the facts code read stay in the saved record. An
	evidence item whose ref is in *gaps* (:func:`evidence_gaps`) says what the trace lacks."""
	answer = record['triage']
	lines = []
	if answer.get('cause'):
		why = _text(answer['cause'])
		if answer.get('where'):
			why += f' ({_where(answer["where"])})'
		lines.append(f'**Why:** {why}')
	past = answer['prior_art']
	if past:
		lines.append('**Past issues**')
		for number, item in enumerate(past, 1):
			state = f'{_words(item["state"])}, applies: {item["applies"]}'
			what = f'{_sentence(item["what"])} {_text(item["why"])}'
			lines.append(f'{number}. {_text(item["ref"])} ({state}): {what}')
	else:
		lines.append('**Past issues:** none found')
	evidence = answer.get('evidence') or []
	if evidence:
		lines.append('**Evidence**')
		for number, item in enumerate(evidence, 1):
			line = f'{number}. {_text(item["fact"])} (`{item["ref"]}`)'
			if gaps and item['ref'] in gaps:
				line += f' · **not shown by the trace:** {gaps[item["ref"]]}'
			lines.append(line)
	settles = []
	for question in answer.get('questions') or []:
		settles.append(f'{question["ask"]}: settles {question["settles"]}')
	_bullets(lines, 'What the questions settle', settles)
	details = []
	for name, value in (answer.get('details') or {}).items():
		if name != 'playbook':
			details.append(f'{_words(name)}: {_value(value)}')
	_bullets(lines, 'Details', details, escape=False)  # _value escapes
	secondary = []
	for finding in answer.get('secondary') or []:
		secondary.append(finding['cause'])
	_bullets(lines, 'Also found', secondary)
	step = _words(answer['settled_at'])
	playbook = answer.get('playbook')
	if playbook and answer['settled_at'] == 'playbook':
		step = f'the {_words(playbook)} playbook'
	elif playbook:
		step += f' ({_words(playbook)} playbook)'
	settled = f'{answer["confidence"]}, settled at {step}'
	unread = []  # what Claude and code could not read, each said once
	for item in (answer.get('flags') or []) + notes:
		if item not in unread:
			unread.append(item)
	if unread:
		settled += '; could not read:'
	lines.append(f'**Confidence:** {settled}')
	for item in unread:
		lines.append(f'- {_text(item)}')
	if record.get('replay'):
		lines.extend(_code_leak_lines(record['replay']))
	return _joined(lines)


def _linked(markdown: str, links: dict[str, str]) -> str:
	"""*markdown* with each name in *links* made a link, outside code spans. Longer names go first,
	so the branch DTN-3726-fix is not linked as the key DTN-3726."""
	if not links:
		return markdown
	urls = {}
	for name, url in links.items():
		urls[_text(name)] = url  # the name as it shows in the escaped text
	names = []
	for name in sorted(urls, key=len, reverse=True):
		names.append(re.escape(name))
	pattern = re.compile(rf'(?<![\w./\\-])({"|".join(names)})(?![\w/-]|\.\w)')

	def link(match: re.Match) -> str:
		return f'[{match[1]}]({urls[match[1]]})'

	parts = CODE_SPAN.split(markdown)
	for index in range(0, len(parts), 2):  # the even parts lie outside code spans
		parts[index] = pattern.sub(link, parts[index])
	return ''.join(parts)


def _joined(lines: list[str]) -> str:
	"""*lines* as Markdown, with a blank line between a list and the heading after it: without
	one, the heading joins the last item and the next list goes on with its numbers."""
	text = []
	for index, line in enumerate(lines):
		if index and line.startswith('**') and LIST_ITEM.match(lines[index - 1]):
			text.append('')
		text.append(line)
	return '\n'.join(text)


def _bullets(lines: list[str], title: str, items: list[str], escape: bool = True) -> None:
	"""*items* under *title*, escaped (:func:`_text`) unless they are rendered already."""
	if items:
		lines.append(f'**{title}**')
		for item in items:
			if escape:
				item = _text(item)
			lines.append(f'- {item}')


def _where(where: dict) -> str:
	text = _words(where['kind'])
	if where.get('path'):
		text += f' `{where["path"]}`'
	if where.get('detail'):
		text += f', {_text(where["detail"])}'
	return text


def _value(value) -> str:
	"""A details value in words, escaped: {"value", "where"} as "value (where)"."""
	if isinstance(value, dict):
		if 'value' in value:
			if value.get('where'):
				return f'{_text(value["value"])} ({_text(value["where"])})'
			return _text(value['value'])
		parts = []
		for name, part in value.items():
			parts.append(f'{_words(name)} {_value(part)}')
		return '; '.join(parts)
	if isinstance(value, list):
		parts = []
		for part in value:
			parts.append(_value(part))
		return ', '.join(parts)
	return _text(value)


def _text(value) -> str:
	"""Claude's text as Markdown that shows as written: marks escaped, `code` spans kept."""
	parts = CODE_SPAN.split(str(value))
	for index in range(0, len(parts), 2):  # the even parts lie outside code spans
		parts[index] = MARKDOWN_MARK.sub(r'\\\1', parts[index])
	return ''.join(parts)


def _sentence(text) -> str:
	"""*text* escaped (:func:`_text`), ending in a full stop, so the next sentence doesn't run on."""
	text = _text(text).rstrip()
	if text and text[-1] not in '.!?':
		text += '.'
	return text


def _quote(text: str) -> str:
	lines = []
	for line in _text(text).splitlines():
		lines.append(f'> {line}')
	return '\n'.join(lines)


def _words(value: str) -> str:
	return WORDS.get(value, value.replace('_', ' '))


def _key(key: str, ticket_link: str) -> str:
	"""The headline's *key*, linked to *ticket_link* (the DSS post), else to Jira."""
	if ticket_link:
		return f'[{key}]({ticket_link})'
	return _link(key)


def _link(key: str) -> str:
	if not jira.setting('JIRA_URL'):
		return key
	return f'[{key}]({jira.browse_url(key)})'


def _strings(value) -> list[str]:
	"""Every string in *value*, a record or a part of one."""
	if isinstance(value, str):
		return [value]
	if isinstance(value, dict):
		value = list(value.values())
	strings = []
	if isinstance(value, list):
		for part in value:
			strings.extend(_strings(part))
	return strings


def _dvt(value) -> int | None:
	"""Jira's DVT field (a number such as 1234.0) as an int; None when empty or not a number."""
	try:
		number = int(float(value))
	except (TypeError, ValueError):
		return None
	if number <= 0:
		return None
	return number


def _jira_time(value: str) -> str:
	try:
		return datetime.strptime(value, JIRA_TIME).isoformat(timespec='seconds')
	except ValueError:
		return value


def _project(dvt: int, project_path, notes: list[str]) -> str | None:
	"""The project folder of *dvt*; Jira's Project Path picks between several, as in triage.md."""
	try:
		folders = cerb.find_project(dvt)['projects']
	except Exception as error:  # noqa: BLE001 - a missing fact is noted, not fatal
		notes.append(f'project index: {error}')
		return None
	if len(folders) == 1:
		return folders[0]
	if not folders:
		notes.append(f'no project folder for DVT{dvt} in the project index (maybe not indexed yet)')
		return None
	named = []
	for folder in folders:
		if ntpath.basename(folder).lower() in str(project_path or '').lower():
			named.append(folder)
	if len(named) == 1:
		return named[0]
	notes.append(f'{len(folders)} project folders for DVT{dvt}; Project Path does not pick one')
	return None


def _last_session(ticket: dict, posted: str, notes: list[str]) -> None:
	"""The reporter's Cerberus version, last failed run and terminal log, from their last session
	before *posted*, into *ticket*. A missing terminal log is noted only where Cerberus writes
	one (1.1 and later)."""
	if ticket['reporter'] == 'unknown':
		return
	try:
		session = cerb.last_session(ticket['project'], ticket['reporter'], posted)
	except Exception as error:  # noqa: BLE001 - a missing fact is noted, not fatal
		notes.append(f'telemetry: {error}')
		return
	if not session['found']:
		notes.append(f'no Cerberus telemetry log of {ticket["reporter"]} before {posted}')
		return
	ticket['cerberus_version'] = session['cerb'] or None
	if session['failed_runs']:
		run = session['failed_runs'][-1]
		ticket['last_error'] = f'{run["name"]}: {run["result_detail"] or run["status"]}'
	log = session['terminal_log']
	ticket['terminal_log'] = log
	if not log['found'] and cerb.writes_terminal_log(session['cerb']):
		notes.append(log['why'])


@functools.cache
def _schema() -> dict:
	return _json_file(SCHEMA_FILE)


def _json_file(path: Path):
	"""The JSON in *path*; a ValueError naming the file when it is not valid JSON."""
	try:
		return json.loads(path.read_text(encoding='utf-8'))
	except json.JSONDecodeError as error:
		raise ValueError(
			f'{path.name} is not valid JSON ({error}); fix it or restore it with git'
		) from error


@functools.cache
def _validator():
	return jsonschema.Draft202012Validator(_schema())


def _path_key(path) -> str:
	"""A repo *path* as written in a call or an evidence ref, for comparing the two."""
	return str(path or '').strip().replace('\\', '/').lower().strip('/')


def _same_file(cited: str, read: str) -> bool:
	"""Whether the *cited* path (:func:`_path_key`) is the file a call *read*: the same path, or
	one given from a folder further down, such as modules/plots.py for cerberus/modules/plots.py."""
	if cited == read:
		return True
	return read.endswith('/' + cited) or cited.endswith('/' + read)


def _gap_count(gaps: dict[str, str]) -> str:
	"""How many evidence refs the trace does not show, in words for the DM."""
	if len(gaps) == 1:
		return '1 evidence ref cites a read the trace does not show'
	return f'{len(gaps)} evidence refs cite a read the trace does not show'
