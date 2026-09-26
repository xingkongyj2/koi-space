import React, { useEffect, useState } from 'react';

interface ProcessInfo {
  pid?: number;
  label: string;
  type: string;
  component?: string;
  mb: number;
  cpuPercent?: number;
  sessionId?: string;
  engineId?: string;
  source?: string;
}

interface MemoryData {
  totalMb: number;
  totalCpuPercent?: number;
  sessions: Array<{ id: string; mb: number; cpuPercent?: number; status: string; processCount?: number }>;
  processes: ProcessInfo[];
  processCount: number;
  errors?: string[];
}

function formatGb(mb: number): string {
  if (mb < 1024) return `${Math.round(mb)} MB`;
  return `${(mb / 1024).toFixed(1)} GB`;
}

function formatCpu(cpuPercent?: number): string {
  if (cpuPercent == null) return '0%';
  return `${Math.round(cpuPercent)}%`;
}

function statusDotClass(status: string): string {
  switch (status) {
    case 'running': return 'mem__dot--running';
    case 'stuck': return 'mem__dot--stuck';
    case 'paused': return 'mem__dot--paused';
    case 'idle': return 'mem__dot--idle';
    default: return 'mem__dot--stopped';
  }
}

function processLabel(process: ProcessInfo): string {
  if (process.label.startsWith('electron:')) {
    const labels: Record<string, string> = {
      Browser: '应用主进程',
      Tab: '页面渲染进程',
      GPU: '图形处理进程',
      Utility: '辅助进程',
      Zygote: '子进程管理',
      'Sandbox helper': '沙箱辅助进程',
    };
    return labels[process.type] ?? '应用辅助进程';
  }
  if (process.label === 'harness:agent-browser') return '浏览器控制进程';
  return process.label.replace(/ child$/u, ' 子进程');
}

export function MemoryIndicatorContent(): React.ReactElement {
  const [data, setData] = useState<MemoryData | null>(null);
  const [loadError, setLoadError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const fetchOnce = async (): Promise<void> => {
      const api = window.electronAPI;
      if (!api) return;
      try {
        const next = await api.sessions.memory();
        if (!cancelled) {
          setData(next);
          setLoadError(false);
        }
      } catch (err) {
        console.warn('[MemoryIndicator] memory fetch failed', err);
        if (!cancelled) setLoadError(true);
      }
    };
    void fetchOnce();
    const interval = window.setInterval(fetchOnce, 5000);
    return () => { cancelled = true; window.clearInterval(interval); };
  }, []);

  if (!data) {
    return <div className="mem-popup mem-popup--loading">{loadError ? '资源信息暂时不可用，正在重试…' : '正在加载资源信息…'}</div>;
  }

  return (
    <div className="mem-popup">
      <div className="mem__header">
        <span className="mem__title">当前资源占用</span>
        <span className="mem__total">{formatGb(data.totalMb)} / {formatCpu(data.totalCpuPercent)}</span>
      </div>
      <div className="mem__processes">
        {(data.processes ?? []).length === 0 && <div className="mem__empty">暂无运行中的进程</div>}
        {(data.processes ?? [])
          .sort((a, b) => {
            if (a.sessionId && !b.sessionId) return -1;
            if (!a.sessionId && b.sessionId) return 1;
            return b.mb - a.mb || (b.cpuPercent ?? 0) - (a.cpuPercent ?? 0);
          })
          .map((p, i) => (
            <div key={p.pid ?? i} className="mem__session-row" title={`${p.type}${p.pid ? ` pid ${p.pid}` : ''}${p.component ? ` (${p.component})` : ''}`}>
              <span className={`mem__dot ${p.sessionId ? statusDotClass(data.sessions.find((s) => s.id === p.sessionId)?.status ?? 'stopped') : 'mem__dot--system'}`} />
              <span className="mem__session-id">{processLabel(p)}</span>
              <span className="mem__session-mb">{Math.round(p.mb)} MB / {formatCpu(p.cpuPercent)}</span>
              {p.sessionId && (
                <button
                  className="mem__kill-btn"
                  onClick={() => {
                    const api = window.electronAPI;
                    if (!api || !p.sessionId) return;
                    api.sessions.cancel(p.sessionId).catch(() => {});
                  }}
                  aria-label="停止会话"
                >
                  <svg width="10" height="10" viewBox="0 0 14 14" fill="none">
                    <path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
                  </svg>
                </button>
              )}
            </div>
          ))}
      </div>
    </div>
  );
}
