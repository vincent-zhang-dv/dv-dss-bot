"""Trial mode: one triage as a checked record (`dss-triage/1`) instead of free Markdown.

Code reads the `dss_ticket` block before Claude starts. Claude follows the same steps and rules as
the bot (`triage.md`) but answers with the `triage` and `class` blocks (`triage_record.md`). Code
then checks the record against `docs/triage_record.schema.json` and renders the reviewer's DM (the
decision) and its thread reply (the proof). The design is on the DSS Triage Flow page.

The Slack bot does not use this yet. `python -m dss trial <key>` runs one by hand and saves it in
TRIAL_DIR, away from the bot's saved answers.
"""

import functools
import json
import ntpath
import re
import string
from datetime import datetime
from pathlib import Path

import jsonschema  # type: ignore[import-untyped]

from dss import cerb, jira, runner, triage
from dss.worker import ROOT

SCHEMA_ID = 'dss-triage/1'
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
{ticket}{notes}{post}"""
WORDS = {
	'not_cerberus': 'not Cerberus',
	'gate_a': 'Gate A',
	'gate_b': 'Gate B',
	'gate_c': 'Gate C',
	'it': 'IT',
}
CODE_SPAN = re.compile(r'(`[^`\n]*`)')
MARKDOWN_MARK = re.compile(r'([\\*_~<>\[\]])')  # shown as typed once escaped: \cerberus, __init__
MAX_PROBLEMS = 10  # schema problems listed in the DM
PROBLEM_CHARS = 300
RAW_CHARS = 3000  # of a refused answer, in the thread


def trial(key: str, posted: str, post: str = '', ask=None) -> dict:
	"""One trial triage of *key*, checked, rendered and saved in TRIAL_DIR.

	*posted* and *post* are as for :func:`dss.triage.triage`. *ask* defaults to
	:func:`dss.runner.run_reply` with the record system prompt (faked in tests). Returns what was
	saved: `ticket`, `notes` (what code could not read), `record` (None when the answer had no
	JSON), `problems` (empty when the record passed the check), `dm` and `thread` (Markdown),
	Claude's raw `text`, its cost, and `file`.
	"""
	if not jira.ISSUE_KEY.fullmatch(key):
		raise ValueError(f'{key!r} is not an issue key like DSS-1234')
	if ask is None:
		ask = functools.partial(runner.run_reply, system=system_prompt())
	ticket, notes = build_ticket(key, posted)
	answer = triage.run(key, posted, prompt(key, posted, ticket, notes, post), ask)
	record, problems = assemble(ticket, answer['text'])
	if record is None or problems:
		dm, thread = render_refused(key, problems, answer['text'])
	else:
		dm, thread = render(record, notes)
	usage = f'{answer["tokens"]:,} tokens · ≈ ${answer["cost_usd"]:.2f} · {answer["seconds"]} s'
	# the blank line keeps the footer out of a quoted reply above it
	dm += f'\n\n_Trial ({SCHEMA_ID}) · draft by Claude, for you only · {_link(key)} · {usage}_'
	TRIAL_DIR.mkdir(parents=True, exist_ok=True)
	path = TRIAL_DIR / f'{key}_{datetime.now().strftime(triage.TIME_FORMAT)}.json'
	result = {
		'key': key,
		'posted': posted,
		'post': post,
		'ticket': ticket,
		'notes': notes,
		'record': record,
		'problems': problems,
		'dm': dm,
		'thread': thread,
		**answer,
		'file': str(path),
	}
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


def prompt(key: str, posted: str, ticket: dict, notes: list[str], post: str = '') -> str:
	"""The trial's prompt: the bot's ticket and time, the `dss_ticket` block, and the post."""
	noted = ''
	if notes:
		noted = '\n\nCode could not read:\n- ' + '\n- '.join(notes)
	return PROMPT.format(
		key=key,
		posted=posted,
		ticket=json.dumps(ticket, indent=1),
		notes=noted,
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


def render(record: dict, notes: list[str]) -> tuple[str, str]:
	"""The DM (the decision) and its thread reply (the proof) of a checked *record*, in Markdown.

	Each field is shown once, and empty fields are left out. *notes* are what code could not read.
	Claude's text is escaped (:func:`_text`), so paths and names show as written.
	"""
	return _decision(record), _proof(record, notes)


def render_refused(key: str, problems: list[str], text: str) -> tuple[str, str]:
	"""The DM listing why the answer was refused, and a thread reply with the answer as given."""
	lines = [f'**{key} · the trial answer failed the record check**']
	for problem in problems[:MAX_PROBLEMS]:
		lines.append(f'- {_text(problem)}')
	if len(problems) > MAX_PROBLEMS:
		lines.append(f'- and {len(problems) - MAX_PROBLEMS} more')
	thread = f"**Claude's answer as given**\n```\n{text[:RAW_CHARS]}\n```"
	if len(text) > RAW_CHARS:
		thread += f'\n(cut at {RAW_CHARS:,} of {len(text):,} characters; the whole answer is saved)'
	return '\n'.join(lines), thread


def _decision(record: dict) -> str:
	ticket, answer, planning = record['dss_ticket'], record['triage'], record['class']
	head = [ticket['key'], _words(answer['status']), f'{answer["confidence"]} confidence']
	head.append(_words(answer.get('playbook') or answer['settled_at']))
	if planning.get('cause_family'):
		head.append(_words(planning['cause_family']))
	if ticket.get('toolbox'):
		head.append(ticket['toolbox'])
	lines = [f'**{" · ".join(head)}**', f'**Reported:** {_text(answer["what"])}']
	if answer.get('cause'):
		lines.append(f'**Cause:** {_text(answer["cause"])}')
	if answer.get('where'):
		lines.append(f'**Where:** {_where(answer["where"])}')
	do_now = answer.get('do_now')
	if do_now:
		lines.append(f'**Do now ({_words(do_now["who"])}):** {_text(do_now["action"])}')
	questions = answer.get('questions') or []
	if questions:
		lines.append('**Ask the analyst:**')
		for number, question in enumerate(questions, 1):
			ask, settles = _text(question['ask']), _text(question['settles'])
			lines.append(f'{number}. {ask} (settles: {settles})')
	escalate = answer.get('escalate')
	if escalate:
		kind = _words(escalate['kind'])
		if escalate.get('dtn_ticket'):
			lines.append(f'**Escalate ({kind}):** {escalate["dtn_ticket"]}')
		if escalate.get('draft'):
			lines.append(f'**Escalate ({kind}), draft to send:**\n{_quote(escalate["draft"])}')
	if answer.get('reply'):
		lines.append(f'**Suggested reply:**\n{_quote(answer["reply"])}')
	return '\n'.join(lines)


def _proof(record: dict, notes: list[str]) -> str:
	ticket, answer, planning = record['dss_ticket'], record['triage'], record['class']
	lines = []
	evidence = answer.get('evidence') or []
	if evidence:
		lines.append('**Evidence**')
		for number, item in enumerate(evidence, 1):
			lines.append(f'{number}. {_text(item["fact"])} (`{item["ref"]}`)')
	_bullets(lines, 'Not proved', answer.get('not_proved') or [])
	details = []
	for name, value in (answer.get('details') or {}).items():
		if name != 'playbook':
			details.append(f'{_words(name)}: {_value(value)}')
	_bullets(lines, 'Details', details, escape=False)  # _value escapes
	secondary = []
	for finding in answer.get('secondary') or []:
		planning = _planning(finding['class'], with_family=True)
		secondary.append(f'{_text(finding["cause"])} ({planning})')
	_bullets(lines, 'Also found', secondary, escape=False)
	_bullets(lines, 'Could not read', answer.get('flags') or [])
	similar = answer.get('similar_dss_tickets') or []
	if similar:
		lines.append(f'**Similar DSS tickets:** {", ".join(similar)}')
	lines.append(f"**Class** (Claude's proposal): {_planning(planning, with_family=False)}")
	labels = _labels(planning.get('labels') or {}, ticket)
	if labels:
		lines.append(f'**Labels to set:** {labels}')
	lines.append(f'**Facts read by code:** {_facts(ticket)}')
	_bullets(lines, 'Code could not read', notes)
	return '\n'.join(lines)


def _bullets(lines: list[str], title: str, items: list[str], escape: bool = True) -> None:
	"""*items* under *title*, escaped (:func:`_text`) unless they are rendered already."""
	if items:
		lines.append(f'**{title}**')
		for item in items:
			if escape:
				item = _text(item)
			lines.append(f'- {item}')


def _planning(planning: dict, with_family: bool) -> str:
	"""The class fields in one line; cause_family only *with_family* (the DM head shows it)."""
	parts = []
	if with_family and planning.get('cause_family'):
		parts.append(_words(planning['cause_family']))
	for name in ('feature', 'known_cause', 'impact', 'fix_type', 'prevent', 'module'):
		if planning.get(name):
			parts.append(f'{_words(name)} `{planning[name]}`')
	if planning.get('prevent_note'):
		parts.append(_text(planning['prevent_note']))
	if planning.get('dtn_tickets'):
		parts.append(', '.join(planning['dtn_tickets']))
	if not parts:
		return 'not known yet'
	return ' · '.join(parts)


def _labels(labels: dict, ticket: dict) -> str:
	"""The Jira labels Claude suggests, leaving out a Toolbox Name the DSS ticket already has."""
	parts = []
	if labels.get('toolbox') and labels['toolbox'] != ticket.get('toolbox'):
		parts.append(f'Toolbox {_text(labels["toolbox"])}')
	if labels.get('outcome'):
		parts.append(f'Outcome {_text(labels["outcome"])}')
	return ' · '.join(parts)


def _facts(ticket: dict) -> str:
	parts = [_text(ticket['reporter'])]
	if ticket.get('dvt') is not None:
		parts.append(f'DVT{ticket["dvt"]}')
	if ticket.get('project'):
		parts.append(f'`{ntpath.basename(ticket["project"])}`')
	parts.append(ticket['workflow'])
	if ticket.get('cerberus_version'):
		parts.append(f'Cerberus {ticket["cerberus_version"]}')
	if ticket.get('prod_version'):
		parts.append(f'prod {ticket["prod_version"]}')
	if ticket.get('last_error'):
		parts.append(f'last failed run: {_text(ticket["last_error"])}')
	return ' · '.join(parts)


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


def _link(key: str) -> str:
	if not jira.setting('JIRA_URL'):
		return key
	return f'[{key}]({jira.browse_url(key)})'


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
	"""The reporter's Cerberus version and last failed run, from their last session before
	*posted*, into *ticket*."""
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


@functools.cache
def _schema() -> dict:
	return json.loads(SCHEMA_FILE.read_text(encoding='utf-8'))


@functools.cache
def _validator():
	return jsonschema.Draft202012Validator(_schema())
