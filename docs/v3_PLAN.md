# Zenbot v3.0.0 OpenAI-Native SDK Plan

## Summary
- Reframe v3.0.0 as a Cloud Run-only, OpenAI-native rebuild: keep the current public Zenbot routes, but replace the hybrid App Engine + Cloud Run runtime and the `chat.completions.create` hot path with the current OpenAI Python SDK and the Responses API.
- Use as much of the OpenAI SDK/tooling stack as is practical without harming latency or Zenbot’s voice: Responses API, strict function calling, File Search with OpenAI vector stores, Prompt Caching, Agents SDK, Background mode, Batch API, Evals API, and Trace Grading. Realtime is prepared in v3 but voice ships next.
- Treat fine-tuning as optional, not foundational. The current repo still has unresolved curation policy and sparse prior “v3” training output, so v3 should first make the runtime better and the data pipeline reliable.
- Fold the open items from [rolling_to_do_list.md](c:/Users/David/Documents/Local_Python/zenbot/project/rolling_to_do_list.md) into v3, and finish the draft curation/documentation gaps called out in [readme_set04.md](c:/Users/David/Documents/Local_Python/zenbot/training/trainset04/readme_set04.md) before model promotion.

## OpenAI-Native Runtime
- Upgrade `zb_app` to one current OpenAI Python SDK version and remove package/version drift between pinned code and the active environment.
- Replace Chat Completions with the Responses API for all live Mumonbot text turns.
- Default live responder: `gpt-5.4-mini` for latency/cost balance. Default critic/judge: `gpt-5.4`. Use `gpt-5.4` in background mode only for slow, complex synthesis.
- Expose all internal Zenbot capabilities as strict tool schemas, then call them through the OpenAI SDK:
  - `load_case_context`
  - `search_exemplars`
  - `load_memory_summaries`
  - `load_memory_entry`
  - `save_memory_candidate`
  - `archive_session`
  - `enqueue_review`
  - `report_ui_status`
- Make every tool schema strict: `strict: true`, `additionalProperties: false`, all required fields explicit.
- Move koans, approved session exemplars, memory summaries, and maintainer reference docs into OpenAI vector stores and use File Search for model-side retrieval.
- Keep web search off for dokusan/koan dialogue. Add a separate factual helper path for modern factual questions, where OpenAI Web Search is allowed.
- Structure prompts for Prompt Caching: static persona/rules/examples/tool definitions at the front, dynamic user/session content at the end, and stable `prompt_cache_key` values by persona + case + mode.

## Agent Arrangement
- Keep the hot path single-hop: one live Mumonbot responder call, not a multi-agent chain.
- Use the OpenAI Agents SDK for non-hot-path orchestration:
  - Session Critic Agent: scores each session after reply completion.
  - Review Recommender Agent: proposes `Use` / `Alter` / `Reject` with rationale.
  - Memory Curator Agent: drafts structured memory/logbook entries and summaries.
  - Dataset Builder Agent: emits clean conversation-level and turn-level exports for training.
- Use Agents SDK traces plus Trace Grading to score tool choice, retrieval relevance, ritual correctness, verbosity discipline, and style drift.
- Add an optional Apps SDK / MCP integration track after the HTTP tool layer is stable so ChatGPT connectivity no longer depends on hand-maintained schema drift.

## Platform, Storage, and Performance
- Collapse the runtime to Cloud Run only. Remove App Engine from the production serving path.
- Use Redis/Memorystore for active session state and TTL-backed live turns.
- Keep Firestore as the canonical structured store for review state, memory records, and searchable metadata.
- Keep GCS for immutable transcript archives and generated training artifacts only.
- Sync Firestore canonical content into OpenAI vector stores asynchronously; do not block user requests on archive rebuilds or index regeneration.
- Run slow post-turn work through Background mode instead of the user-facing request.
- Use Batch API for bulk evaluation, dataset labeling, large export grading, and repository-scale embedding/indexing jobs.

## Training and Fine-Tuning
- Build `set05` as the first v3-native dataset from approved `set03a`, approved `set04`, and newly accepted sessions.
- Auto-drop test chatter, malformed rows, captured `ChatCompletion(...)` blobs, meta-AI leakage, and reviewer-noise artifacts before any export.
- Export two canonical artifacts:
  - conversation-level archive JSONL
  - turn-level supervised JSONL with short preserved context
- Gate every model or prompt change through the Evals API before promotion.
- Keep the live product prompt + retrieval first. Only retain post-training if it beats the runtime-only baseline on evals.
- Because the current docs explicitly show GPT-4.1 supports fine-tuning and distillation while the current GPT-5 mini model page shows those are not supported there, use GPT-4.1 as the post-training/distillation lane if v3 still keeps a fine-tuned style model.
- Do not block v3.0.0 launch on a new fine-tune. Ship the OpenAI-native runtime first.

## Public Interfaces, Tests, and Documentation
- Keep existing browser routes and `zb_api` route names stable for v3.0.0.
- Add typed internal contracts for `TurnRequest`, `ToolCallResult`, `ReviewRecord`, `MemoryEntry`, `TrainingExample`, and `VectorStoreDoc`.
- Generate three artifacts from one schema source of truth:
  - Responses tool definitions
  - `action_schemas.yaml`
  - optional Apps SDK / MCP descriptors
- Officially support `needs_cm_review` anywhere runtime already supports it.
- Require all of the following before cutover:
  - `pytest`, `flake8`, `mypy`, and docstring coverage all green
  - contract parity between HTTP routes, OpenAPI, and tool schemas
  - latency benchmarks for first token, file search, save/archive, and prompt-cache hit rate
  - Evals coverage for style, koan grounding, brevity, no meta leakage, and correct ritual closure
  - Trace Grading coverage for background agents
- Finish the v2 documentation/docstring freeze before sweeping v3 changes, then write v3 docs around the new OpenAI-native runtime, tool contracts, data flow, and operator runbooks.

## Assumptions and Defaults
- Default live model: `gpt-5.4-mini`.
- Default critic/judge: `gpt-5.4`.
- Default post-training lane, if still used: `gpt-4.1`.
- Default OpenAI SDK footprint for v3: Responses API, strict function calling, File Search, vector stores, Prompt Caching, Agents SDK, Background mode, Batch API, Evals API, Trace Grading.
- Default platform direction: Cloud Run only, text-first in v3, Realtime voice immediately after.
- If OpenAI File Search proves slower than acceptable for a specific lookup, preserve the same tool interface but satisfy it from app-side deterministic retrieval without changing callers.

## External References
- OpenAI Models: https://developers.openai.com/api/docs/models
- GPT-5 mini model features/tools: https://developers.openai.com/api/docs/models/gpt-5-mini
- GPT Realtime model: https://developers.openai.com/api/docs/models/gpt-realtime
- File Search: https://developers.openai.com/api/docs/guides/tools-file-search
- Retrieval / vector stores: https://developers.openai.com/api/docs/guides/retrieval
- Agents: https://developers.openai.com/api/docs/guides/agents
- Agents SDK: https://developers.openai.com/api/docs/guides/agents-sdk
- Function calling strict mode: https://developers.openai.com/api/docs/guides/function-calling
- Prompt Caching: https://developers.openai.com/api/docs/guides/prompt-caching
- Background mode: https://developers.openai.com/api/docs/guides/background
- Batch API: https://developers.openai.com/api/docs/guides/batch
- Evals: https://platform.openai.com/docs/guides/evals
- Trace Grading: https://developers.openai.com/api/docs/guides/trace-grading
- Supervised fine-tuning / distillation: https://developers.openai.com/api/docs/guides/supervised-fine-tuning#distilling-from-a-larger-model
- Apps SDK: https://developers.openai.com/apps-sdk
- Google App Engine vs Cloud Run: https://docs.cloud.google.com/appengine/migration-center/run/compare-gae-with-run
- Google Memorystore for Redis: https://docs.cloud.google.com/memorystore/docs/redis
