"""Trial mode: one triage as a checked record (`dss-triage/3`) instead of free Markdown.

Code reads the `dss_ticket` block before Claude starts. Claude follows the same steps and rules as
the bot (`triage.md`) but answers with the `triage` and `class` blocks (`triage_record.md`). Code
then checks the record against `docs/triage_record.schema.json`, checks in the run's trace that
past issues were searched before the investigation, and renders the reviewer's DM (the decision)
and its thread reply (the proof). The design is on the DSS Triage Flow page.

The Slack bot does not use this yet. `python -m dss trial <key>` runs one by hand and saves it in
TRIAL_DIR, away from the bot's saved answers.
"""

import functools
import json
import ntpath
import re
import string
import traceback
from datetime import datetime
from pathlib import Path

import jsonschema  # type: ignore[import-untyped]

from dss import cerb, code, jira, runner, triage
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
# Reading code or project files is investigation (steps 4 to 7); past issues are searched before
INVESTIGATION_TOOLS = {'code_read', 'cerb_read_text', 'cerb_read_sheet', 'cerb_client_info'}
CODE_SPAN = re.compile(r'(`[^`\n]*`)')
MARKDOWN_MARK = re.compile(r'([\\*_~<>\[\]])')  # shown as typed once escaped: \cerberus, __init__
MAX_PROBLEMS = 10  # schema problems listed in the DM
PROBLEM_CHARS = 300
RAW_CHARS = 3000  # of a refused answer, in the thread
TRIAL_LABEL = 'Trial · draft by Claude, for you only'
LIST_ITEM = re.compile(r'\s*(\d+\.|-) ')  # a list item, nested ones too
LINKED_KEY = re.compile(r'\b(?:DSS|DTN)-[0-9]+\b')  # linked to Jira in the post and the thread


def trial(
	key: str,
	posted: str,
	post: str = '',
	ask=None,
	thread_ts: str = '',
	label: str = TRIAL_LABEL,
	ticket_link: str = '',
	analyst: str = '',
) -> dict:
	"""One trial triage of *key*, checked, rendered and saved in TRIAL_DIR.

	*posted*, *post* and *thread_ts* are as for :func:`dss.triage.triage`. *ask* defaults to
	:func:`dss.runner.run_reply` with the record system prompt (faked in tests). *label* starts
	the DM's footer, the headline's key links to *ticket_link* (else to Jira), and *analyst* heads
	the analyst's todos (else the Jira reporter); the Slack bot gives its own label, the DSS post
	and the analyst's Slack name. Returns what was saved: `ticket`, `notes` (what
	code could not read), `record` (None when the answer had no JSON), `problems` (empty when the
	record passed the check), `dm` and `thread` (Markdown), Claude's raw `text`, its cost, and
	`file`.
	"""
	if not jira.ISSUE_KEY.fullmatch(key):
		raise ValueError(f'{key!r} is not an issue key like DSS-1234')
	if ask is None:
		ask = functools.partial(runner.run_reply, system=system_prompt())
	ticket, notes = build_ticket(key, posted)
	notes += code.refresh()
	asked = prompt(key, posted, ticket, notes, post, thread_ts)
	trace = triage.TRACE_DIR / f'{key}_{datetime.now().strftime(triage.TIME_FORMAT)}.jsonl'
	answer = triage.run(key, posted, asked, ask, trace=trace)
	record, crash = None, ''
	try:
		record, problems = assemble(ticket, answer['text'])
		if record is not None:
			problems += order_problems(record, traced_tools(trace))
		if record is None or problems:
			dm, thread = render_refused(key, problems, answer['text'], ticket_link)
		else:
			dm, thread = render(record, notes, links(record, notes), ticket_link, analyst)
	except Exception as error:  # noqa: BLE001 - a paid answer is never lost to a bug in this code
		crash = traceback.format_exc()
		problems = [f'the answer could not be checked or rendered: {type(error).__name__}: {error}']
		dm, thread = render_refused(key, problems, answer['text'], ticket_link)
	usage = f'{answer["tokens"]:,} tokens · ≈ ${answer["cost_usd"]:.2f} · {answer["seconds"]} s'
	# the blank line keeps the footer out of a quoted reply above it
	dm += f'\n\n_{label} · {usage}_'
	TRIAL_DIR.mkdir(parents=True, exist_ok=True)
	path = TRIAL_DIR / f'{key}_{datetime.now().strftime(triage.TIME_FORMAT)}.json'
	result = {
		'key': key,
		'posted': posted,
		'post': post,
		'thread_ts': thread_ts,
		'ticket': ticket,
		'notes': notes,
		'record': record,
		'problems': problems,
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


def system_prompt() -> str:
	"""triage.md's steps and rules, with ANSWER_FILE's record answer in place of its Markdown one."""
	steps = runner.SYSTEM.read_text(encoding='utf-8')
	cut = steps.find(ANSWER_HEADING)
	if cut < 0:
		raise ValueError(f'{runner.SYSTEM.name} has no "{ANSWER_HEADING}" section to replace')
	defs = _schema()['$defs']
	causes = []
	for cause in json.loads(KNOWN_CAUSES_FILE.read_text(encoding='utf-8')):
		causes.append(f'- `{cause["slug"]}`: {cause["cause"]}')
	answer = string.Template(ANSWER_FILE.read_text(encoding='utf-8')).substitute(
		cause_families=_choices(defs['cause_family']['enum']),
		features=_choices(defs['feature']['enum']),
		prevents=_choices(defs['prevent']['enum']),
		fix_types=_choices(defs['class']['properties']['fix_type']['enum']),
		known_causes='\n'.join(causes),
	)
	return steps[:cut] + answer


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


def assemble(ticket: dict, text: str) -> tuple[dict | None, list[str]]:
	"""The record made of *ticket* and Claude's answer *text*, and what is wrong with it.

	The record is None when *text* holds no JSON object.
	"""
	try:
		answer = parse(text)
	except ValueError as error:
		return None, [str(error)]
	record = {'schema': SCHEMA_ID, 'dss_ticket': ticket}
	for block in ('triage', 'class'):
		if block in answer:
			record[block] = answer[block]
	problems = check(record)
	extra = sorted(set(answer) - {'triage', 'class'})
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


def traced_tools(trace: Path) -> list[str]:
	"""The tools a run called, in order, from its *trace*; none when it called none."""
	if not trace.exists():
		return []
	tools = []
	for line in trace.read_text(encoding='utf-8').splitlines():
		if line.strip():
			tools.append(json.loads(line)['tool'])
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
) -> tuple[str, str]:
	"""The DM (the decision) and its thread reply (the proof) of a checked *record*, in Markdown.

	Each field is shown once, and empty fields are left out. *notes* are what code could not read.
	Claude's text is escaped (:func:`_text`), so paths and names show as written. Each name in
	*links* (:func:`links`) is linked below the headline, whose key links to *ticket_link*. The
	reply to the analyst links only Jira tickets: analysts cannot open Cerberus on GitHub.
	*analyst* (a Slack name) heads the analyst's todos.
	"""
	decision, proof = _decision(record, ticket_link, analyst), _proof(record, notes)
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
	lines = [head, f'**Bot Summary:** {_text(answer["what"])}']
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


def _todos(lines: list[str], title: str, items: list[str]) -> None:
	"""*items* numbered under *title*, or nothing when there are none. The list is not nested in
	a bullet, as Slack numbers a nested list with letters."""
	if not items:
		return
	lines.append(f'**{_text(title)}:**')
	for number, item in enumerate(items, 1):
		lines.append(f'{number}. {_text(item)}')


def _proof(record: dict, notes: list[str]) -> str:
	"""The thread: why, past issues, evidence, what the questions settle, details, also found and
	confidence. The class, labels, escalate and the facts code read stay in the saved record."""
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
			lines.append(f'{number}. {_text(item["fact"])} (`{item["ref"]}`)')
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


def _choices(values: list) -> str:
	quoted = []
	for value in values:
		if value is not None:
			quoted.append(f'`{value}`')
	return ', '.join(quoted)


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
	return json.loads(SCHEMA_FILE.read_text(encoding='utf-8'))


@functools.cache
def _validator():
	return jsonschema.Draft202012Validator(_schema())
