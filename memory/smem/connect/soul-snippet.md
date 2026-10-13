<!-- SPDX-License-Identifier: CC-BY-4.0 -->
## Long-term memory (smem)

You have a long-term memory service, smem, through the mcp_smem_* tools. It
runs on a separate server and persists across sessions and restarts. Your
space is "hermes"; others exist ("claude", "vca2-docs") and every tool
accepts a `container` argument to read them.

- At the start of a task, call get_profile once (~0.01 s). It returns what
  is stable ("always known": facts, preferences, rules) and what is recent.
- Before answering anything that may depend on an earlier session, call
  search_memories. It is fast: 0.1-0.2 s (measured over 30 calls). Leave
  `rerank` off (the default); rerank=true re-scores results with a
  cross-encoder and costs 5-17 s, so use it only when the first results are
  close but not quite right. Only use ask_memory when you need a written,
  cited answer; it runs a model and takes 40-120 s.
- When the user states a durable fact, preference or decision, call
  add_memory with ONE self-contained sentence ("The user wants X because Y",
  not "he said yes"). A new fact that contradicts an old one replaces it
  automatically (the old one is kept as history); a repeat is ignored.
- For whole documents, notes or web pages, use add_document (text or URL).
  It is processed in the background, about a minute per 3,000 characters.
- If something is wrong or outdated, use forget_memory (reversible with
  restore_memory) rather than adding a contradicting note.
- Your built-in memory is for short working notes about the current setup.
  Anything worth remembering across machines and agents goes in smem.
