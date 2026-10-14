/**
 * OIDC sessiz yenileme (iframe) geri dönüşü. `/auth/silent` iframe içinde açılır; burada
 * React uygulaması hiç kurulmaz (gereksiz /me çağrısı, yönlendirme ya da çift kod
 * kullanımı olmasın): yalnızca yanıt üst pencereye iletilir.
 */
import type { UserManager } from 'oidc-client-ts';
import type { AppConfig } from '@/config/env';
import { SILENT_CALLBACK_PATH, createOidcUserManager } from './adapters';

type MakeManager = (
  oidc: NonNullable<AppConfig['oidc']>,
) => Pick<UserManager, 'signinSilentCallback'>;

/** Sessiz yenileme sayfasıysa yanıtı işler ve true döner (uygulama render edilmemeli). */
export async function handleSilentCallback(
  pathname: string,
  config: AppConfig,
  makeManager: MakeManager = createOidcUserManager,
): Promise<boolean> {
  if (pathname !== SILENT_CALLBACK_PATH || config.authMode !== 'oidc' || !config.oidc) {
    return false;
  }
  try {
    await makeManager(config.oidc).signinSilentCallback();
  } catch {
    // Üst pencere zaman aşımıyla hatayı görür; iframe içinde uygulama açılmaz
  }
  return true;
}
