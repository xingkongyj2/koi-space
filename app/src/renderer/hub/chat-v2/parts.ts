/**
 * chat-v2 parts model — mirrors the AI SDK `UIMessage.parts` shape so the
 * renderer is a switch over typed parts instead of the legacy "streaming
 * patcher". Backed by a `fromHlEvents` adapter; the IPC protocol is unchanged.
 */

export type ToolState =
  | 'input-streaming'
  | 'input-available'
  | 'output-available'
  | 'output-error';

export type StreamState = 'streaming' | 'done';

export interface TextPart {
  type: 'text';
  state: StreamState;
  text: string;
}

export interface ReasoningPart {
  type: 'reasoning';
  state: StreamState;
  text: string;
}

export interface ToolPart {
  type: 'tool';
  state: ToolState;
  toolCallId: string;
  toolName: string;
  input?: unknown;
  output?: string;
  errorText?: string;
  /** ms epoch; set when the tool_call event arrives */
  startedAt: number;
  /** ms epoch; set when the tool_result event arrives */
  completedAt?: number;
  /** Duration as reported by the engine (tool_result.ms). May differ slightly
   *  from completedAt - startedAt because the engine measures inside the tool. */
  durationMs?: number;
}

export interface FilePart {
  type: 'file';
  name: string;
  path: string;
  size: number;
  mime: string;
}

export interface NotifyPart {
  type: 'notify';
  level: 'info' | 'blocking';
  message: string;
}

export type MessagePart =
  | TextPart
  | ReasoningPart
  | ToolPart
  | FilePart
  | NotifyPart;

export type MessageRole = 'user' | 'assistant';
export type MessageStatus = 'streaming' | 'done' | 'error';

export interface UIMessageV2 {
  id: string;
  role: MessageRole;
  parts: MessagePart[];
  status: MessageStatus;
  createdAt: number;
}
