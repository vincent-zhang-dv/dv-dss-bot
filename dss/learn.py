"""What the developers' reviews on the DSS Bot Review page teach: `python -m dss learn <export>`.

*export* is the page's database as files, one JSON document each: `<export>/reviews/DSS-<n>.json`
and `<export>/tickets/DSS-<n>.json`, as Claude Code's ArtifactData `list` with `out_dir` saves
them. Each submitted review, and each review the weekly run inferred from the developers' Slack
replies (`source` SELF, :mod:`dss.selflearn`), becomes a golden row for `dss eval` in
out/eval/golden_reviews.jsonl (git-ignored: it holds client data), and the digest says what the
reviewers corrected: the reasons by ticket count, the ones worth a fix (given on two or more
tickets, or a post marked harmful), and each ticket's corrections. Reads and writes local files
only.
"""

import json
import re
from datetime import datetime
from pathlib import Path

from dss.worker import ROOT

GOLDEN = ROOT / 'out' / 'eval' / 'golden_reviews.jsonl'
REPEATED = 2  # a reason given on this many tickets is worth a fix
SELF = 'self-learning'  # `by` and `source` of what the weekly run writes from the Slack replies
# the page's rated sections and reasons (review/dss-bot-review.html SECTIONS, TAGS, ACTS)
SECTIONS = {
	'headline': 'Headline and status',
	'summary': 'Bot Summary',
	'analyst_todos': 'Analyst Todos',
	'dev_todos': 'Data Dev Todos',
	'reply': 'Draft Reply',
	'why': 'Why',
	'past': 'Past issues',
	'evidence': 'Evidence',
	'confidence': 'Confidence',
}
TAGS = {
	'wrong_fact': 'Wrong fact',
	'wrong_cause': 'Wrong cause',
	'missed_known': 'Missed a known issue',
	'overclaimed': 'Overclaimed',
	'vague': 'Vague',
	'missing': 'Missing item',
	'unneeded': 'Repeated or unneeded',
	'internals': 'Code details for the analyst',
	'source_gap': "Bot couldn't read a source",
	'other': 'Other reason',
}
ACTS = {'as_is': 'Yes, as is', 'edits': 'Yes, after edits', 'no': 'No'}
LABEL = re.compile(r'^\*\*[^*\n]+?:?\*\*:?\s*')  # a section's own heading, such as **Why:**
MARKUP = re.compile(r'\[([^\]\n]*)\]\([^)\s]*\)|\*\*|`')


def load(export: Path) -> tuple[dict[str, dict], dict[str, dict]]:
	"""The reviews and tickets of *export*, by ticket key. A review saved under another id (the
	page's first, per-reviewer format) is left out, as the page leaves it out, and so is a ticket
	without its filing time. FileNotFoundError when *export* has no reviews folder."""
	if not (export / 'reviews').is_dir():
		raise FileNotFoundError(f'no reviews folder in {export}')
	found: tuple[dict[str, dict], dict[str, dict]] = ({}, {})
	for into, name in zip(found, ('reviews', 'tickets'), strict=True):
		for path in sorted((export / name).glob('*.json')):
			body = json.loads(path.read_text(encoding='utf-8'))
			if isinstance(body, dict) and body.get('key') == path.stem:
				into[path.stem] = body
	reviews, tickets = found
	return reviews, {
		key: ticket for key, ticket in tickets.items() if (ticket.get('filed') or {}).get('created')
	}


def plain(markdown: str) -> str:
	"""A section's Markdown as one line of plain text, without its heading."""
	text = MARKUP.sub(r'\1', LABEL.sub('', markdown.strip()))  # a link keeps its label
	return ' '.join(text.split())


def bot_text(ticket: dict, section: str) -> str:
	"""What the bot's post or thread said in *section*, as plain text."""
	for part in ticket.get('post', []) + ticket.get('thread', []):
		if part.get('id') == section and part.get('md'):
			return plain(part['md'])
	return ''


def outcome(review: dict, ticket: dict) -> str:
	"""The real cause in one line: what the reviewers say Why should say, or the bot's Why when
	they rated it Correct; failing both, the same for the headline."""
	for section in ('why', 'headline'):
		rating = review.get('sections', {}).get(section) or {}
		text = (rating.get('should') or '').strip()
		if rating.get('rating') == 'correct':
			text = bot_text(ticket, section)
		if text:
			return ' '.join(text.split())
	return ''


def posted(ticket: dict) -> str:
	"""When the DSS ticket was posted, in this PC's time, as `python -m dss triage --posted`."""
	created = datetime.fromisoformat(ticket['filed']['created'])
	return created.astimezone().strftime('%Y-%m-%d_%H-%M-%S')


def counted(review: dict) -> bool:
	"""Whether *review* teaches: a person submitted it, or the weekly run wrote it."""
	return bool(review.get('submittedAt')) or review.get('source') == SELF


def golden_row(review: dict, ticket: dict) -> dict | None:
	"""The golden row of a counted *review*; None when it gives no outcome."""
	cause = outcome(review, ticket)
	if not cause:
		return None
	if review.get('submittedAt'):
		source = f'DSS Bot Review page, submitted {review["submittedAt"][:10]}'
	else:
		source = f'{SELF} from the Slack replies, {(review.get("updatedAt") or "")[:10]}'
	return {
		'key': review['key'],
		'posted': posted(ticket),
		'outcome': cause,
		'source_of_truth': source,
	}


def reasons(rating: dict) -> list[str]:
	"""A rating's reasons as the page names them; the reviewer's own words go with 'Other'."""
	named = [TAGS.get(tag, tag) for tag in rating.get('tags') or []]
	if (rating.get('why') or '').strip():
		named.append(f'{TAGS["other"]}: {rating["why"].strip()}')
	return named


def digest(
	reviews: dict[str, dict], tickets: dict[str, dict], written: str, skipped: list[str]
) -> list[str]:
	"""What the counted reviews say, for the person or session drafting fixes."""
	submitted = {key: review for key, review in reviews.items() if counted(review)}
	counts = {'correct': 0, 'partial': 0, 'incorrect': 0}
	by_reason: dict[str, set[str]] = {}
	harmful = []
	corrections = []
	for key, review in sorted(submitted.items()):
		overall = review.get('overall') or {}
		head = f'{key} · would act on it: {ACTS.get(overall.get("act"), "not answered")}'
		if not review.get('submittedAt'):
			head += f' · {SELF}'
		if overall.get('harmful'):
			head += ' · HARMFUL'
			harmful.append(f'{key}: {(overall.get("note") or "").strip() or "no note"}')
		elif (overall.get('note') or '').strip():
			head += f' · {overall["note"].strip()}'
		lines = [head]
		for section, title in SECTIONS.items():
			rating = review.get('sections', {}).get(section) or {}
			if rating.get('rating') in counts:
				counts[rating['rating']] += 1
			if rating.get('rating') not in ('partial', 'incorrect'):
				continue
			given = reasons(rating)
			for tag in rating.get('tags') or []:
				by_reason.setdefault(TAGS.get(tag, tag), set()).add(key)
			if (rating.get('why') or '').strip():
				by_reason.setdefault(TAGS['other'], set()).add(key)
			should = ' '.join((rating.get('should') or '').split())
			lines.append(
				f'  {title} ({rating["rating"]}; {"; ".join(given) or "no reason"}): {should}'
			)
		corrections.append(lines)
	out = [
		(
			f'Reviews: {len(submitted)} submitted or self-learned of {len(reviews)}; '
			f'{len(tickets)} tickets on the page'
		),
		f'Sections: correct {counts["correct"]} · partial {counts["partial"]} · incorrect {counts["incorrect"]}',
		f'Golden rows: {written}',
	]
	if skipped:
		out.append(f'No golden row (no ticket, or no outcome given): {", ".join(skipped)}')
	ranked = sorted(((len(keys), name, keys) for name, keys in by_reason.items()), reverse=True)
	out.append('')
	out.append('Reasons, by tickets:')
	for count, name, keys in ranked:
		out.append(
			f'  {name}: {count} ticket{"" if count == 1 else "s"} ({", ".join(sorted(keys))})'
		)
	worth = [
		f'{name}: {", ".join(sorted(keys))}' for count, name, keys in ranked if count >= REPEATED
	]
	worth += [f'harmful, {entry}' for entry in harmful]
	out.append('')
	out.append(f'Worth a fix (a reason on {REPEATED}+ tickets, or a post marked harmful):')
	out += [f'  - {entry}' for entry in worth] or ['  none yet']
	out.append('')
	out.append('Corrections:')
	for lines in corrections:
		out += lines
	return out


def run(export: Path, golden: Path | None = None) -> list[str]:
	"""Write the golden rows of every counted review in *export* to *golden* (GOLDEN by
	default), replacing what it held, and return the digest."""
	golden = golden or GOLDEN
	reviews, tickets = load(export)
	rows = []
	skipped = []
	for key, review in sorted(reviews.items()):
		if not counted(review):
			continue
		row = golden_row(review, tickets[key]) if key in tickets else None
		if row is None:
			skipped.append(key)
		else:
			rows.append(row)
	golden.parent.mkdir(parents=True, exist_ok=True)
	golden.write_text(
		''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8'
	)
	return digest(reviews, tickets, f'{len(rows)} written to {golden}', skipped)
