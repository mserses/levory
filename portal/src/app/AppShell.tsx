import { useEffect, useId, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { Button } from '@/components/Button';
import { FOCUSABLE, trapTabKey } from '@/components/focusTrap';
import { cx } from '@/lib/cx';
import { ROLE_LABELS } from '@/lib/labels';
import { applyTheme, readTheme, saveTheme, type ThemePreference } from '@/lib/preferences';
import { useMediaQuery } from '@/lib/useMediaQuery';
import styles from './AppShell.module.css';
import { navGroupsFor } from './navigation';

/** AppShell.module.css içindeki kırılım ile aynı olmalı */
export const COMPACT_QUERY = '(max-width: 1023.98px)';

/**
 * Uygulama çerçevesi: üst bar (ürün adı, kullanıcı, tema, çıkış) + gruplu kenar çubuğu menüsü
 * + içerik alanı. 1024px altında menü, üst bardaki "Menü" butonuyla açılan bir çekmecedir
 * (odak tuzağı, Escape ile kapanma, odağın butona dönmesi, sayfa kaydırma kilidi).
 */
export function AppShell() {
  const { user, roles, logout } = useAuth();
  const location = useLocation();
  const compact = useMediaQuery(COMPACT_QUERY);
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useState<ThemePreference>(() => readTheme());
  const navId = useId();
  const groups = navGroupsFor(roles);
  const drawerOpen = compact && menuOpen;

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  // Geniş ekrana geçilince çekmece kapanır (kenar çubuğu zaten görünür)
  useEffect(() => {
    if (!compact) setMenuOpen(false);
  }, [compact]);

  // Sayfa değişince mobil menüyü kapat; odağı yeni içeriğe taşı (ekran okuyucu sayfayı duyurur,
  // klavye kullanıcısı menünün başına dönmez). İlk açılışta ve sayfa kendi öğesine (ör. bir
  // dialog ya da başlık) odak verdiyse dokunulmaz.
  const mainRef = useRef<HTMLElement>(null);
  const firstPath = useRef(true);
  useEffect(() => {
    setMenuOpen(false);
    if (firstPath.current) {
      firstPath.current = false;
      return;
    }
    const main = mainRef.current;
    const active = document.activeElement;
    const keptByPage =
      active instanceof HTMLElement && active !== document.body && main?.contains(active);
    if (main && !keptByPage) main.focus();
  }, [location.pathname]);

  // Çekmece: ilk öğeye odak, Tab tuzağı, Escape ile kapanma, kaydırma kilidi; kapanınca
  // odak (sayfa değişimiyle ana içeriğe taşınmadıysa) "Menü" butonuna döner.
  const drawerRef = useRef<HTMLDivElement>(null);
  const menuButtonRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!drawerOpen) return;
    const node = drawerRef.current;
    const menuButton = menuButtonRef.current;
    const current = node?.querySelector<HTMLElement>('[aria-current="page"]');
    (current ?? node?.querySelector<HTMLElement>(FOCUSABLE) ?? node)?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (!node) return;
      if (event.key === 'Escape') {
        event.stopPropagation();
        setMenuOpen(false);
        return;
      }
      trapTabKey(event, node);
    };
    document.addEventListener('keydown', onKeyDown);
    const { overflow } = document.body.style;
    document.body.style.overflow = 'hidden';
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      document.body.style.overflow = overflow;
      const active = document.activeElement;
      const lost = !active || active === document.body || (node?.contains(active) ?? false);
      if (lost) menuButton?.focus();
    };
  }, [drawerOpen]);

  const roleText = roles.map((r) => ROLE_LABELS[r] ?? r).join(', ') || 'Rol yok';

  const userBlock = (
    <div className={styles.user}>
      <div className={styles.userInfo}>
        <span className={styles.userName}>{user?.username}</span>
        <span className={styles.userRoles}>{roleText}</span>
      </div>
      <div className={styles.userActions}>
        <label className="visually-hidden" htmlFor={`${navId}-theme`}>
          Tema
        </label>
        <select
          id={`${navId}-theme`}
          className={styles.theme}
          value={theme}
          onChange={(e) => {
            const next = e.target.value as ThemePreference;
            saveTheme(next);
            setTheme(next);
          }}
        >
          <option value="system">Sistem teması</option>
          <option value="light">Açık</option>
          <option value="dark">Koyu</option>
        </select>
        <Button variant="secondary" size="sm" onClick={() => void logout()}>
          Çıkış
        </Button>
      </div>
    </div>
  );

  return (
    <>
      <a className={styles.skip} href="#main">
        İçeriğe geç
      </a>
      <header className={styles.header}>
        <Button
          ref={menuButtonRef}
          variant="secondary"
          size="sm"
          className={styles.menuButton}
          aria-expanded={drawerOpen}
          aria-controls={navId}
          onClick={() => {
            setMenuOpen((v) => !v);
          }}
        >
          <span className={styles.menuIcon} aria-hidden="true" />
          Menü
        </Button>
        <Link to="/" className={styles.brand}>
          <span className={styles.brandMark} aria-hidden="true">
            SZ
          </span>
          ServisZeka
        </Link>
        {!compact && userBlock}
      </header>
      <div className={styles.layout}>
        {drawerOpen && (
          <div
            className={styles.backdrop}
            data-testid="nav-backdrop"
            role="presentation"
            onMouseDown={() => {
              setMenuOpen(false);
            }}
          />
        )}
        <div
          ref={drawerRef}
          id={navId}
          className={cx(styles.sidebar, drawerOpen && styles.open)}
          role={drawerOpen ? 'dialog' : undefined}
          aria-modal={drawerOpen || undefined}
          aria-label={drawerOpen ? 'Menü' : undefined}
          tabIndex={drawerOpen ? -1 : undefined}
        >
          {compact && (
            <div className={styles.drawerHead}>
              <span className={styles.drawerTitle}>Menü</span>
              <Button
                variant="ghost"
                size="sm"
                aria-label="Menüyü kapat"
                onClick={() => {
                  setMenuOpen(false);
                }}
              >
                ✕
              </Button>
            </div>
          )}
          <nav className={styles.nav} aria-label="Ana menü">
            {groups.map((group) => (
              <div
                key={group.id}
                className={styles.group}
                role="group"
                aria-labelledby={`${navId}-${group.id}`}
              >
                <span id={`${navId}-${group.id}`} className={styles.groupTitle}>
                  {group.label}
                </span>
                <ul className={styles.navList}>
                  {group.items.map((item) => (
                    <li key={item.path}>
                      <NavLink to={item.path} className={styles.navLink}>
                        {item.label}
                      </NavLink>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
          {compact && <div className={styles.drawerFoot}>{userBlock}</div>}
        </div>
        <main id="main" ref={mainRef} className={styles.main} tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </>
  );
}
