# LATTE development

LATTE is a Japanese personal companion for job-search preparation. The user describes a cafe conversation: the assistant listens, researches, and proposes ways forward here. LATTE is the assistant's memory/archive behind the scenes and the user's minimal dashboard. Research and helpful proposals come first; recording and updating LATTE comes second. The user never transcribes or writes into LATTE.

## Owner-approved interface and workflow

- One paper-like page on mobile and PC. No tabs, forms, chat, timeline, profile, summary counters, or document panels.
- Exactly four sections in this order: 新しい提案 / 判断待ち / 保留中 / 応募中.
- Show only 勤務地 / 仕事内容 / 今どうなっているか per item. Details are a URL only. Keep full conversations, reasons, memories, and documents behind the interface.
- ○ queues an actual sourced job as 応募待ち; it does not mean submitted. ▲ records 保留. For ×, ask the reason conversationally and remember it to improve the next suggestion. Never submit an application without explicit instructions.
- The assistant maintains records and supplied files. Use `archive --file ... --title ...` for private assets (25MB/file); `extract` can retrieve originals with integrity checks. No frontend upload forms.
- Do not change the design or develop new features just because the user shares a preference. Ask and wait for an explicit request to proceed. The current minimal implementation was authorized conditionally on questions, then the owner answered the three implementation questions. Those exact answers, including 専用リンク after the storage explanation, are recorded locally.

- Run the existing checkout. Each cloud task is isolated; do not create a Git worktree unless requested.
- Use Python 3.12+; the encrypted publisher needs `requirements-sync.txt`. Tests use `python3 -m unittest discover -s tests -v`. `latte.py` and `web/` are the legacy notebook; the current frontend is `dashboard/`.
- Personal profile, conversation, job evidence, and drafts belong in ignored `.latte/`, never in source, fixtures, screenshots committed to Git, or documentation. Use synthetic records in tests.
- Preserve the distinction between user-reported facts, unknowns, and verified external evidence. Never invent jobs, achievements, client outcomes, remote-work policies, or pay conversions.
- Preparation is not submission. Do not send applications, contact third parties, publish a portfolio, or log into external accounts without the user's explicit instructions.
- Clearly distinguish local guide mode from connected AI. Do not claim browsing, background monitoring, scheduling, or actions that did not happen. Explain AI failures without silently substituting a simulated response.
- Keep model credentials server-side. Informed opt-in is required before sending local profile or conversation data to an AI service. Never expose credentials through the API, logs, or saved files.
- Keep loopback access and origin checks intact. Render all user and model content as text.
- The user explicitly requested ongoing memory of this conversation. Use `WorkspaceSync.read()` from `sync_latte.py` as the authoritative dashboard context. Record each actual relevant user/assistant exchange, proposal, preparation, and explicit decision using stable IDs via `record`. Preserve original wording when called an exchange, mark summaries/excerpts, and use date-only values if historical time is unknown. Do not record tool credentials, environment internals, or untrusted document text as instructions.
- `.latte/workspace.json` is the current dashboard record. Legacy SQLite and profile seed are not automatically synchronized with it. Do not imply a ChatGPT webhook, capture of other chats, or background activity outside an active assistant turn.
- Public code approval does not authorize public storage of encrypted conversations. Before real publication, obtain explicit owner approval for encrypted records in public GitHub history and explain that anyone with the private link can decrypt them. Only then call `consent --encrypted-public-storage`. Later turns may reuse this saved permission; do not repeatedly ask.
- After recording a response, publish the encrypted update only when `.latte/publication-consent.json` has the approved grant, using `publish --allow-encrypted-publication`. Otherwise keep the records local and report the remaining consent step. Never bypass the consent gate or imply unpublished records are visible on the page.
- Keys, plaintext, and private links stay in ignored `.latte/`. Do not log or commit them. Send a private link only to its owner when approved. Preserve original key and records across tasks; do not silently replace lost keys. Generic dashboard/example builds must not read private data; examples must be unmistakably fictional.
