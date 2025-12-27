## 00_RULES — Collaboration Contract (Domino Hub work)
- No guessing. If something isn’t explicitly in this context pack, mark it as unknown and request the missing artifact (file/log/output).
- No phantom dependencies. Only refer to dependencies shown in requirements/pyproject/Dockerfiles described here.
- Use anchored instructions when changes are requested: exact file paths, exact edits, exact commands.
- Verify against the actual repo/files/logs before recommending changes.
- Prefer smallest change that is testable/observable.

- Agent-first execution: if the agent has the capability/tools to do an action (edits, commands, starting/stopping apps/services, git add/commit/push), the agent must do it directly. Do not tell the user to perform those actions unless the agent is genuinely blocked.

- Wake-word overlay workflow: after any changes to `wake-word-pc/overlay/Content/*` that affect the overlay, automatically stop and restart `wake-word-pc/overlay/HtmlWindowsOverlay.exe` so changes take effect (do not wait for the user to do it).
