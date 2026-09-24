import { describe, expect, it, beforeEach } from 'vitest';
import {
  submissionKey,
  getSubmission,
  recordSubmission,
  _resetSubmissionCacheForTests,
} from '@/renderer/hub/chat-v2/optionListStore';

beforeEach(() => {
  _resetSubmissionCacheForTests();
});

describe('submissionKey', () => {
  it('is stable regardless of option-id order', () => {
    expect(submissionKey('s', ['b', 'a', 'c'])).toBe(submissionKey('s', ['c', 'a', 'b']));
  });

  it('differs across sessions even with the same options', () => {
    expect(submissionKey('s1', ['a', 'b'])).not.toBe(submissionKey('s2', ['a', 'b']));
  });

  it('differs across pickers with different option sets in the same session', () => {
    expect(submissionKey('s', ['a', 'b'])).not.toBe(submissionKey('s', ['c', 'd']));
  });

  it('handles undefined sessionId without crashing', () => {
    expect(JSON.parse(submissionKey(undefined, ['a']))).toEqual({
      sessionId: null,
      optionIds: ['a'],
    });
  });

  it('does not collide when session or option ids contain delimiters', () => {
    expect(submissionKey('s:a', ['b|c'])).not.toBe(submissionKey('s', ['a:b', 'c']));
    expect(submissionKey('s', ['a|b', 'c'])).not.toBe(submissionKey('s', ['a', 'b|c']));
  });
});

describe('getSubmission / recordSubmission', () => {
  it('returns null for unrecorded keys', () => {
    expect(getSubmission('s:a')).toBeNull();
  });

  it('round-trips selected ids', () => {
    recordSubmission('s:a', ['x', 'y']);
    const got = getSubmission('s:a');
    expect(got).not.toBeNull();
    expect(got!.has('x')).toBe(true);
    expect(got!.has('y')).toBe(true);
    expect(got!.has('z')).toBe(false);
  });

  it('overwrites prior submission for the same key', () => {
    recordSubmission('s:a', ['x']);
    recordSubmission('s:a', ['y']);
    expect(getSubmission('s:a')!.has('x')).toBe(false);
    expect(getSubmission('s:a')!.has('y')).toBe(true);
  });
});
