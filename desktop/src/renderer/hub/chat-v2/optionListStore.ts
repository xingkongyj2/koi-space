/**
 * In-memory write-through cache for the brief window between clicking
 * Choose/Confirm and the resulting user-message landing on the transcript.
 *
 * Cross-reload persistence is intentionally NOT handled here — for that, the
 * renderer derives submitted state from the transcript itself (the next
 * user-message after the picker's turn). This cache only smooths the
 * optimistic window so the picker doesn't flash back to live mode on a
 * ChatTurn unmount mid-submit. See OptionList.tsx for the transcript-derived
 * path that handles historical / reopened sessions.
 */

export interface SubmissionRecord {
  selectedIds: readonly string[];
  otherText?: readonly string[];
  otherTextByKey?: Readonly<Record<string, string>>;
}

const submissions = new Map<string, SubmissionRecord>();

export function submissionKey(sessionId: string | undefined, optionIds: string[]): string {
  return JSON.stringify({
    sessionId: sessionId ?? null,
    optionIds: [...optionIds].sort(),
  });
}

export function getSubmission(key: string): ReadonlySet<string> | null {
  const record = submissions.get(key);
  return record ? new Set(record.selectedIds) : null;
}

export function getSubmissionRecord(key: string): SubmissionRecord | null {
  return submissions.get(key) ?? null;
}

export function recordSubmission(
  key: string,
  ids: Iterable<string>,
  extra?: { otherText?: readonly string[]; otherTextByKey?: Readonly<Record<string, string>> },
): void {
  submissions.set(key, {
    selectedIds: [...ids],
    otherText: extra?.otherText ? [...extra.otherText] : undefined,
    otherTextByKey: extra?.otherTextByKey ? { ...extra.otherTextByKey } : undefined,
  });
}

/** Test helper — clears all cached submissions. Not used in app code. */
export function _resetSubmissionCacheForTests(): void {
  submissions.clear();
}
