export const INPUT_PLACEHOLDER = 'What should the agent do?' as const;

export const STATUS_LABEL: Record<string, string> = {
  draft: '待启动',
  running: '运行中',
  stuck: '受阻',
  paused: '已暂停',
  stopped: '已停止',
  idle: '空闲',
};
