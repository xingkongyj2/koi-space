import React, { useEffect, useRef } from 'react';
import { TaskInput, type TaskInputHandle, type TaskInputSubmission } from './TaskInput';
import { useUIStore } from './state/uiStore';

interface NewSessionPageProps {
  onSubmit: (input: TaskInputSubmission) => void;
  focusRequest: number;
}

export function NewSessionPage({ onSubmit, focusRequest }: NewSessionPageProps): React.ReactElement {
  const inputRef = useRef<TaskInputHandle>(null);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      const pending = useUIStore.getState().pendingDashboardPrompt;
      if (pending) {
        inputRef.current?.setText(pending);
        useUIStore.getState().setPendingDashboardPrompt(null);
      } else {
        inputRef.current?.focus();
      }
    }, 0);
    return () => window.clearTimeout(timer);
  }, [focusRequest]);

  return (
    <section className="new-session-page" aria-label="新建会话">
      <div className="new-session-page__composer">
        <h1 className="new-session-page__title">Koi Space</h1>
        <TaskInput variant="borderless" ref={inputRef} onSubmit={onSubmit} placeholder="想让小K帮你干什么？" showAttachmentButton={false} />
      </div>
    </section>
  );
}
