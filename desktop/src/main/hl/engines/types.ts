/**
 * EngineAdapter — pluggable interface for agent backends. Each adapter knows
 * how to detect its runtime, build spawn args, and translate its NDJSON event
 * stream into universal HlEvent shapes.
 */

import type { WebContents } from 'electron';
import type { HlEvent } from '../../../shared/session-schemas';

// ── run-time context passed to adapters ─────────────────────────────────────

export interface SpawnContext {
  /** User prompt to feed to the agent. Adapters may wrap with seed/system text. */
  prompt: string;
  /** Full chronological session event log, including the current user input. */
  history?: HlEvent[];
  /** Absolute path to <userData>/harness/ (AGENTS.md, skills, uploads, outputs). */
  harnessDir: string;
  /** App session id (used for naming uploads/outputs dirs + env injection). */
  sessionId: string;
  /** CDP target id for the browser view the agent must drive. Passed through
   *  to the agent verbatim; the app never resolves it to anything else. */
  targetId: string;
  /** Port Electron exposes CDP on. */
  cdpPort: number;
  /** If set, ask the agent to continue a prior conversation with this id. */
  resumeSessionId?: string;
  /** List of attachment paths (relative to harnessDir) the adapter may mention in wrappedPrompt. */
  attachmentRefs: Array<{ relPath: string; mime: string; size: number }>;
}

export interface ParseContext {
  /** Incrementing iteration counter; adapter bumps this on new agent turns. */
  iter: number;
  /** In-flight tool calls keyed by engine-specific tool id for pairing with results. */
  pendingTools: Map<string, { name: string; startedAt: number; iter: number }>;
  /** Harness file paths, for harness_edited / skill_used detection. */
  harnessToolsPath: string;
  harnessSkillPath: string;
  /** Last agent-facing narrative text seen this turn; adapters that don't
   *  emit a proper summary use this as the `done.summary` so users
   *  see a meaningful sentence instead of token telemetry. */
  lastNarrative?: string;
  /** Model id reported by the engine. Adapters capture this from their
   *  init/thread-started event so cost estimation and future per-turn UI can
   *  cite the model that ran. */
  currentModel?: string;
}

/** Result of parsing one NDJSON line. */
export interface ParseResult {
  /** Zero or more HlEvents to emit downstream. */
  events: HlEvent[];
  /** Engine-reported session id, used to resume the conversation later. */
  capturedSessionId?: string;
  /** The agent signaled completion in this event; runner may early-exit wait. */
  terminalDone?: boolean;
  /** The agent signaled a hard error in this event. */
  terminalError?: string;
}

// ── probes ──────────────────────────────────────────────────────────────────

export interface InstallProbe {
  installed: boolean;
  version?: string;
  error?: string;
}

// ── adapter ─────────────────────────────────────────────────────────────────

export interface EngineAdapter {
  /** Stable identifier (stored on sessions.engine). */
  readonly id: string;
  /** Human-readable display name. */
  readonly displayName: string;
  /** Executable to spawn. */
  readonly binaryName: string;

  /** Pre-flight check run before a session starts, so a missing runtime
   *  produces an actionable error instead of a spawn failure. */
  probeInstalled(): Promise<InstallProbe>;

  // Execution
  /** Produce the argv for spawning this engine. */
  buildSpawnArgs(ctx: SpawnContext, wrappedPrompt: string): string[];
  /** Produce the env for the spawned process. */
  buildEnv(ctx: SpawnContext, baseEnv: NodeJS.ProcessEnv): NodeJS.ProcessEnv;
  /** Build the seed/wrapper prompt the CLI will receive. */
  wrapPrompt(ctx: SpawnContext): string;
  /** Optional: return a payload to write to the child's stdin instead of
   *  passing the prompt via argv. Required on Windows for any prompt with
   *  newlines, because `.cmd` shims routed through `cmd.exe /c` cannot
   *  carry a literal newline inside a quoted argument — the line break
   *  truncates the command and the prompt gets word-split. Returning a
   *  string here makes runEngine open stdin as a pipe, write the payload,
   *  and close it. Adapters that opt in must omit the prompt from
   *  `buildSpawnArgs` and tell their CLI to read stdin. */
  getStdinPayload?(ctx: SpawnContext, wrappedPrompt: string): string | undefined;
  /** Translate one NDJSON line from stdout into HlEvents. */
  parseLine(line: string, ctx: ParseContext): ParseResult;
}

// ── runEngine input ─────────────────────────────────────────────────────────

export interface EngineRunControl {
  readonly pid?: number;
  readonly canSuspend: boolean;
  pause(): { paused?: boolean; error?: string };
  resume(): { resumed?: boolean; error?: string };
  terminate(): void;
}

export interface RunEngineOptions {
  engineId: string;
  prompt: string;
  /** Persisted conversation and execution history; never a recent-turn window. */
  history?: HlEvent[];
  sessionId: string;
  webContents: WebContents;
  cdpPort: number;
  harnessDir: string;
  attachments?: Array<{ name: string; mime: string; bytes: Buffer | Uint8Array }>;
  resumeSessionId?: string;
  signal?: AbortSignal;
  onRunControl?: (control: EngineRunControl) => void;
  onEvent: (e: HlEvent) => void;
  onSessionId?: (id: string) => void;
  /** Fired when the agent reports which model this run is using. */
  onModelResolved?: (info: { model: string }) => void;
}
