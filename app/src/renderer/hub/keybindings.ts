export type ActionId =
  | 'nav.down' | 'nav.up' | 'nav.top' | 'nav.bottom' | 'nav.open'
  | 'goto.dashboard' | 'goto.agents' | 'goto.settings'
  | 'search.open'
  | 'action.create' | 'action.createPane' | 'action.close' | 'action.cancel' | 'action.followUp'
  | 'scroll.halfDown' | 'scroll.halfUp'
  | 'meta.help' | 'meta.commandPalette' | 'meta.escape';

export interface KeyBinding {
  id: ActionId;
  label: string;
  keys: string[];
  category: string;
}

export const DEFAULT_KEYBINDINGS: KeyBinding[] = [
  { id: 'nav.down', label: '下一个会话', keys: ['j'], category: '会话导航' },
  { id: 'nav.up', label: '上一个会话', keys: ['k'], category: '会话导航' },
  { id: 'nav.top', label: '第一个会话', keys: ['g g'], category: '会话导航' },
  { id: 'nav.bottom', label: '最后一个会话', keys: ['G', 'Shift+G'], category: '会话导航' },
  { id: 'nav.open', label: '打开会话', keys: ['Enter'], category: '会话导航' },
  { id: 'goto.dashboard', label: '首页', keys: ['g d'], category: '页面切换' },
  { id: 'goto.agents', label: '会话页面', keys: ['g a'], category: '页面切换' },
  { id: 'goto.settings', label: '设置', keys: ['CommandOrControl+,'], category: '页面切换' },
  { id: 'search.open', label: '搜索', keys: ['/'], category: '操作' },
  { id: 'action.create', label: '新建任务', keys: [], category: '操作' },
  { id: 'action.createPane', label: '打开任务输入窗口', keys: [], category: '操作' },
  { id: 'action.close', label: '关闭会话', keys: ['x'], category: '操作' },
  { id: 'action.cancel', label: '停止会话', keys: ['Ctrl+c'], category: '操作' },
  { id: 'action.followUp', label: '追加消息', keys: ['f'], category: '操作' },
  { id: 'scroll.halfDown', label: '向下滚动', keys: ['Ctrl+d'], category: '滚动' },
  { id: 'scroll.halfUp', label: '向上滚动', keys: ['Ctrl+u'], category: '滚动' },
  { id: 'meta.help', label: '快捷键帮助', keys: [], category: '通用' },
  { id: 'meta.commandPalette', label: '命令栏', keys: [], category: '通用' },
  { id: 'meta.escape', label: '关闭浮层', keys: ['Escape'], category: '通用' },
];
