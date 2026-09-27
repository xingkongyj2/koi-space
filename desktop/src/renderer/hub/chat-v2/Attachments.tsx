/**
 * Attachments — composable, AI SDK Elements–shaped attachment list.
 *
 * Matches the public API from https://elements.ai-sdk.dev/components/attachments
 * so we can later swap to their package without churn:
 *
 *   <Attachments variant="grid">
 *     <Attachment>
 *       <AttachmentPreview src={...} mime={...} />
 *       <AttachmentInfo name={...} meta={...} />
 *     </Attachment>
 *   </Attachments>
 *
 * Pure presentation. No coupling to HlEvent / parts. Pass it whatever you
 * want — file_output entries, user attachments, generated artifacts.
 */

import React from 'react';
import './attachments.css';

type Variant = 'grid' | 'gallery' | 'list' | 'inline';

export function Attachments({
  variant = 'grid',
  children,
}: {
  variant?: Variant;
  children?: React.ReactNode;
}): React.ReactElement | null {
  const arr = React.Children.toArray(children).filter(Boolean);
  if (arr.length === 0) return null;
  return (
    <div
      className={`chatv2-attachments chatv2-attachments--${variant}`}
      data-testid="chatv2-attachments"
      data-variant={variant}
    >
      {children}
    </div>
  );
}

export function Attachment({
  children,
  onClick,
  onRemove,
  removeLabel,
}: {
  children?: React.ReactNode;
  onClick?: () => void;
  onRemove?: () => void;
  removeLabel?: string;
}): React.ReactElement {
  const useButton = Boolean(onClick && !onRemove);
  const Tag = useButton ? 'button' : 'div';
  const activate = (): void => {
    onClick?.();
  };
  return (
    <Tag
      className={`chatv2-attachment${onClick ? ' chatv2-attachment--clickable' : ''}${onRemove ? ' chatv2-attachment--removable' : ''}`}
      onClick={onClick ? activate : undefined}
      onKeyDown={!useButton && onClick ? (e) => {
        if (e.target !== e.currentTarget) return;
        if (e.key !== 'Enter' && e.key !== ' ') return;
        e.preventDefault();
        activate();
      } : undefined}
      role={!useButton && onClick ? 'button' : undefined}
      tabIndex={!useButton && onClick ? 0 : undefined}
      type={useButton ? 'button' : undefined}
    >
      {children}
      {onRemove && (
        <button
          type="button"
          className="chatv2-attachment__remove"
          aria-label={removeLabel ?? 'Remove attachment'}
          onClick={(e) => {
            e.stopPropagation();
            onRemove();
          }}
        >
          <svg width="10" height="10" viewBox="0 0 10 10" fill="none" aria-hidden="true">
            <path d="M2 2l6 6M8 2l-6 6" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
        </button>
      )}
    </Tag>
  );
}

export function AttachmentPreview({
  src,
  mime,
  alt,
}: {
  src?: string;
  mime?: string;
  alt?: string;
}): React.ReactElement {
  const isImage = mime?.startsWith('image/') && src;
  return (
    <div className="chatv2-attachment__preview">
      {isImage
        ? <img src={src} alt={alt ?? ''} loading="lazy" />
        : <FileIcon mime={mime} />}
    </div>
  );
}

export function AttachmentInfo({
  name,
  meta,
}: {
  name: string;
  meta?: string;
}): React.ReactElement {
  return (
    <div className="chatv2-attachment__info">
      <div className="chatv2-attachment__name" title={name}>{name}</div>
      {meta && <div className="chatv2-attachment__meta">{meta}</div>}
    </div>
  );
}

/** Convenience helper for the common "show a list of files" case so callers
 *  don't have to wire the composition by hand. */
export interface AttachmentItem {
  key: string;
  name: string;
  src?: string;
  mime?: string;
  meta?: string;
  onClick?: () => void;
  onRemove?: () => void;
}

export function AttachmentList({
  items,
  variant = 'grid',
}: {
  items: AttachmentItem[];
  variant?: Variant;
}): React.ReactElement | null {
  if (items.length === 0) return null;
  return (
    <Attachments variant={variant}>
      {items.map((it) => (
        <Attachment
          key={it.key}
          onClick={it.onClick}
          onRemove={it.onRemove}
          removeLabel={it.onRemove ? `Remove ${it.name}` : undefined}
        >
          <AttachmentPreview src={it.src} mime={it.mime} alt={it.name} />
          <AttachmentInfo name={it.name} meta={it.meta} />
        </Attachment>
      ))}
    </Attachments>
  );
}

function FileIcon({ mime }: { mime?: string }): React.ReactElement {
  // Inline SVG, no emoji, no sparkles. Stroke uses currentColor so the icon
  // adapts to text-secondary by default.
  const label = mimeLabel(mime);
  return (
    <div className="chatv2-attachment__file-icon" aria-hidden="true">
      <svg width="28" height="32" viewBox="0 0 28 32" fill="none">
        <path d="M3 2.5h13.5L25 11v18.5a1 1 0 0 1-1 1H3a1 1 0 0 1-1-1v-26a1 1 0 0 1 1-1z" stroke="currentColor" strokeWidth="1.4" />
        <path d="M16.5 2.5V11H25" stroke="currentColor" strokeWidth="1.4" />
      </svg>
      <span className="chatv2-attachment__file-ext">{label}</span>
    </div>
  );
}

function mimeLabel(mime?: string): string {
  if (!mime) return '';
  if (mime === 'application/pdf') return 'PDF';
  if (mime === 'application/json') return 'JSON';
  if (mime.startsWith('text/')) return 'TXT';
  if (mime.startsWith('video/')) return 'VID';
  if (mime.startsWith('audio/')) return 'AUD';
  const sub = mime.split('/')[1] ?? '';
  return sub.slice(0, 4).toUpperCase();
}
