# chat-v2 — data layer only

A small, pure module that adapts the legacy `HlEvent[]` stream into an
**AI SDK Elements–shaped** `UIMessageV2[]` (typed `parts`: `text`,
`reasoning`, `tool`, `file`, `notify`).

There is **no renderer here**. The legacy components in `../chat/`
(`ChatTurn`, `ToolBlock`, `ThinkingIndicator`, …) remain the visual
source of truth. This module exists so we can:

1. Reason about messages as a list of typed parts instead of a flat
   event log with heuristic streaming patches.
2. Drop in individual [AI SDK Elements](https://elements.ai-sdk.dev)
   components surgically — e.g. `<Attachments>` for file uploads,
   `<InlineCitation>` for engine-provided sources — without rewriting
   the whole transcript.
3. Eventually move the parts model onto the IPC boundary so the
   renderer reads typed parts directly. Not done yet.

## Files

- `parts.ts` — the `UIMessageV2` / `MessagePart` discriminated union
- `fromHlEvents.ts` — pure transform: `HlEvent[]` → `UIMessageV2[]`
- `logger.ts` — verbose `[chat-v2]` logging; gated by
  `localStorage.chatv2_debug === '1'` or `process.env.VITEST`

## Important: what `thinking` means

In this codebase a `thinking` HlEvent is the **assistant's streaming
output text** (mapped from `content_block_delta.text_delta` in the
claude-code adapter, `agent_message` in codex, etc.) — NOT
chain-of-thought reasoning. The transform therefore maps `thinking`
to a `text` part.

If you later plumb true reasoning blocks through (e.g. by handling
`thinking_delta` in `claude-code/adapter.ts` and emitting a new
HlEvent type), map that to a `reasoning` part — the type is already
defined in `parts.ts` and dormant.

## Tests

`app/tests/unit/chat-v2/fromHlEvents.test.ts` — pure unit tests for
the transform contract. Run with `npx vitest run tests/unit/chat-v2/`.

## Using elements

When adding a specific AI SDK Elements component (Attachments,
InlineCitation, etc.):

1. Add the new part type to `parts.ts` (e.g. `AttachmentPart`,
   `CitationPart`).
2. Emit it from `fromHlEvents.ts` (or extend the HlEvent schema and
   the engine adapter if the data isn't there yet).
3. In the legacy renderer (`chat/ChatTurn.tsx` or a focused
   subcomponent), call `fromHlEvents` to derive parts for the turn,
   and render the new part type with the element component.

That way each integration is a small, reviewable change instead of a
parallel renderer rewrite.
