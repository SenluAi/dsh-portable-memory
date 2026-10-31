# Changelog

## 1.1.0 (2026-10-31)

### Fixed

- Privacy deletion covers active copies, archive, conflicts, derived summaries, managed backup/temp/index; interrupted deletion stays retryable and implicit reimport is blocked.
- Shared pruning compacts fragments instead of losing them through MCP; archive retention also runs when no new archive entries are added.
- Domain failures are structured errors, not SystemExit. MCP survives corrupt stores, invalid input, malformed JSON and Windows non-UTF8 console defaults.
- Thread-aware reentrant locks supplement cross-process locks. New IDs are UUIDs; missing/duplicate legacy identities are repaired and persisted.
- Input schemas, ranking, task fields, fragment retrieval and tool catalogs are shared between adapters; short destructive queries are refused.
- Context budgeting includes metadata and cannot silently remove mandatory rules.
- Archived restores respect scopes and cannot reintroduce superseded or currently contradictory facts; CLI context limits and summary TTL are honored.
- Plugin packages ship runtime source, keep one hidden Python process, report failures, support cancellation/timeouts and use Cordis effect cleanup.
- Tests no longer depend on a developer's real memory file. Added Windows/macOS/Linux regression workflow and root MIT license matching the plugin.

### Added

- Actual local multilingual ONNX vectors via optional FastEmbed; hybrid and strict semantic modes, text-free SQLite cache, model/tokenizer fingerprint invalidation and explicit lexical fallback metadata.
- Explicit optional OpenAI-compatible embeddings, HTTPS/loopback-only URLs and redirect refusal to protect authorization headers.
- Atomic fact identity, source/reference/confidence/confirmation, conservative automatic corrections, pending contradiction review and superseded history.
- Legacy fact extraction without inventing confirmation, and contradiction detection across rules/profile/preferences rather than allowing a layer change to bypass it.
- Scope-aware retrieval and modification, basic credential rejection/output redaction, and guards against stale search/index results after deletion.

### Boundaries

- Source/confirmation fields are caller claims, not authentication. The host must verify them and keep current authorization separate from memory.
- Vector similarity is not a truth oracle. Free-form text without a recognized/explicit atomic fact is not promised automatic contradiction resolution.
- Local JSON is not encrypted; purge cannot erase external or OS backups. Models/Python are not bundled in the small Cordis package.
- This changes portable-memory, not Daisy's installed runtime or existing user memory. npm registry publication is a separate operation.
