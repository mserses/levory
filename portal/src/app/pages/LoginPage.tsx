import { useState, type FormEvent } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { errorMessage } from '@/api/errors';
import { isValidDevUsername } from '@/auth/devToken';
import { safeAppPath } from '@/auth/returnTo';
import { ROLES, type Role } from '@/auth/roles';
import { useAuth } from '@/auth/useAuth';
import { Button } from '@/components/Button';
import { TextInput } from '@/components/Field';
import { Spinner } from '@/components/Spinner';
import { ROLE_LABELS } from '@/lib/labels';
import { homePathFor } from '../navigation';
import styles from './LoginPage.module.css';

function safeReturnTo(state: unknown): string | null {
  return safeAppPath((state as { from?: unknown } | null)?.from);
}

/** /giris — OIDC kipinde IdP'ye yönlendiren buton, dev kipinde kullanıcı/rol formu. */
export function LoginPage() {
  const auth = useAuth();
  const location = useLocation();
  const returnTo = safeReturnTo(location.state);

  if (auth.status === 'authenticated') {
    return <Navigate to={returnTo ?? homePathFor(auth.roles)} replace />;
  }

  return (
    <main className={styles.wrap}>
      <div className={`card ${styles.panel ?? ''}`}>
        <div>
          <p className={styles.brand}>ServisZeka</p>
          <h1 style={{ fontSize: 'var(--text-lg)', margin: 0 }}>Giriş yap</h1>
        </div>
        {auth.status === 'loading' ? (
          <Spinner block label="Oturum doğrulanıyor…" />
        ) : auth.mode === 'oidc' ? (
          <Button block onClick={() => void auth.login(returnTo ?? '/')}>
            Kurumsal hesabınızla giriş yapın
          </Button>
        ) : (
          <DevLoginForm returnError={auth.status === 'error' ? auth.error : null} />
        )}
      </div>
    </main>
  );
}

function DevLoginForm({ returnError }: { returnError: string | null }) {
  const { loginDev } = useAuth();
  const [username, setUsername] = useState('');
  const [roles, setRoles] = useState<Role[]>(['requester']);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const usernameError =
    username && !isValidDevUsername(username)
      ? 'Yalnızca harf, rakam ve . _ @ - kullanılabilir.'
      : null;

  const onSubmit = async (event: FormEvent) => {
    event.preventDefault();
    if (!username || usernameError) {
      setError('Geçerli bir kullanıcı adı girin.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await loginDev(username, roles);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const toggle = (role: Role) => {
    setRoles((current) =>
      current.includes(role) ? current.filter((r) => r !== role) : [...current, role],
    );
  };

  return (
    <form className="stack" onSubmit={(e) => void onSubmit(e)} noValidate>
      <p className={styles.warning}>
        Geliştirme kipi: belirteç imzasızdır (<code>dev:&lt;kullanıcı&gt;:&lt;roller&gt;</code>).
        Yalnızca yerel geliştirme içindir.
      </p>
      <TextInput
        label="Kullanıcı adı"
        name="username"
        autoComplete="username"
        required
        value={username}
        error={usernameError}
        onChange={(e) => {
          setUsername(e.target.value.trim());
        }}
      />
      <fieldset className={styles.roles}>
        <legend>Roller</legend>
        {ROLES.map((role) => (
          <label key={role} className={styles.check}>
            <input
              type="checkbox"
              checked={roles.includes(role)}
              onChange={() => {
                toggle(role);
              }}
            />
            {ROLE_LABELS[role]} <span className="muted">({role})</span>
          </label>
        ))}
      </fieldset>
      {(error ?? returnError) && (
        <p className={styles.formError} role="alert">
          {error ?? returnError}
        </p>
      )}
      <Button type="submit" block loading={busy}>
        Giriş yap
      </Button>
    </form>
  );
}
