import { useEffect, useId, useRef, useState } from 'react';
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { Button } from '@/components/Button';
import { cx } from '@/lib/cx';
import { ROLE_LABELS } from '@/lib/labels';
import { applyTheme, readTheme, saveTheme, type ThemePreference } from '@/lib/preferences';
import styles from './AppShell.module.css';
import { navItemsFor } from './navigation';

/** Uygulama çerçevesi: üst bar (rol bazlı menü, kullanıcı, tema, çıkış) + içerik alanı. */
export function AppShell() {
  const { user, roles, logout } = useAuth();
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [theme, setTheme] = useState<ThemePreference>(() => readTheme());
  const navId = useId();
  const items = navItemsFor(roles);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

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

  const roleText = roles.map((r) => ROLE_LABELS[r] ?? r).join(', ') || 'Rol yok';

  return (
    <>
      <a className={styles.skip} href="#main">
        İçeriğe geç
      </a>
      <header className={cx(styles.header, menuOpen && styles.open)}>
        <div className={styles.bar}>
          <Link to="/" className={styles.brand}>
            ServisZeka
          </Link>
          <Button
            variant="secondary"
            size="sm"
            className={styles.menuButton}
            aria-expanded={menuOpen}
            aria-controls={navId}
            onClick={() => {
              setMenuOpen((v) => !v);
            }}
          >
            Menü
          </Button>
          <nav className={styles.nav} id={navId} aria-label="Ana menü">
            <ul className={styles.navList}>
              {items.map((item) => (
                <li key={item.path}>
                  <NavLink to={item.path} className={styles.navLink}>
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </nav>
          <div className={styles.user}>
            <div className={styles.userInfo}>
              <span className={styles.userName}>{user?.username}</span>
              <span className={styles.userRoles}>{roleText}</span>
            </div>
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
            <Button variant="ghost" size="sm" onClick={() => void logout()}>
              Çıkış
            </Button>
          </div>
        </div>
      </header>
      <main id="main" ref={mainRef} className={styles.main} tabIndex={-1}>
        <Outlet />
      </main>
    </>
  );
}
