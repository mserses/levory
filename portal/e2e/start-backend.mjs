// E2E backend'i: geçici SQLite, geliştirme kipi kimlik doğrulaması ve sahte (fake) LLM.
// Playwright `webServer` tarafından başlatılır; her çalıştırma boş bir veritabanıyla başlar.
// E2E_PROFILE=faz3: Faz 3 otomasyon backend'i — gölge mod KAPALI (varsayılan eşik), sahte Entra ID /
// M365 / Teams dizini (`SZ_CONNECTOR_PROVIDER=fake`, katalog tohumlanır) ve standart grup izin listesi.
// Faz 1/2 senaryoları gölge modu açık backend'i kullanır (öneriler insan onayına düşer).
import { spawn } from 'node:child_process';
import { mkdirSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../..', import.meta.url));
const port = process.env.E2E_API_PORT ?? '8765';
const origins = process.env.E2E_PORTAL_ORIGINS ?? 'http://127.0.0.1:5180';
// Sabit (porta özgü) geçici dizin: her başlangıçta silinip yeniden kurulur. Playwright sunucuyu
// SIGKILL ile durdurabildiği için temizlik çıkışa değil başlangıca bağlıdır.
const dir = join(tmpdir(), `serviszeka-e2e-${port}`);
const faz3 = process.env.E2E_PROFILE === 'faz3';
const profileEnv = faz3
  ? {
      SZ_AI_SHADOW_MODE: 'false',
      SZ_CONNECTOR_PROVIDER: 'fake',
      SZ_IDENTITY_STANDARD_GROUPS: 'sg-yazilim-standart,sg-vpn-kullanicilari,m365-ekip-proje-x',
      SZ_IDENTITY_PRIVILEGED_GROUPS: '',
    }
  : { SZ_AI_SHADOW_MODE: 'true' };
rmSync(dir, { recursive: true, force: true });
mkdirSync(dir, { recursive: true });

const child = spawn(
  'uv',
  [
    'run',
    'uvicorn',
    'serviszeka.main:app',
    '--host',
    '127.0.0.1',
    '--port',
    port,
    '--log-level',
    'warning',
  ],
  {
    cwd: root,
    stdio: 'inherit',
    env: {
      ...process.env,
      SZ_ENVIRONMENT: 'development',
      SZ_AUTH_MODE: 'dev',
      SZ_LLM_PROVIDER: 'fake',
      SZ_ANTHROPIC_API_KEY: '',
      SZ_CORS_ORIGINS: origins,
      SZ_DATABASE_URL: `sqlite+aiosqlite:///${join(dir, 'e2e.db')}`,
      SZ_AUTO_CREATE_SCHEMA: 'true',
      ...profileEnv,
      SZ_ONCALL_WEBHOOK_URL: '',
      SZ_SERVICENOW_INSTANCE_URL: '',
      SZ_JIRA_BASE_URL: '',
    },
  },
);

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    child.kill('SIGTERM');
  });
}
child.on('exit', (code) => {
  rmSync(dir, { recursive: true, force: true });
  process.exit(code ?? 0);
});
