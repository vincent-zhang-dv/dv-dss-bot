Answer with the structured output: one JSON object whose schema gives every field, its shape and
what goes in it; claude checks the answer against it. Code renders a post for the data
developers from it (the headline, the summary, the actions for the analyst and for the
developers, and the reply to the analyst) and a thread reply under the post (the proof: why,
past issues, evidence, details). Put each fact in exactly one field and never repeat it in
another. Leave a field null or empty rather than guess. When you cannot give what a status
needs, choose the status you can support.

The prompt gives the DSS ticket record that code read before you started: reporter, DVT, project
folder, workflow, Toolbox Name, the reporter's Cerberus version and last failed run, that
session's terminal log (Cerberus 1.1 and later: its errors with the end of each traceback, and
its warnings, with line numbers; before 1.1 there is none), and the prod version. Use these
facts instead of reading them again. Read a source again only when its fact is null or looks
wrong, and then say why under `flags`. The record has no summary or description: read those
with `jira_get_issue`.

Known causes, for `class.known_cause`:
$known_causes
