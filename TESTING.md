# Regression And Acceptance

All tests use temporary synthetic stores, never an installed assistant's live memory.
Do not set MEMORY_STORE to personal data when running development commands.

## Review Findings Addressed

| Finding | Implementation | Regression coverage |
| --- | --- | --- |
| purge leaves backup/archive/derived data | memory.forget_items and save | backup, archive, digest, conflict, tombstone and interrupted-delete tests |
| MCP pruning loses fragments | service.execute delegates to prune_store | fragment compaction/archive and retention tests |
| corrupt data or missing text kills MCP | domain exceptions and MCP error envelopes | invalid input, corrupt-store process and subsequent ping tests |
| adapters disagree | service.TOOLS and service.execute | task fields, fragment recall, CLI ranking and bridge catalog tests |
| installed plugin lacks Python scripts | prepack runtime whitelist | check_plugin_package.py checks hashes and boots extracted runtime |
| rules disappear within budgets | bounded rendering with mandatory rules | tiny budget, 25-rule and response-metadata budget tests |
| threads bypass process lock | RLock plus OS file lock | thread exclusion, triple nesting and 4-process/40-write tests |
| short IDs collide | UUID and legacy migration | 1000 IDs and duplicate legacy migration tests |
| single-character delete is too broad | destructive query validation | short-delete refusal test |
| no actual semantic recall | optional local FastEmbed ONNX and hybrid fusion | deterministic cache tests plus opt-in real-model test |
| facts conflict without resolution | atomic facts and provenance-based resolution | bilingual, negation, cross-layer, legacy, pending-conflict and restore tests |

This is a reproducible regression baseline, not a guarantee that arbitrary future bugs or all natural-language contradictions can be detected.

## Required Regression

From the repository root, with Python 3.10+ and Node 20+:

```sh
python -X utf8 tests/test_memory.py
python -X utf8 -m unittest discover -s tests -p test_service.py -v
node --test dsh-plugin/test/bridge.test.mjs
```

The workflow runs these tests and packaged-runtime checks on Windows, macOS and Linux.
The real-model test is opt-in and skipped in the no-dependency CI matrix.

## Real Semantic Retrieval

Install the optional dependency in an isolated environment:

```sh
python -m pip install -e ".[semantic]"
```

Set MEMORY_TEST_LOCAL_MODEL=1 and optionally MEMORY_MODEL_CACHE to a dedicated directory, then run the service tests again.
The actual multilingual model must retrieve "I live in Shanghai." for a Chinese question about the user's home city.
Once the model is cached, MEMORY_LOCAL_FILES_ONLY=1 validates offline inference.

Integration acceptance should also connect an official MCP SDK client, list 10 tools, remember that English fact, recall it in strict semantic mode, submit a confirmed Chinese correction, and verify only the new value appears in context.
For Cordis, inject the official tools service, execute a registered tool, then dispose the context and verify the Python child exits.

## Verify The Actual Plugin Package

In dsh-plugin, run npm pack. Then from the repository root:

```sh
python tests/check_plugin_package.py dsh-plugin/dsh-portable-memory-1.1.0.tgz
```

The checker enforces the exact ten-file whitelist, compares bundled Python sources to the root sources, rejects accidental data/pyc inclusion, and starts the extracted runtime with a temporary store.
Do not publish to a public registry as part of this verification.

The public repository also preserves its original root-level plugin package entry. Run `npm pack` at the repository root, then `python tests/check_plugin_package.py dsh-portable-memory-1.1.0.tgz`.
This layout has a separate thirteen-file whitelist, including the forwarding entry, shared adapter, root license and DSH patches. The checker also imports the extracted Node entry.
Both package layouts use the exact same Python core and Cordis adapter; CI verifies both.

## Trust Boundaries

- Vectors rank relevance, not truth. Automatic replacement needs explicit confirmed user evidence or stronger observed evidence against an unconfirmed agent guess.
- The host authenticates scope/provenance; arguments alone are not an ACL.
- Purge sanitizes managed files but cannot erase external backups or guarantee forensic disk erasure.
- This repository's tests and releases do not deploy a new Daisy runtime or migrate its live memory.
