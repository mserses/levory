/**
 * Geliştirme kipi belirteci: `dev:<kullanıcı>:<rol1,rol2>`. Backend yalnızca
 * SZ_ENVIRONMENT=development + SZ_AUTH_MODE=dev iken kabul eder; imza yoktur, gizli değildir.
 */
import type { Role } from './roles';

export const DEV_TOKEN_KEY = 'sz.devToken';

const USERNAME_RE = /^[A-Za-z0-9._@-]{1,64}$/;

export function isValidDevUsername(username: string): boolean {
  return USERNAME_RE.test(username);
}

export function buildDevToken(username: string, roles: readonly Role[]): string {
  const name = username.trim();
  if (!isValidDevUsername(name)) {
    throw new Error('Kullanıcı adı yalnızca harf, rakam ve . _ @ - içerebilir.');
  }
  return `dev:${name}:${roles.join(',')}`;
}
