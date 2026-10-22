"""Triage answers, one JSON file per workflow post: `<root>/<channel>_<ts>.json`.

The Slack bot saves an answer only when the triage succeeded, so on start it can tell which
tickets still need one (Slack does not resend events missed while the bot was down).
"""

import json
import os
import re
from pathlib import Path

CHANNEL = re.compile(r'[A-Z0-9]+')
TS = re.compile(r'\d+\.\d+')


class AnswerStore:
	"""Saved triage answers under *root*, keyed by the workflow post's channel and ts."""

	def __init__(self, root: Path) -> None:
		self.root = root

	def get(self, channel: str, ts: str) -> dict | None:
		"""The answer saved for the post *ts* in *channel*, or None when there is none."""
		path = self._path(channel, ts)
		if not path.exists():
			return None
		return json.loads(path.read_text(encoding='utf-8'))

	def put(self, channel: str, ts: str, answer: dict) -> None:
		"""Written whole through a temp file, so a crash never leaves half a file."""
		path = self._path(channel, ts)
		path.parent.mkdir(parents=True, exist_ok=True)
		tmp = path.with_suffix('.tmp')
		tmp.write_text(json.dumps(answer, indent=1), encoding='utf-8')
		os.replace(tmp, path)

	def _path(self, channel: str, ts: str) -> Path:
		# both come from Slack events; checking them keeps every file name inside the store root
		if not (CHANNEL.fullmatch(channel) and TS.fullmatch(ts)):
			raise ValueError(f'not a Slack channel id and message ts: {channel!r}, {ts!r}')
		return self.root / f'{channel}_{ts}.json'
