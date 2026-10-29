"""Score saved triage answers against the golden set: `python -m dss eval <golden> <run folder>`.

The golden set (out/eval/golden.jsonl, git-ignored: it holds client data) has one JSON row per
closed DSS ticket; docs/golden.example.jsonl is a made-up row to copy. Its fields:

- key, posted: the ticket and its time, as `python -m dss triage` takes them.
- gate: the step that should settle it: A, B, C or playbook.
- outcome: the real cause and fix, in one line.
- evidence: what the run should read, each `<tool>:<text>` or `<text>`; it was read when a traced
  call (of that tool) has the text in its arguments.
- must_not: phrases of known false leads; none may appear in the answer.
- toolbox, investigation_outcome: the labels Jira has; Suggested labels should name them.
- source_of_truth: where the outcome comes from (Jira comment, Slack thread, DTN ticket).

The run folder holds `<key>.md` (the answer) and `<key>*.jsonl` (its trace, from DSS_TRACE=1).
Every Jira key, Confluence page id and file:line under Checked is looked up, read-only. With
--judge, a locked-down Haiku also says whether the answer reached the outcome; each judgement is
one more `claude -p` run under the person's login.
"""

import json
import os
import re
import subprocess
from pathlib import Path

from dss import code, confluence, jira, runner

GATES = ('A', 'B', 'C', 'playbook')
REQUIRED = ('key', 'posted', 'gate', 'outcome')
MAX_ANSWER_CHARS = 3500  # the limit triage.md sets
SECTION = re.compile(
	r'\s*(?:#+\s*|\*\*)(triage|checked|not proved|suggested reply|suggested labels|confidence)\b',
	re.I,
)
TOOL_POINTER = re.compile(r'([a-z]+_[a-z_]+):(.+)')  # code_read:cerberus/plots.py
JIRA_KEY = re.compile(r'(?<![\w@\\-])[A-Z][A-Z0-9]{1,9}-\d+\b')  # not hosts: 8000@DV-4269
NOT_JIRA = {'UTF', 'ISO', 'SHA', 'RFC', 'PEP'}  # look like keys: UTF-8, ISO-8601
CONFLUENCE_PAGE = re.compile(r'pages/(\d+)|\bpage(?: id)?\s+(\d{5,})', re.I)
CODE_LINE = re.compile(r'([\w./\\-]+\.(?:py|json|ya?ml|toml|cfg|ini)):(\d+)')
CODE_REF = re.compile(r' at ([0-9a-f]{7,40})[,:]')  # "Cerberus at 1a2b3c4, ..." in code_* results
NOT_FOUND = ('no such ticket', 'no such page')
JUDGE_MODEL = 'claude-haiku-5-5'
JUDGE_TIMEOUT_S = 120
JUDGE_SYSTEM = (
	'You compare a support triage with the known resolution of the ticket. Answer only with JSON: '
	'{"match": true or false, "reason": "<one sentence>"}. match is true when the triage proves or '
	'clearly names the same cause as the known resolution; a lead it lists as not proved does not '
	'count.'
)


def load_golden(path: Path) -> list[dict]:
	"""The golden rows in *path*; ValueError names the first bad line."""
	rows = []
	for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), start=1):
		if not line.strip():
			continue
		try:
			row = json.loads(line)
		except json.JSONDecodeError as error:
			raise ValueError(f'{path.name} line {number}: not JSON ({error})') from error
		missing = [name for name in REQUIRED if not row.get(name)]
		if missing:
			raise ValueError(f'{path.name} line {number}: no {", ".join(missing)}')
		if row['gate'] not in GATES:
			raise ValueError(f'{path.name} line {number}: gate must be one of {", ".join(GATES)}')
		rows.append(row)
	return rows


def sections(answer: str) -> dict[str, str]:
	"""The answer's parts by lower-case name (triage, checked, ...), each with its header line."""
	found: dict[str, str] = {}
	name = ''
	for line in answer.splitlines():
		header = SECTION.match(line)
		if header:
			name = header.group(1).lower()
			found[name] = ''
		if name:
			found[name] += line + '\n'
	return found


def was_read(pointer: str, calls: list[dict]) -> bool:
	"""Whether a traced call (of the pointer's tool, if it names one) has its text in the
	arguments; case and slash direction do not matter."""
	tool = ''
	text = pointer
	qualified = TOOL_POINTER.fullmatch(pointer)
	if qualified:
		tool, text = qualified.groups()
	text = _plain(text)
	for call in calls:
		if tool and call.get('tool') != tool:
			continue
		if text in _plain(json.dumps(call.get('arguments', {}), ensure_ascii=False)):
			return True
	return False


def citations(checked: str) -> list[tuple[str, object]]:
	"""The (kind, value) pairs cited under Checked: jira keys, confluence page ids and
	(path, line) in the Cerberus code."""
	found: list[tuple[str, object]] = []
	for match in JIRA_KEY.finditer(checked):
		if match.group().split('-')[0] not in NOT_JIRA:
			found.append(('jira', match.group()))
	for match in CONFLUENCE_PAGE.finditer(checked):
		found.append(('confluence', match.group(1) or match.group(2)))
	for match in CODE_LINE.finditer(checked):
		found.append(('code', (match.group(1).replace('\\', '/'), int(match.group(2)))))
	unique = []
	for citation in found:
		if citation not in unique:
			unique.append(citation)
	return unique


class Resolver:
	"""Looks up cited Jira keys, Confluence pages and code lines; True when each exists.

	A setup or network failure raises, so a broken token never reads as a wrong citation.
	"""

	def __init__(self) -> None:
		self.paths: dict[str, list[str]] = {}

	def __call__(self, kind: str, value, ref: str) -> bool:
		try:
			if kind == 'jira':
				jira.get_issue(value)
			elif kind == 'confluence':
				confluence.page(value)
			else:
				path, line = value
				return self.code_line(path, line, ref)
		except (jira.JiraError, confluence.ConfluenceError) as error:
			if str(error).startswith(NOT_FOUND):
				return False
			raise
		return True

	def code_line(self, path: str, line: int, ref: str) -> bool:
		"""Whether one file at *ref* ends with *path* and has *line*."""
		if ref not in self.paths:
			self.paths[ref] = code.files(ref)
		matches = []
		for name in self.paths[ref]:
			if name == path or name.endswith('/' + path):
				matches.append(name)
		for name in matches:
			if code.line_count(name, ref) >= line:
				return True
		return False


def score(row: dict, answer: str, calls: list[dict], resolve, ref: str = '') -> dict:
	"""One answer against its golden *row*: evidence read, citations that resolve, false leads,
	labels and length. *resolve* is a :class:`Resolver` (faked in tests); *ref* is the Cerberus
	commit the run read, else the one its trace shows, else the prod release."""
	parts = sections(answer)
	read = {}
	for pointer in row.get('evidence', []):
		read[pointer] = was_read(pointer, calls)
	cited = citations(parts.get('checked', ''))
	if not ref and any(kind == 'code' for kind, value in cited):
		ref = ref_from(calls) or code.prod_ref()
	resolved = []
	for kind, value in cited:
		resolved.append({'kind': kind, 'value': value, 'resolves': resolve(kind, value, ref)})
	false_leads = []
	for phrase in row.get('must_not', []):
		if phrase.lower() in answer.lower():
			false_leads.append(phrase)
	labels = parts.get('suggested labels', '').lower()
	named = {}
	for field in ('toolbox', 'investigation_outcome'):
		if row.get(field):
			named[field] = row[field].lower() in labels
	return {
		'key': row['key'],
		'gate': row['gate'],
		'evidence_read': read,
		'citations': resolved,
		'false_leads': false_leads,
		'labels_named': named,
		'chars': len(answer),
		'under_limit': len(answer) <= MAX_ANSWER_CHARS,
		'sections': sorted(parts),
	}


def ref_from(calls: list[dict]) -> str:
	"""The Cerberus commit a run's code_* calls read, from their traced results."""
	for call in calls:
		if call.get('tool', '').startswith('code_'):
			found = CODE_REF.search(call.get('result', ''))
			if found:
				return found.group(1)
	return ''


def load_run(run_dir: Path, key: str) -> tuple[str, list[dict]] | None:
	"""The answer and traced calls saved for *key* in *run_dir*; None without an answer."""
	answer_file = run_dir / f'{key}.md'
	if not answer_file.exists():
		return None
	calls = []
	traces = sorted(run_dir.glob(f'{key}_*.jsonl'))
	if traces:
		for line in traces[-1].read_text(encoding='utf-8').splitlines():
			if line.strip():
				calls.append(json.loads(line))
	return answer_file.read_text(encoding='utf-8'), calls


def judge(row: dict, answer: str, ask=None) -> dict:
	"""Haiku's `match` and `reason` for whether *answer* reached the row's outcome.

	*ask* takes the prompt and returns the reply text (faked in tests); the default runs
	`claude -p` with no tools.
	"""
	if ask is None:
		ask = ask_judge
	prompt = f'Known resolution of {row["key"]}: {row["outcome"]}\n\nTriage:\n{answer}'
	text = ask(prompt)
	found = re.search(r'\{.*\}', text, re.S)
	if not found:
		return {'match': None, 'reason': f'no JSON in the judge reply: {text[:200]}'}
	try:
		verdict = json.loads(found.group())
	except json.JSONDecodeError:
		return {'match': None, 'reason': f'bad JSON in the judge reply: {text[:200]}'}
	return {'match': verdict.get('match'), 'reason': verdict.get('reason', '')}


def judge_command(model: str = JUDGE_MODEL) -> list[str]:
	"""`claude -p` for the judge: no tools, MCP servers, settings or session."""
	line = ['claude', '-p', '--output-format', 'json', '--model', model]
	line += ['--setting-sources', '', '--strict-mcp-config', '--mcp-config', '{"mcpServers": {}}']
	line += ['--tools', '', '--permission-mode', 'dontAsk', '--disable-slash-commands']
	line += ['--no-session-persistence', '--system-prompt', JUDGE_SYSTEM]
	return line


def ask_judge(prompt: str) -> str:
	"""The judge's reply text to *prompt*."""
	runner.RUN_DIR.mkdir(parents=True, exist_ok=True)
	env = {**os.environ, 'CLAUDE_CODE_DISABLE_AUTO_MEMORY': '1'}
	done = subprocess.run(
		judge_command(),
		input=prompt,
		capture_output=True,
		encoding='utf-8',
		cwd=runner.RUN_DIR,
		timeout=JUDGE_TIMEOUT_S,
		check=False,
		env=env,
	)
	if done.returncode:
		raise RuntimeError(f'judge failed ({done.returncode}): {done.stderr or done.stdout}')
	return json.loads(done.stdout).get('result', '')


def run(golden: Path, run_dir: Path, use_judge: bool = False, resolve=None, ask=None) -> list[dict]:
	"""Scores for every golden row, also written to *run_dir*/scores.jsonl."""
	if resolve is None:
		resolve = Resolver()
	scores = []
	for row in load_golden(golden):
		saved = load_run(run_dir, row['key'])
		if saved is None:
			scores.append({'key': row['key'], 'gate': row['gate'], 'missing': True})
			continue
		answer, calls = saved
		result = score(row, answer, calls, resolve)
		if use_judge:
			result['judge'] = judge(row, answer, ask)
		scores.append(result)
	lines = [json.dumps(result, ensure_ascii=False) for result in scores]
	(run_dir / 'scores.jsonl').write_text('\n'.join(lines) + '\n', encoding='utf-8')
	return scores


def summary(result: dict) -> str:
	"""One line per scored ticket, for the terminal."""
	if result.get('missing'):
		return f'{result["key"]:<10} gate {result["gate"]:<8} no answer saved'
	read = result['evidence_read']
	resolved = [citation for citation in result['citations'] if citation['resolves']]
	labels = result['labels_named']
	line = f'{result["key"]:<10} gate {result["gate"]:<8}'
	line += f' read {sum(read.values())}/{len(read)}'
	line += f' · citations {len(resolved)}/{len(result["citations"])}'
	line += f' · false leads {len(result["false_leads"])}'
	line += f' · labels {sum(labels.values())}/{len(labels)}'
	line += f' · {result["chars"]} chars'
	if 'judge' in result:
		line += f' · judge {result["judge"]["match"]}'
	return line


def _plain(text: str) -> str:
	return text.replace('\\\\', '/').replace('\\', '/').lower()
