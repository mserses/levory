# ServisZeka Portalı

ServisZeka'nın web arayüzü: self-servis talep, agent çalışma alanı, onay kutusu, bilgi bankası ve yönetim ekranları.
Vite + React 18 + TypeScript (strict). UI dili Türkçe.

> **Ürün kuralı:** Yapay zekâ önerir, insan onaylar. Her yapay zekâ kararı **sonuç** (`OutcomeBadge`),
> **güven** (`ConfidenceMeter`), **gerekçe** ve **bilgi bankası kaynakları** ile gösterilir; bunun hazır hâli
> `AIDecisionSummary` bileşenidir.

## Hızlı başlangıç

```bash
# 1) Backend (ayrı terminal, serviszeka/ kökünde)
SZ_ENVIRONMENT=development SZ_AUTH_MODE=dev SZ_CORS_ORIGINS=http://localhost:5173 \
  uv run uvicorn serviszeka.main:app --reload

# 2) Portal
cd portal
npm ci
cp .env.example .env.local        # VITE_AUTH_MODE=dev, VITE_API_BASE_URL=http://localhost:8000
npm run dev                       # http://localhost:5173
```

Dev kipinde `/giris` sayfasında kullanıcı adı + roller seçilir; portal `dev:<kullanıcı>:<roller>` belirteci üretir
(imzasız; yalnızca backend `SZ_ENVIRONMENT=development` + `SZ_AUTH_MODE=dev` iken çalışır).

## Komutlar

| Komut                             | Açıklama                                                                                   |
| --------------------------------- | ------------------------------------------------------------------------------------------ |
| `npm run dev`                     | Geliştirme sunucusu (5173)                                                                 |
| `npm run typecheck`               | `tsc -b` (strict, `noUncheckedIndexedAccess`)                                              |
| `npm run lint`                    | ESLint (typescript-eslint strict-type-checked, react-hooks, jsx-a11y strict), uyarı = hata |
| `npm test -- --run`               | Vitest + Testing Library + jsdom + MSW (tek sefer)                                         |
| `npm run build`                   | Tip denetimi + üretim paketi (`dist/`)                                                     |
| `npm run format` / `format:check` | Prettier                                                                                   |
| `npm run gen:openapi`             | Backend'den `openapi.json` üretir (`uv run python -m serviszeka.openapi_export`)           |
| `npm run gen:api`                 | `openapi.json` → `src/api/schema.d.ts` (openapi-typescript)                                |
| `npm run gen:taxonomy`            | Backend `taxonomy.json` → `src/lib/taxonomy.json` (bayt bayt kopya)                        |
| `npm run sync:api`                | Üçü birden. **Backend API'si değişince çalıştırın ve üretilen dosyaları commit edin.**     |
| `npm run e2e`                     | Playwright E2E (`e2e/`): gerçek API + Vite'ı kendisi başlatır                              |

Bitirmeden önce dördü de yeşil olmalı: `npm run typecheck && npm run lint && npm test -- --run && npm run build`.

## Uçtan uca testler (Playwright)

`npm run e2e` iki sunucu başlatır: `e2e/start-backend.mjs` ile API (`127.0.0.1:8765`; geçici SQLite,
`SZ_ENVIRONMENT=development`, `SZ_AUTH_MODE=dev`, `SZ_LLM_PROVIDER=fake`, CORS portal kökenine açık) ve Vite
(`127.0.0.1:5180`). Her çalıştırma boş veritabanıyla başlar; senaryolar seri koşar ve her rol ayrı tarayıcı bağlamında
`/giris` dev formuyla oturum açar. Senaryo sonunda sayfada yakalanmamış JS/konsol hatası olmadığı da doğrulanır.

| Dosya                         | Senaryo                                                                                                  |
| ----------------------------- | -------------------------------------------------------------------------------------------------------- |
| `e2e/incident-flow.spec.ts`   | Talep → Taleplerim; triage (P1 önerisi, onay gerekli) → onay → uzman P1'i görür; bayat onay → 409 mesajı |
| `e2e/knowledge-admin.spec.ts` | Makale oluştur + yayınla + ara (`<mark>` vurgusu); yönetici denetim zincirini doğrular                   |

- Tarayıcı: `@playwright/test` sürümüyle eşleşen Chromium (`npx playwright install chromium`; CI'da `--with-deps`).
- Derlenmiş imaja karşı: `E2E_WEB_URL=http://127.0.0.1:8080 npm run e2e` (Vite başlatılmaz; imaj
  `VITE_AUTH_MODE=dev`, `VITE_API_BASE_URL=http://127.0.0.1:8765` ile derlenmiş olmalı).
- Portlar: `E2E_API_PORT`, `E2E_WEB_PORT`. Hata durumunda iz/ekran görüntüsü `test-results/` altındadır.

## Docker imajı

`Dockerfile` çok aşamalıdır (Node 22 derleme → nginx, root olmayan kullanıcı, `:8080`). nginx yapılandırması
`nginx/*.template` dosyalarındadır ve açılışta `envsubst` ile doldurulur:

- SPA geri dönüşü (`try_files … /index.html`), `/assets/` için `immutable` önbellek, `index.html` için `no-cache`,
  `.map` ve gizli dosyalar `404`, `/healthz` sağlık ucu.
- Güvenlik başlıkları her yanıtta: sıkı CSP (`script-src 'self'`, `style-src 'self'`, `frame-ancestors 'none'`,
  `object-src 'none'`), `X-Content-Type-Options`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`,
  `Cross-Origin-Opener-Policy`, HSTS. Tek istisna OIDC sessiz yenileme sayfası `/auth/silent`: yalnızca aynı
  kökenden çerçevelenebilir (`frame-ancestors 'self'`, `X-Frame-Options: SAMEORIGIN`); uygulama orada kurulmaz,
  yalnızca yanıt üst pencereye iletilir. Başlıklar `scripts/check-nginx-headers.sh <adres>` ile denetlenir (CI).
- `PORTAL_API_ORIGIN` ve `PORTAL_OIDC_ORIGIN` (çalışma zamanı) CSP `connect-src` / `frame-src` / `form-action`
  listesine eklenir; `VITE_*` derleme argümanlarıdır (bkz. kök `README.md`, `docker-compose.yml`).

## Ortam değişkenleri

`src/config/env.ts` zod ile doğrular; kodda `import.meta.env` yerine `config` kullanın. `VITE_*` değerleri pakete
gömülür — **gizli bilgi koymayın**.

| Değişken              | Varsayılan              | Açıklama                                                |
| --------------------- | ----------------------- | ------------------------------------------------------- |
| `VITE_API_BASE_URL`   | `http://localhost:8000` | API kökü (backend `SZ_CORS_ORIGINS` bu kökeni içermeli) |
| `VITE_AUTH_MODE`      | `oidc`                  | `oidc` \| `dev`                                         |
| `VITE_OIDC_AUTHORITY` | —                       | OIDC yayıncısı (oidc kipinde zorunlu)                   |
| `VITE_OIDC_CLIENT_ID` | —                       | Public client (PKCE); oidc kipinde zorunlu              |
| `VITE_OIDC_SCOPE`     | `openid profile email`  |                                                         |
| `VITE_TIME_ZONE`      | `Europe/Istanbul`       | Tarih/saat gösterimi                                    |

OIDC istemcisinde yönlendirme adresleri `<origin>/auth/callback` ve `<origin>/auth/silent` (yenileme belirteci
verilmeyen IdP'lerde sessiz yenileme), çıkış dönüşü `<origin>/giris` olarak kayıtlı olmalı.

## Dizin yapısı ve sahiplik

```
src/
  app/                 Kabuk (AppShell), rota tablosu (routes.tsx), rol→menü haritası (navigation.ts), genel sayfalar
  auth/                AuthProvider, useAuth, RequireRole, roller, OIDC/dev bağdaştırıcıları
  api/                 fetch sarmalayıcısı (client.ts), ApiError, alan bazlı uç noktalar, queryKeys, üretilmiş tipler
  components/          Paylaşılan UI bileşenleri (+ testleri) — index.ts'den içe aktarın
  lib/                 format (tarih/göreli zaman/süre), labels (Türkçe enum etiketleri), fields (alan adı + değer
                       gösterimi), taxonomy (kategori/hizmet/grup adları; taxonomy.json üretilir), sla, cx, usePageTitle
  styles/              tokens.css (tasarım belirteçleri, açık/koyu), global.css
  test/                setup, MSW sunucusu/işleyicileri, fixture fabrikaları, render yardımcıları
  features/
    self-service/      /talep, /taleplerim
    incidents/         /kayitlar, /kayitlar/:id
    approvals/         /onaylar
    knowledge/         /bilgi-bankasi, /bilgi-bankasi/:id
    admin/             /yonetim/denetim, /yonetim/entegrasyonlar
```

| Rota                      | Sayfa dosyası                                 | Roller (`ROUTE_ROLES`)     | Ana uç nokta                                         |
| ------------------------- | --------------------------------------------- | -------------------------- | ---------------------------------------------------- |
| `/talep`                  | `features/self-service/NewRequestPage.tsx`    | requester, agent           | `POST /incidents`                                    |
| `/taleplerim`             | `features/self-service/MyRequestsPage.tsx`    | requester, agent, approver | `GET /incidents?mine=true`                           |
| `/kayitlar`               | `features/incidents/IncidentQueuePage.tsx`    | agent, approver            | `GET /incidents`                                     |
| `/kayitlar/:id`           | `features/incidents/IncidentDetailPage.tsx`   | agent, approver            | `GET/PATCH /incidents/{id}`, `/triage`, `/decisions` |
| `/onaylar`                | `features/approvals/ApprovalsPage.tsx`        | approver                   | `GET /incidents/decisions`, `POST …/approve`         |
| `/bilgi-bankasi`          | `features/knowledge/KnowledgeListPage.tsx`    | agent, approver            | `GET /knowledge/articles`, `/knowledge/search`       |
| `/bilgi-bankasi/:id`      | `features/knowledge/KnowledgeArticlePage.tsx` | agent, approver            | `GET /knowledge/articles/{id}`                       |
| `/yonetim/denetim`        | `features/admin/AuditPage.tsx`                | admin                      | `GET /audit`, `/audit/verify`                        |
| `/yonetim/entegrasyonlar` | `features/admin/IntegrationsPage.tsx`         | admin                      | `/integrations/*`                                    |

`admin` her rolü kapsar. Rol yetmezse `RequireRole` 403 sayfası gösterir; **asıl yetki kontrolü backend'dedir**.

### Ekran ekleyen ajanlar için kurallar

- **Yalnızca kendi `features/<alan>/` klasörünüzde çalışın.** Sayfa dosyası **varsayılan dışa aktarım** olarak
  kalmalı (rota tablosu `React.lazy` ile yükler). Alt bileşenler, hook'lar ve testler aynı klasöre:
  `features/incidents/components/…`, `features/incidents/useIncident.ts`, `features/incidents/*.test.tsx`.
- `app/routes.tsx`, `app/navigation.ts`, `components/`, `api/`, `lib/` paylaşılır: değiştirmeniz gerekirse küçük,
  geriye uyumlu ekleme yapın (yeni bileşen/yeni fonksiyon); mevcut imzaları bozmayın.
- Bir alanda yeniden kullanılabilir bir şey çıkarsa önce özellik klasöründe tutun; ikinci kullanıcı çıkınca
  `components/` veya `lib/`'e taşıyın.
- Sayfa başlığı: `usePageTitle('Kayıtlar')`; sayfa iskeleti için global `.page`, `.page-header`, `.card`, `.stack`,
  `.row`, `.muted`, `.prewrap` sınıfları.
- Sayfada bir `<h1>` olsun; bölümler `<h2>`.

## API katmanı

```ts
import { incidentsApi, decisionsApi, queryKeys, isApiError } from '@/api';

const q = useQuery({
  queryKey: queryKeys.incidents.list({ status: 'new' }),
  queryFn: ({ signal }) => incidentsApi.list({ status: 'new' }, signal),
});

const update = useMutation({
  mutationFn: (patch) => incidentsApi.update(incident.id, incident.version, patch), // If-Match: "<version>"
  onSuccess: (updated) => queryClient.setQueryData(queryKeys.incidents.detail(updated.id), updated),
  onError: (e) => {
    if (isApiError(e) && e.isConflict) {
      /* kaydı yeniden yükle, kullanıcıya "kayıt değişti" göster */
    }
  },
});
```

- `src/api/client.ts`: `api.get/post/patch/…`, `request`, `requestWithMeta` (ETag dahil). Taban adres, bearer belirteç,
  `If-Match` (`ifMatch: version`), dizi sorgu parametreleri (`outcome=a&outcome=b`) burada.
- Hatalar her zaman `ApiError`: `status` (ağ hatası `0`), Türkçe `detail` (sunucu metni ya da varsayılan),
  `fieldErrors` (422), yardımcılar `isConflict`, `needsRetriage` (bayat öneri onayı → "yeniden triage gerekli"),
  `isNotFound`, `isForbidden`, `isNetworkError`. Ekranda `errorMessage(e)` veya `<ErrorState error={e} />` kullanın.
- **401** → `AuthProvider` oturumu temizler, sorgu önbelleğini siler ve girişe yönlendirir (OIDC: IdP, dev: `/giris`).
- **409** → çağırana fırlar; otomatik yeniden deneme yoktur. Sürüm çakışmasında kaydı yeniden yükleyin;
  `needsRetriage` ise kullanıcıya "Yeniden triage başlat" seçeneği sunun.
- Tipler `src/api/types.ts`'deki kısa adlardan (`Incident`, `AIDecision`, `DecisionInboxItem`, `Priority` …).
  `schema.d.ts` üretilmiştir, **elle düzenlemeyin**.
- Sorgu anahtarları yalnızca `queryKeys`'ten; mutasyondan sonra ilgili kökü `invalidateQueries` ile tazeleyin.
  4xx hatalarında sorgular yeniden denenmez (`app/queryClient.ts`).

### Backend sözleşmesi (portal için önemli)

- `GET /me` → `{ username, roles }` (gezinme buna göre kurulur).
- `requester` rolü: `POST /incidents` (sunucu `caller`'ı kullanıcı adıyla ezer, `impact` yok sayılır — formda
  göstermeyin), yalnızca kendi kayıtlarını görür, `ai_confidence` daima `null`; kararlar/triage/SLA/KB **yok**.
  Self-servis ekranlarında yapay zekâ iç ayrıntısı göstermeyin.
- `GET /incidents?mine=true` (tüm roller), `GET /incidents/decisions?outcome=…&limit&offset` (approver/admin;
  onaylanmamış kararlar, en yeni önce, `incident_number/title/priority/status/current_version` dahil).
  `incident_version !== incident_current_version` ise onay büyük olasılıkla 409 döner — ekranda uyarın.
- P1/P2'ye yükseltme ve P1/P2'den düşürme her zaman `needs_approval`'dır.
- SLA yalnızca `awaiting_caller` / `awaiting_vendor` beklemesinde durur (`SLA_PAUSING_REASONS`, `ON_HOLD_REASON_LABELS`).

## Kimlik doğrulama

```tsx
const { user, roles, hasRole, isRequesterOnly, logout } = useAuth();
hasRole('approver'); // admin her zaman true

<RequireRole roles={['agent', 'approver']}>…</RequireRole>; // boş dizi: oturum açmış herkes
```

- OIDC: `oidc-client-ts`, Authorization Code + PKCE, kullanıcı/state **sessionStorage**'da, sessiz yenileme açık.
- Dev: belirteç sessionStorage'da (`sz.devToken`).
- **`localStorage`'a belirteç yazılmaz** (ESLint `no-restricted-globals` yasaklar; yalnızca tema tercihi
  `lib/preferences.ts` içinde saklanır).

## Paylaşılan bileşenler (`@/components`)

| Bileşen                                                    | Not                                                                                                              |
| ---------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `Button`                                                   | `variant` primary/secondary/ghost/danger, `size` sm/md, `loading`, `block`; bağlantı için `buttonClassName()`    |
| `Field`, `TextInput`, `TextArea`                           | Etiket + `hint` + `error` otomatik `aria-describedby`/`aria-invalid`; `Field` render-prop ile her denetimi sarar |
| `Select`, `optionsFrom(labels)`                            | `placeholder` boş seçenek ekler                                                                                  |
| `Badge`                                                    | `tone`: neutral/info/success/warning/danger/accent                                                               |
| `PriorityBadge`, `StatusBadge`, `OutcomeBadge`, `SlaBadge` | Türkçe etiketli; renk tek başına anlam taşımaz                                                                   |
| `ConfidenceMeter`                                          | `role="meter"`, eşik çizgisi (/me → SZ_AI_CONFIDENCE_THRESHOLD) ve eşik altı uyarısı                             |
| `AIDecisionSummary`                                        | Sonuç + güven + gerekçe (düz metin) + KB kaynak bağlantıları + `actions` yuvası                                  |
| `EmptyState`, `ErrorState`, `Spinner`                      | Boş/hata/yükleniyor durumları; `ErrorState` `ApiError`'u anlar, `onRetry`                                        |
| `Dialog`, `ConfirmDialog`                                  | Odak tuzağı, Escape, odağı geri verme; `ConfirmDialog` `alertdialog` + `busy`                                    |
| `ToastProvider`, `useToast()`                              | `toast.show({ title, description, tone })`; hata `role="alert"`                                                  |

Biçimlendirme (`@/lib/format`): `formatDateTime`, `formatDate`, `formatTime`, `formatRelative` ("5 dakika önce"),
`formatDuration` ("2 sa 5 dk"), `formatPercent` ("%87"). Enum etiketleri (`@/lib/labels`): `STATUS_LABELS`,
`PRIORITY_LABELS`, `IMPACT_LABELS`, `URGENCY_LABELS`, `KIND_LABELS`, `ON_HOLD_REASON_LABELS`, `OUTCOME_LABELS`,
`OUTCOME_DESCRIPTIONS`, `ROLE_LABELS`. Ham enum değeri ekranda gösterilmez.

## Tasarım ve erişilebilirlik

- Renk/ölçü/boşluk yalnızca `src/styles/tokens.css` değişkenleriyle (`var(--space-4)`, `var(--color-text-muted)`,
  `var(--tone-danger-fg)` …). Ham renk yazmayın. Açık/koyu tema otomatik; `html[data-theme]` ile zorlanır.
  Tüm metin/zemin çiftleri WCAG AA (≥ 4.5:1) sağlar.
- Bileşen stilleri CSS Modules (`X.module.css`); UI çerçevesi yok.
- Semantik HTML, her denetimin etiketi, klavye ile tam kullanım, görünür odak (`:focus-visible`), 375px'e kadar
  yatay kaydırmasız düzen. jsx-a11y `strict` lint'i açık.
- **Güvenlik:** sunucu/yapay zekâ metni yalnızca düz metin olarak basılır (`dangerouslySetInnerHTML` ve `innerHTML`
  ESLint ile yasak). Satır sonlarını korumak için `.prewrap` sınıfı.

## Testler

- Vitest (jsdom, `TZ=Europe/Istanbul`, `VITE_API_BASE_URL=http://api.test`, `VITE_AUTH_MODE=dev`).
- MSW: `src/test/handlers.ts` varsayılanları (`/me` dev belirtecini çözer); testte `server.use(http.get(apiUrl('/incidents'), …))`.
  Tanımsız istek testi düşürür (`onUnhandledRequest: 'error'`).
- `renderWithProviders(ui, { user: AGENT, route: '/kayitlar/1', path: '/kayitlar/:id' })` gerçek `AuthProvider` ile render
  eder (oturum `/me` ile yüklendiği için `findBy…` kullanın). Hazır kullanıcılar: `REQUESTER`, `AGENT`, `APPROVER`, `ADMIN`.
  `renderApp({ route, user })` tüm rota tablosu + kabukla render eder.
- Veri fabrikaları: `makeIncident`, `makeDecision`, `makeInboxItem`, `makeArticle` (`src/test/fixtures.ts`).
- Sorgulamada erişilebilir rolleri tercih edin (`getByRole('button', { name: … })`).
