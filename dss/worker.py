"""Run a function of this package in another app's venv, so this repo needs none of their deps.

The child imports *module*, calls *func* with JSON kwargs from stdin and prints the JSON result
on one line after a marker (anything else the app prints to stdout is ignored).

Each app's venv python and working folder are set per machine, in DSS_<APP>_PY and
DSS_<APP>_CWD (see `.env.example`).
"""

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1].resolve()
MARK = '@@dss@@'
APPS = ('cerb', 'arch')  # Cerberus (DataDHPython) and arch (its arch-master branch)
_CHILD = (
	'import importlib, json, sys; sys.path.append(sys.argv[1]); call = json.load(sys.stdin); '
	'function = getattr(importlib.import_module(call["module"]), call["func"]); '
	'result = function(**call["kwargs"]); '
	f'print("\\n{MARK}" + json.dumps(result, default=str), flush=True)'
)


def app_dir(app: str) -> str:
	"""The working folder of *app* on this machine (DSS_<APP>_CWD)."""
	return _setting(app, 'CWD')


def run_in(app: str, module: str, func: str, timeout: float = 900, **kwargs):
	"""``module.func(**kwargs)`` run by *app*'s python; its JSON result."""
	done = subprocess.run(
		[_setting(app, 'PY'), '-c', _CHILD, str(ROOT)],
		input=json.dumps({'module': module, 'func': func, 'kwargs': kwargs}),
		check=False,
		capture_output=True,
		text=True,
		encoding='utf-8',
		cwd=app_dir(app),
		timeout=timeout,
		env={**os.environ, 'PYTHONIOENCODING': 'utf-8'},
	)
	_, mark, result = done.stdout.rpartition(MARK)
	if done.returncode or not mark:
		raise RuntimeError(f'{app}: {module}.{func} failed: {done.stderr[-2000:]}')
	return json.loads(result.splitlines()[0])


def _setting(app: str, kind: str) -> str:
	"""DSS_<APP>_<KIND> for *app*, with a message naming the setting when it is missing."""
	if app not in APPS:
		raise ValueError(f'unknown app {app!r}; expected one of {", ".join(APPS)}')
	name = f'DSS_{app.upper()}_{kind}'
	if not os.environ.get(name):
		raise RuntimeError(f'{app} is not set up on this machine: set {name} (see .env.example)')
	return os.environ[name]
