# dv-dss-bot: DSS ticket triage

A locked-down, read-only Claude triages new DSS tickets and sends the analysis to a data
developer's Slack DM. Owners: Vincent Zhang and Annika. Read `README.md` (how it works, setup,
tools) and `docs/STATUS.md` (what was done, what's next) before non-trivial work.

## Commands
```powershell
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
uv run python -m dss triage DSS-1234 [--posted YYYY-MM-DD_HH-MM-SS]   # one real triage, no Slack
```
Windows, Python 3.11, uv. Settings come from the environment or `.env` (template:
`.env.example`). Cerberus code runs in its own venv through `dss/worker.py`; never add its
packages to this repo's dependencies.

## Rules
- **Read-only, always.** Tools send only GET requests and git reads. Adding a tool that writes
  anything (Jira, Slack channels, the project share, Cerberus) needs both owners to agree first.
  The project index login is approved for read-only use on DSS tickets only. Keep the checks that
  confine paths to the share (`cerb._inside_index`) and the repo (`code._checked`).
- **Keep the runner locked down.** `dss/runner.py` gives Claude no built-in tools, no user
  settings, only `mcp__dss__*` and `dontAsk`. Don't loosen it; `test_claude_is_locked_down` must
  keep passing.
- **Nothing reaches the channel.** Analyses go to the reviewer's DM only. Changing that is a
  decision for both owners, not a code change.
- **Prompt and tool changes:** `dss/triage.md` drives the behaviour. Test a change with
  `python -m dss triage` on a few real tickets and compare the answers before and after. Each run
  uses the person's Claude login and costs tokens, so ask before running more than a few.
- **Slack:** only one bot runs on the production Slack app, on the reviewer's PC. Never start
  `python -m dss slack` with its tokens elsewhere: Slack would split the events between the two.
  Test Slack changes with a dev app and a test channel.
- **Secrets and client data:** never commit `.env` or `out/`, and never print the values of
  secrets. Tickets and analyses contain client names and project details: keep them out of
  commits, PR descriptions and test fixtures (use made-up data such as ACME and DVT1234). Tests
  fake every external service; none may need the network.
- **Git:** work on a branch and open a pull request to `main`; the other owner reviews it. Commit
  or push only when asked, and never push to `main` directly.
- **Copied code:** `runner.py`, `worker.py`, `confluence.py`, `cerb.client_info` and the Slack
  formatting helpers were copied from dv-auto-setup-agent (Arsenii Stolbov's repo) at `b3f182e`.
  Fix them here; when a fix may apply there too, note it in `docs/STATUS.md` for the owners to
  pass on. Never edit or commit in dv-auto-setup-agent from this repo.
- **Status log:** at the end of each work session, add a dated entry to `docs/STATUS.md`: what
  changed, what was checked (tests, live runs), what is not done.
- **Style:** tabs, single quotes, 100-character lines (`pyproject.toml`); match the surrounding
  code's naming, docstrings and comment density.

## Related repos (read-only from here)
| Repo | Why |
|---|---|
| DataDHPython (`darkvisiontech/DataDHPython`) | Cerberus. The `code_*` tools read it through git at the prod release commit, and `cerb_client_info` runs in its venv. |
| dv-auto-setup-agent | Arsenii's casing intake and auto-setup project, the source of the copied code. |

arch (DataDHPython's `arch-master` branch) is not a source: Cerberus reads only the client info
and the other spreadsheets in the DVT folder. If Cerberus starts reading arch directly, add a
read-only arch tool; both owners agree first.
