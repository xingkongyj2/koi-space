import { useEffect, useState } from 'react';
import { create } from 'zustand';
import { persist } from 'zustand/middleware';

/**
 * Cycling "Working" verb shown next to the spinner while an agent is busy.
 * Inspired by jaehongpark-agent/claude-code-spinner-verbs - playful gerunds
 * that rotate every few seconds so a long-running task feels alive instead
 * of stuck on one word.
 *
 * All presets are pure data - add a new key to PRESETS and it appears in
 * Settings automatically. The "custom" key is reserved for the user's own
 * list, edited via SettingsPane.
 */

export type SpinnerPresetId =
  | 'classic'
  | 'playful'
  | 'cafe'
  | 'wizard'
  | 'lab'
  | 'cosmic'
  | 'forge'
  | 'custom';

export interface SpinnerPreset {
  label: string;
  description: string;
  verbs: ReadonlyArray<string>;
}

export const PRESETS: Readonly<Record<Exclude<SpinnerPresetId, 'custom'>, SpinnerPreset>> = {
  classic: {
    label: '经典',
    description: '只显示“处理中”。',
    verbs: ['处理中'],
  },
  playful: {
    label: '趣味',
    description: '任务运行时轮流显示轻松有趣的提示词。',
    verbs: [
      '冲泡中',
      '思索中',
      '施法中',
      '搅拌中',
      '锻造中',
      '孵化中',
      '调试中',
      '慢炖中',
      '整理中',
      '探索中',
      '构思中',
      '酝酿中',
      '梳理中',
      '过滤中',
      '描绘中',
    ],
  },
  cafe: {
    label: '咖啡馆',
    description: '像在咖啡馆制作一杯咖啡。',
    verbs: ['冲泡中', '浸泡中', '打泡中', '压萃中', '淋注中', '搅拌中', '倾注中', '压粉中', '研磨中'],
  },
  wizard: {
    label: '魔法',
    description: '充满魔法气息的提示词。',
    verbs: ['施法中', '附魔中', '占卜中', '洞察中', '召唤中', '吟诵中', '变幻中', '引导中'],
  },
  lab: {
    label: '实验室',
    description: '实验室风格的研究提示词。',
    verbs: ['校准中', '合成中', '分析中', '推演中', '计算中', '建模中', '优化中', '测量中'],
  },
  cosmic: {
    label: '宇宙',
    description: '宇宙探索风格的提示词。',
    verbs: ['环绕中', '跃迁中', '对齐中', '绘制中', '导航中', '观测中', '折射中', '漂移中'],
  },
  forge: {
    label: '锻造',
    description: '像锻造作品一样完成任务。',
    verbs: ['锻造中', '锤炼中', '回火中', '淬火中', '熔炼中', '退火中', '塑形中', '打磨中'],
  },
};

export const DEFAULT_CUSTOM_VERBS: ReadonlyArray<string> = [
  '处理中',
  '思考中',
  '酝酿中',
];

export const MIN_CYCLE_MS = 600;
export const MAX_CYCLE_MS = 8000;
export const DEFAULT_CYCLE_MS = 2200;

interface SpinnerVerbsState {
  presetId: SpinnerPresetId;
  customVerbs: string[];
  cycleMs: number;
  setPreset: (id: SpinnerPresetId) => void;
  setCustomVerbs: (verbs: string[]) => void;
  setCycleMs: (ms: number) => void;
}

export const useSpinnerVerbsStore = create<SpinnerVerbsState>()(
  persist(
    (set) => ({
      presetId: 'playful',
      customVerbs: [...DEFAULT_CUSTOM_VERBS],
      cycleMs: DEFAULT_CYCLE_MS,
      setPreset: (id) => set(() => {
        console.log('[spinnerVerbs] setPreset', { id });
        return { presetId: id };
      }),
      setCustomVerbs: (verbs) => set(() => {
        const cleaned = verbs.map((v) => v.trim()).filter(Boolean);
        console.log('[spinnerVerbs] setCustomVerbs', { count: cleaned.length });
        return { customVerbs: cleaned };
      }),
      setCycleMs: (ms) => set(() => {
        const clamped = Math.max(MIN_CYCLE_MS, Math.min(MAX_CYCLE_MS, Math.round(ms)));
        console.log('[spinnerVerbs] setCycleMs', { ms: clamped });
        return { cycleMs: clamped };
      }),
    }),
    {
      name: 'hub-spinner-verbs',
    },
  ),
);

/** Resolve the active verb list. Falls back to ['处理中'] if custom is empty. */
export function selectActiveVerbs(state: SpinnerVerbsState): ReadonlyArray<string> {
  if (state.presetId === 'custom') {
    return state.customVerbs.length > 0 ? state.customVerbs : ['处理中'];
  }
  return PRESETS[state.presetId].verbs;
}

/**
 * Hook: returns a verb from the active list, rotating every `cycleMs`.
 * Re-runs only on store changes and on the interval tick, so it's safe to
 * call from any number of `<ChatThinking>`-style indicators.
 *
 * The starting index is randomized per mount so re-opening a session doesn't
 * always show the same first word, but the rotation order is stable for the
 * lifetime of the indicator.
 */
export function useCyclingVerb(): string {
  const presetId = useSpinnerVerbsStore((s) => s.presetId);
  const customVerbs = useSpinnerVerbsStore((s) => s.customVerbs);
  const cycleMs = useSpinnerVerbsStore((s) => s.cycleMs);

  const verbs = (() => {
    if (presetId === 'custom') return customVerbs.length > 0 ? customVerbs : ['处理中'];
    return PRESETS[presetId].verbs;
  })();

  const [idx, setIdx] = useState(() => Math.floor(Math.random() * Math.max(1, verbs.length)));

  // Reset index when the active list changes (preset switch / custom edit)
  // so we don't land on a stale out-of-range slot for a frame.
  useEffect(() => {
    setIdx((prev) => (verbs.length === 0 ? 0 : prev % verbs.length));
  }, [verbs]);

  useEffect(() => {
    if (verbs.length <= 1) return;
    const id = window.setInterval(() => {
      setIdx((prev) => (prev + 1) % verbs.length);
    }, cycleMs);
    return () => window.clearInterval(id);
  }, [verbs, cycleMs]);

  return verbs[idx] ?? '处理中';
}
