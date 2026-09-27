import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { extractHostname, getFaviconUrl, isDefaultFavicon, sortDomains } from './domain-utils';
import { BrowserLogoAvatar } from './BrowserLogoAvatar';
import { userFacingIpcError } from './ipcErrors';
import { useToast } from '@/renderer/components/base/Toast';
import './CookieBrowser.css';

const MAX_VISIBLE_DOMAINS = 2000;
const SEARCH_DEBOUNCE_MS = 80;
const REFRESH_AFTER_SYNC_MS = 250;

export interface CookieBrowserProfile {
  id: string;
  directory: string;
  browserKey: string;
  browserName: string;
  name: string;
  email: string;
  avatarIcon: string;
}

export interface CookieBrowserCookie {
  name: string;
  domain: string;
  path: string;
  secure: boolean;
  httpOnly: boolean;
  expires: number | null;
  sameSite: string;
}

export interface CookieBrowserImportResult {
  profileId: string;
  browserName: string;
  profileDirectory: string;
  total: number;
  imported: number;
  failed: number;
  domains: string[];
}

export interface CookieBrowserSyncRecord {
  last_synced_at: string;
  imported: number;
  total: number;
  domain_count: number;
  new_cookies?: number;
  updated_cookies?: number;
  unchanged_cookies?: number;
  new_domain_count?: number;
  updated_domain_count?: number;
}

export interface CookieBrowserApi {
  detectProfiles: () => Promise<CookieBrowserProfile[]>;
  importCookies: (profileId: string) => Promise<CookieBrowserImportResult>;
  listCookies: () => Promise<CookieBrowserCookie[]>;
  getSyncs: () => Promise<Record<string, CookieBrowserSyncRecord>>;
}

interface Props {
  api: CookieBrowserApi;
  /** Hide the title block when the host page already has one. */
  hideHeader?: boolean;
}

function fuzzyMatch(query: string, candidate: string): boolean {
  if (!query) return true;
  const q = query.toLowerCase();
  const c = candidate.toLowerCase();
  if (c.includes(q)) return true;
  // Subsequence fallback so 'gh' matches 'github' even with typos.
  let qi = 0;
  for (let i = 0; i < c.length && qi < q.length; i++) {
    if (c[i] === q[qi]) qi++;
  }
  return qi === q.length;
}

function normalizeDomain(domain: string): string {
  return domain.startsWith('.') ? domain.slice(1) : domain;
}

function localizedSyncError(error: unknown, fallback: string): string {
  const message = userFacingIpcError(error);
  console.warn('[CookieBrowser] request failed', message);
  return /[\u4e00-\u9fff]/u.test(message) ? message : fallback;
}

function relativeTime(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return '刚刚';
  const m = Math.floor(ms / 60000);
  if (m < 1) return '刚刚';
  if (m < 60) return `${m} 分钟前`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h} 小时前`;
  const d = Math.floor(h / 24);
  if (d < 30) return `${d} 天前`;
  const mo = Math.floor(d / 30);
  if (mo < 12) return `${mo} 个月前`;
  return `${Math.floor(mo / 12)} 年前`;
}

export function CookieBrowser({ api, hideHeader }: Props): React.ReactElement {
  const toast = useToast();
  const [profiles, setProfiles] = useState<CookieBrowserProfile[]>([]);
  const [profilesLoading, setProfilesLoading] = useState(false);
  const [profilesError, setProfilesError] = useState<string | null>(null);
  const [hasLoadedProfiles, setHasLoadedProfiles] = useState(false);

  const [syncingProfile, setSyncingProfile] = useState<string | null>(null);
  const [syncRecords, setSyncRecords] = useState<Record<string, CookieBrowserSyncRecord>>({});
  const [syncError, setSyncError] = useState<string | null>(null);

  const [cookies, setCookies] = useState<CookieBrowserCookie[]>([]);
  const [cookiesLoading, setCookiesLoading] = useState(false);
  const [search, setSearch] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [defaultFaviconDomains, setDefaultFaviconDomains] = useState<Set<string>>(new Set());

  const handleFaviconLoad = useCallback((domain: string, isDefault: boolean) => {
    if (!isDefault) return;
    setDefaultFaviconDomains((prev) => {
      if (prev.has(domain)) return prev;
      const next = new Set(prev);
      next.add(domain);
      return next;
    });
  }, []);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(t);
  }, [search]);

  const refreshCookies = useCallback(async () => {
    setCookiesLoading(true);
    try {
      const list = await api.listCookies();
      setCookies(list);
    } catch (err) {
      console.error('[CookieBrowser] listCookies failed', err);
    } finally {
      setCookiesLoading(false);
    }
  }, [api]);

  const refreshProfiles = useCallback(async () => {
    setProfilesLoading(true);
    setProfilesError(null);
    try {
      const list = await api.detectProfiles();
      setProfiles(list);
      setHasLoadedProfiles(true);
      if (list.length === 0) {
        setProfilesError('未找到 Chromium 浏览器配置，请先在支持的浏览器中登录，再刷新。');
      }
    } catch (err) {
      setProfilesError(localizedSyncError(err, '无法读取浏览器配置，请检查浏览器是否已安装并重试。'));
    } finally {
      setProfilesLoading(false);
    }
  }, [api]);

  const refreshSyncs = useCallback(async () => {
    try {
      const records = await api.getSyncs();
      setSyncRecords(records ?? {});
    } catch (err) {
      console.error('[CookieBrowser] getSyncs failed', err);
    }
  }, [api]);

  // Initial load: cookies, profiles, and persisted sync history. Auto-running
  // detectProfiles avoids the "no browser profile registered" empty state on
  // every reopen — the user shouldn't have to click Detect every time.
  useEffect(() => {
    void refreshCookies();
    void refreshProfiles();
    void refreshSyncs();
  }, [refreshCookies, refreshProfiles, refreshSyncs]);

  const handleSync = useCallback(async (profileId: string) => {
    setSyncingProfile(profileId);
    setSyncError(null);
    try {
      const result = await api.importCookies(profileId);
      // Slight delay so the writes have flushed before we re-list.
      setTimeout(() => { void refreshCookies(); }, REFRESH_AFTER_SYNC_MS);
      // Pull the freshly-persisted record (timestamp + counts) from main.
      void refreshSyncs();
      const domainCount = result.domains?.length ?? 0;
      toast.show({
        variant: 'success',
        title: `已同步 ${result.imported} 条登录数据`,
        message: `${result.browserName} · ${domainCount} 个网站`,
      });
    } catch (err) {
      const message = localizedSyncError(err, '登录状态同步失败，请检查浏览器配置后重试。');
      setSyncError(message);
      toast.show({ variant: 'error', title: '登录状态同步失败', message });
    } finally {
      setSyncingProfile(null);
    }
  }, [api, refreshCookies, refreshSyncs, toast]);

  // Collapse the cookie list into one entry per unique domain. The raw list is
  // 5–10× longer (each site sets multiple cookies); the user just wants to see
  // "which sites am I logged into" — domain + favicon is enough.
  const domainGroups = useMemo(() => {
    const seen = new Set<string>();
    for (const c of cookies) {
      const d = normalizeDomain(c.domain);
      if (d) seen.add(d);
    }
    // sortDomains pushes domains whose favicon resolved to Google's default
    // placeholder to the bottom — keeps the recognizable sites on top.
    return sortDomains(Array.from(seen), defaultFaviconDomains);
  }, [cookies, defaultFaviconDomains]);

  const filteredDomains = useMemo(() => {
    if (!debouncedSearch) return domainGroups;
    return domainGroups.filter((d) => fuzzyMatch(debouncedSearch, d));
  }, [domainGroups, debouncedSearch]);

  const visibleDomains = filteredDomains.slice(0, MAX_VISIBLE_DOMAINS);
  const truncated = filteredDomains.length > MAX_VISIBLE_DOMAINS;

  return (
    <div className="cb-root">
      {!hideHeader && (
        <div className="cb-header">
          <span className="cb-title">浏览器登录状态</span>
          <p className="cb-subtitle">
            从本机 Chromium 浏览器同步登录状态，让会话直接使用已登录的网站。浏览器登录状态变化后，可以再次同步。
          </p>
        </div>
      )}

      <div className="cb-section">
        <div className="cb-section-head">
          <span className="cb-section-title">本机浏览器配置</span>
          <button
            type="button"
            className="cb-btn cb-btn--ghost"
            onClick={refreshProfiles}
            disabled={profilesLoading}
          >
            {profilesLoading ? '检测中…' : hasLoadedProfiles ? '刷新' : '检测'}
          </button>
        </div>

        {profilesError && <p className="cb-error">{profilesError}</p>}

        {profiles.length > 0 && (
          <ul className="cb-profile-list">
            {profiles.map((p) => {
              const profileId = p.id ?? p.directory;
              const isSyncing = syncingProfile === profileId;
              const record = syncRecords[profileId] ?? syncRecords[p.directory];
              const subtitle = p.email ? `${p.browserName} · ${p.email}` : p.browserName;
              return (
                <li key={profileId} className="cb-profile">
                  <BrowserLogoAvatar
                    browserKey={p.browserKey}
                    fallbackLabel={p.browserName || p.name || p.directory}
                    className="cb-browser-logo"
                  />
                  <div className="cb-profile-meta">
                    <span className="cb-profile-name">{p.name || p.directory}</span>
                    {subtitle && <span className="cb-profile-email">{subtitle}</span>}
                    {record && (
                      <span className="cb-profile-result" title={new Date(record.last_synced_at).toLocaleString()}>
                        同步于 {relativeTime(record.last_synced_at)} · {record.domain_count.toLocaleString()} 个网站
                        {typeof record.new_domain_count === 'number' && typeof record.updated_domain_count === 'number' && (
                          <>（新增 {record.new_domain_count.toLocaleString()} 个，更新 {record.updated_domain_count.toLocaleString()} 个）</>
                        )}
                      </span>
                    )}
                  </div>
                  <button
                    type="button"
                    className="cb-btn cb-btn--primary"
                    onClick={() => handleSync(profileId)}
                    disabled={isSyncing || syncingProfile !== null}
                  >
                    {isSyncing ? '同步中…' : record ? '重新同步' : '同步'}
                  </button>
                </li>
              );
            })}
          </ul>
        )}

        {syncError && <p className="cb-error">{syncError}</p>}
      </div>

      <div className="cb-section">
        <div className="cb-section-head">
          <span className="cb-section-title">会话中已同步的网站</span>
          <button
            type="button"
            className="cb-btn cb-btn--ghost"
            onClick={refreshCookies}
            disabled={cookiesLoading}
          >
            {cookiesLoading ? '读取中…' : '刷新'}
          </button>
        </div>

        <div className="cb-search">
          <svg className="cb-search__icon" width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.4" />
            <path d="M9.5 9.5l3 3" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          <input
            className="cb-search__input"
            type="text"
            placeholder="按域名搜索，例如 github"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          {search && (
            <button
              type="button"
              className="cb-search__clear"
              onClick={() => setSearch('')}
              aria-label="清除搜索"
            >
              ×
            </button>
          )}
        </div>

        <div className="cb-list" role="list">
          {cookiesLoading && cookies.length === 0 ? (
            <div className="cb-empty">正在读取登录状态…</div>
          ) : visibleDomains.length === 0 ? (
            <div className="cb-empty">
              {cookies.length === 0
                ? '暂无登录数据，请先同步本机浏览器配置。'
                : '没有匹配的网站。'}
            </div>
          ) : (
            visibleDomains.map((d) => (
              <DomainRow key={d} domain={d} onFaviconLoad={handleFaviconLoad} />
            ))
          )}
        </div>

        {truncated && (
          <p className="cb-truncated">
            共 {filteredDomains.length.toLocaleString()} 个网站，当前显示前 {MAX_VISIBLE_DOMAINS.toLocaleString()} 个。请缩小搜索范围以查看更多。
          </p>
        )}
      </div>
    </div>
  );
}

function DomainRow({
  domain,
  onFaviconLoad,
}: {
  domain: string;
  onFaviconLoad: (domain: string, isDefault: boolean) => void;
}): React.ReactElement {
  const [showFallback, setShowFallback] = useState(false);
  const hostname = extractHostname(domain);

  const handleLoad = async (event: React.SyntheticEvent<HTMLImageElement>) => {
    const img = event.currentTarget;
    const isDefault = await isDefaultFavicon(img);
    onFaviconLoad(domain, isDefault);
  };

  const handleError = () => {
    setShowFallback(true);
    onFaviconLoad(domain, true);
  };

  return (
    <div className="cb-row" role="listitem" title={domain}>
      <span className="cb-row__icon">
        {showFallback ? (
          <span className="cb-row__icon-fallback" aria-hidden="true">
            {hostname.charAt(0).toUpperCase()}
          </span>
        ) : (
          <img
            src={getFaviconUrl(domain)}
            alt=""
            width={16}
            height={16}
            onLoad={handleLoad}
            onError={handleError}
          />
        )}
      </span>
      <span className="cb-row__hostname">{hostname}</span>
    </div>
  );
}
