import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '@/auth/useAuth';
import { ErrorState } from '@/components/ErrorState';
import { Spinner } from '@/components/Spinner';

/** /auth/callback — OIDC yetkilendirme kodu dönüşü (PKCE) */
export function AuthCallbackPage() {
  const { completeLogin } = useAuth();
  const navigate = useNavigate();
  const [failed, setFailed] = useState(false);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return; // StrictMode çift çağrısında kod iki kez harcanmasın
    started.current = true;
    completeLogin()
      .then((target) => {
        navigate(target, { replace: true });
      })
      .catch(() => {
        setFailed(true);
      });
  }, [completeLogin, navigate]);

  if (failed) {
    return (
      <main style={{ padding: 'var(--space-6)' }}>
        <ErrorState
          title="Giriş tamamlanamadı"
          message="Kimlik sağlayıcıdan dönüş doğrulanamadı. Lütfen yeniden giriş yapın."
          action={<Link to="/giris">Giriş sayfasına dön</Link>}
        />
      </main>
    );
  }
  return <Spinner block label="Giriş tamamlanıyor…" showLabel />;
}
