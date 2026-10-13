# Faz 3 — Otomasyon: araç kayıt defteri, eylem istekleri, MCP

**Kapı:** *Standart taleplerin %50'si insansız çözülüyor*, ve bu sırada **her R2/R3 eylem hâlâ insan
onayı istiyor**. Bu belge temel katmanın (araç kaydı + tek yürütme boru hattı + eylem istekleri + MCP
sunucusu) ile hizmet kataloğu, hizmet talebi karşılaması ve Entra ID / Graph / Teams bağlayıcılarının
sözleşmesidir.

Kanıt (değişmez kapı): `tests/test_gate_no_unapproved_risk.py` bölüm 4 (`test_gate_tools_*`) — her
aracın risk sınıfı ve politikası var; her R2/R3 araç (ve koşullu riskin R2'ye çıktığı her R1 çağrı)
yapay zekâ aktörüyle en gevşek politikada (gölge kapalı, eşik 0, güven 1.0) çağrıldığında işleyici
çalışmaz; R3 tek onayla çalışmaz; talep eden kendi isteğini onaylayamaz; süresi dolan istek çalışmaz;
işleyicilere yalnızca `tools/executor.py` ve `tools/actions.py` ulaşır (statik tarama). Yeni bir riskli
araç eklerken önce `RISKY_SAMPLES`/`CONDITIONAL_RISKY`'ye başarısız bir örnek ekleyin.

## Risk sınıfları

| Sınıf | Anlam | Yapay zekâ | İnsan |
|---|---|---|---|
| **R0** | Okuma | Serbest (okuma `tool.read` denetimine yazılır) | Rolü varsa çalışır |
| **R1** | Düşük riskli yazma | `policy.evaluate` **AUTO_APPLIED** derse çalışır (gölge mod kapalı + güven ≥ eşik); öneri/onay/eskalasyon sonucu **eylem isteği** açar; REJECTED hiçbir şey yapmaz | Rolü varsa çalışır |
| **R2** | Orta | Asla çalışmaz → eylem isteği (1 onay) | Aynı: eylem isteği |
| **R3** | Kritik | Asla çalışmaz → eylem isteği (2 farklı onaylayan **ya da** CAB onaylı bağlı değişiklik + 1 onay) | Aynı |

Politika anahtarı araç adından gelir: `alan.fiil` → `policy.POLICIES[(alan, fiil)]`. R1 araçlar en fazla
`LOW` riskte otomatik, R2/R3 araçlar `max_auto_risk=None` (asla otomatik). Değerlendirmede risk sınıfı
seviyeye çevrilir: R1→LOW, R2→HIGH, R3→CRITICAL. Boru hattı ayrıca R2/R3'te politika ne derse desin
çalıştırmaz (tablo yanlışlıkla gevşetilse bile).

**Koşullu risk:** `ToolSpec.risk_fn(ctx, args)` argümanlara/kayda göre daha yüksek sınıf döndürebilir;
sonuç `max(risk_class, risk_fn)` — temel sınıfın altına asla inmez. Örnekler (bugün):
`incident.create` açılışta P1/P2 → R2; `incident.set_priority` yalnızca P3..P5 içinde yükseltme R1,
P1/P2'ye yükseltme ve her düşürme R2 (Faz 1 kuralı); `incident.set_on_hold` SLA saatini durduran neden
(`awaiting_caller`/`awaiting_vendor`) → R2. Gelecek: `identity.add_to_group` standart grupta R1,
ayrıcalıklı grupta (`IdentityConnector.is_privileged_group`) R3.

## Araç tanımı — `serviszeka.tools`

```python
from serviszeka.tools import ToolSpec, registry
from serviszeka.domain.enums import RiskClass

registry.register(ToolSpec(
    name="identity.add_to_group",            # alan.fiil_nesne (küçük harf, rakamsız)
    description="Kullanıcıyı gruba ekler.",   # Türkçe; MCP açıklamasının başına risk etiketi eklenir
    risk_class=RiskClass.R1,                  # temel sınıf
    input_model=AddToGroupArgs,               # pydantic, extra="forbid"; MCP JSON şeması buradan
    handler=add_to_group,                     # async (ctx: ToolContext, args) -> dict (JSON'a uygun)
    roles=frozenset({"agent"}),               # insan çağıranlar için gereken rollerden biri
    risk_fn=privileged_group_risk,            # isteğe bağlı: (ctx, args) -> RiskClass (async olabilir)
    connectors=("identity",),                 # yapılandırılmamışsa araç 503 (ToolUnavailableError)
    links={"incident_id": "incident"},        # argüman -> eylem isteği bağı (incident/change/request)
))
```

- `RESERVED_ARGS` (`dry_run`, `idempotency_key`, `reason`, `confidence`) argüman adı olamaz: çağıran
  zarfıdır. R0 araçta `risk_fn` olamaz. Kayıt `validate_definition()` ile denetlenir.
- **İşleyici kuralları:** iş kuralını yeniden yazmaz, mevcut servisi `ctx.actor` ile çağırır (P1/P2
  onayı, CAB, `ai:` aktörün değişiklik geçişi yapamaması gibi kurallar serviste aynen geçerli kalır);
  flush eder, commit etmez; alan hatasını yükseltir (boru hattı çevirir). LLM gerekiyorsa
  `ctx.get_llm()` (yalnızca `serviszeka.llm`). Bildirim `notifications.stage` ile (commit sonrası).
- `ToolContext`: `session`, `actor` (doğrudan çağrıda çağıran; eylem isteğinde **son onaylayan insan**),
  `settings`, `now`, `llm`, `action_request`.

### Tek yürütme boru hattı

```python
result = await execute_tool(
    session, actor, "incident.assign", {"incident_id": ..., "assignment_group": "network_ops"},
    dry_run=False, idempotency_key="k-1", reason="...", confidence=0.9,
    links={"change_id": ...},       # isteğe bağlı ek bağ
    settings=None, llm=None, now=None,
)
await session.commit()             # commit ve notifications.send_pending çağıranın işi
```

1. **Aktör:** `ai_agent` rolü → `ai:<ad>` aktörü, rolleri yalnızca `{ai_agent}` (`auth.principal`).
   Yapay zekâ: `ai_agent` + `spec.ai_allowed`; insan: `spec.roles`'tan biri (admin hepsi).
2. **Doğrulama:** pydantic; hata → `ToolValidationError` (422), hiçbir şey yazılmaz. Bağlı olay /
   değişiklik yoksa `ToolNotFoundError`.
3. **Idempotency** (R1-R3, anahtar verilirse; anahtar aktöre özeldir): aynı anahtar + aynı araç ve
   kanonik argüman → önceki sonuç (`replayed=true`, yeni istek/yazma yok); farklı argüman →
   `IdempotencyConflictError` (409). Anahtar işlemden önce `tool_invocations`'a ayrılır (tekil kısıt):
   eşzamanlı aynı anahtar ikinci kez yürütmez. `dry_run` anahtarı tüketmez; R0 anahtarı yok sayar.
4. **Risk** çözülür, sınıfa göre dağıtılır (tablo yukarıda). Yapay zekânın R1-R3 çağrısı her zaman
   `policy.evaluate` → `AIDecision` (`agent=alan`, `action=fiil`, `output.tool`, `action_request_id`)
   → `audit.record("ai_decision.tool")` zincirinden geçer. Bu kararlar olay karar kutusunda
   (`/incidents/decisions`) görünmez; onay `/actions`'tadır.
5. **İşleyici** SAVEPOINT içinde çalışır: hata → kısmi yazmalar ve bekleyen bildirimler geri alınır,
   sonuç `failed` (istisna değil; `tool.failed` denetimi kalır).

`ToolResult.to_dict()` (MCP gövdesi): `executed` → `{status, tool, risk_class, result}`;
`pending_approval` → `{"status": "pending_approval", "action_request_id", tool, risk_class, message}`;
`rejected` / `failed` → `error {code, message}`; `dry_run` → `result {would: execute|pending_approval|
rejected, risk_class, required_approvals, policy_outcome?, args}` (hiçbir şey yazılmaz). Ön koşul
hataları `ToolError` (`code`: `not_found` 404, `forbidden` 403, `validation_error` 422,
`idempotency_conflict`/`conflict` 409, `unavailable` 503).

Denetim eylemleri: `tool.read` (yapay zekâ R0), `tool.execute` / `tool.failed`, `ai_decision.tool`,
`action_request.create|approve|reject|execute|failed|expire|risk_raised`.

## Eylem istekleri — `serviszeka.tools.actions`, `ActionRequest`

Alanlar: `number` (`ACT0000001`), `tool`, `args` (ham; **yalnızca yürütmede kullanılır, API'de
dönmez**), `args_display` (maskeli: PII `mask_pii`, gizli görünen anahtarlar `***`; gizli bilgi argüman
olmamalı), `args_hash`, `risk_class`, `state` (`pending` / `approved` (geçici) / `rejected` /
`executed` / `failed` / `expired`), `required_approvals`, `requested_by` (`ai:<ajan>` ya da kullanıcı),
`reason`, `confidence`, `incident_id` / `change_id` / `request_id`, `decision_id`, `result`, `error`,
`executed_by`, `executed_at`, `expires_at`, `version`. Onaylar `ActionApproval` (istek başına kişi başına
tek karar; `approver_key` casefold ile tekil).

| Fonksiyon | Açıklama |
|---|---|
| `approve(session, id, user, comment="", *, now=, settings=, llm=)` | Onay kaydeder; kural sağlandıysa onaylayan aktörle hemen yürütür |
| `reject(session, id, user, comment="", *, now=)` | Tek red → `rejected` |
| `expire_due(session, now=)` | Süresi dolan bekleyenleri `expired` yapar |
| `get_action`, `list_actions(session, state=, tool=, requested_by=, limit=, offset=)`, `list_approvals`, `is_satisfied` | |

Kurallar: yapay zekâ karar veremez; talep eden kendi isteğine karar veremez (`changes.same_identity`,
büyük/küçük harf ve boşluk duyarsız); karar verebilen `approver`/`admin`, R3 istek değişikliğe bağlıysa
`change_manager`. R1/R2 tek onay; R3 iki farklı onaylayan **ya da** bağlı değişiklik CAB onaylı (normal /
acil, onay durumu yeterli, `scheduled`/`implement`) + tek onay. Süre `SZ_ACTION_REQUEST_TTL_HOURS`
(varsayılan 24); dolan istek `ActionExpiredError` (409) ile `expired` olur, artık onaylanamaz.
**Yürütme anında** argümanlar yeniden doğrulanır (bozuksa `failed`), risk yeniden çözülür
(`max(saklı, güncel)`; arttıysa yeni sınıfın onay kuralı aranır, yetmiyorsa istek beklemede kalır),
bağlayıcılar denetlenir; işleyici onaylayan insanın kimliği ve rolleriyle çalışır — alan servisinin kendi
yetki kuralları geçerli olduğundan değişiklik geçişi içeren araçları (`change.submit_for_cab`,
`change.record_pir`) yürütebilmek için onaylayanın `change_manager` rolü de olmalıdır (yoksa `failed`).
Bağlı yapay zekâ kararı (`decision_id`) onayla `approved_by` alır.

HTTP (`/actions`, `ActionApproverUser` = approver / change_manager / admin):
`GET /actions?state=&tool=&limit=&offset=` (önce `expire_due`), `GET /actions/{id}`,
`POST /actions/{id}/approve {comment}` (yürütür, commit sonrası bildirimleri gönderir),
`POST /actions/{id}/reject {comment}`. Hatalar: 404, 403 (talep eden / yapay zekâ / rol), 409
(karar beklemiyor, süresi doldu, aynı kişi ikinci kez). Yanıt `ActionRequestRead`: `args_display`,
`tool_description`, `approvals[]`; ham `args` yok.

## MCP sunucusu — `serviszeka.mcp_server`, `/mcp`

- Resmi Python MCP SDK (`mcp` 2.x, düşük seviye `mcp.server.Server`), **streamable HTTP** (durumsuz,
  JSON yanıt), FastAPI uygulamasına `/mcp` yolu olarak eklenir; oturum yöneticisi uygulama yaşam
  döngüsünde (`gateway.run()`) çalışır. Hem 2026-07-28 (modern) hem el sıkışmalı (legacy) istemciler.
- **Kimlik:** `Authorization: Bearer <JWT>` REST ile aynı yoldan (`auth.authenticate_token`: OIDC JWKS
  ya da geliştirmede `dev:` belirteci); eksik/geçersiz → 401 `WWW-Authenticate: Bearer`. Yapay zekâ
  ajanları **hizmet sorumlusudur** (client credentials) ve `ai_agent` uygulama rolünü taşır; aktör
  `ai:<azp|appid|client_id>` olur (Entra ID: uygulama rolü `ai_agent` + `azp`/`appid`; Keycloak: hizmet
  hesabı rolü + `azp`). Bu belirteçteki diğer roller yok sayılır; REST insan uçları 403 döner.
- `tools/list`: çağıranın kullanabileceği araçlar; açıklama risk etiketiyle başlar
  (`[R2 · her zaman insan onayı gerekir] ...`), `inputSchema` pydantic şeması + R1-R3'te zarf alanları
  (`dry_run`, `idempotency_key`, `reason`, `confidence`), `annotations` (R0 `readOnlyHint`, R2/R3
  `destructiveHint`), `_meta["serviszeka/risk_class"]`.
- `tools/call`: zarf ayrılır, `execute_tool` çalışır, commit + `send_pending`. Sonuç
  `structuredContent` ve aynı JSON'u taşıyan metin içeriği; `failed`/`rejected`/ön koşul hatası →
  `isError=true` + `{"status": "error", "error": {code, message}}`. **Yapay zekâ çağıranlara dönen
  sonuç PII maskelidir** (kimlik/numara alanları hariç). `confidence` verilmezse 0 sayılır → R1 onaya düşer.
- Testte: `gateway.settings_provider`, `auth_settings_provider`, `authenticator_provider`,
  `llm_provider`, `notifier_provider` değiştirilebilir; istemci SDK `Client` +
  `streamable_http_client(url, http_client=httpx2.AsyncClient(transport=httpx2.ASGITransport(app)))`
  (ağ yok). Oturum yöneticisinin görev grubu aynı görevde açılıp kapanmalıdır (bkz. `tests/test_mcp.py`).

## Bağlayıcılar — `serviszeka.connectors`, kayıt `serviszeka.tools.connectors`

Varsayılan hedef yığın: **Microsoft Entra ID** (kimlik), **Microsoft 365 / Graph** (posta, lisans),
**Teams** (kanal mesajı). Araçlar somut sağlayıcıyı değil arayüzü ister (`connectors/base.py`):

| Tür | Arayüz | Yöntemler |
|---|---|---|
| `identity` | `IdentityProvider` | `get_user`, `get_manager`, `list_groups`, `get_group`, `is_privileged_group`, `add_to_group`, `remove_from_group`, `revoke_sessions`, `set_account_enabled`, `reset_password`, `reset_mfa`, `unlock_account` |
| `mail` | `MailProvider` | `grant_shared_access`, `send_mail` |
| `license` | `LicenseProvider` | `list_skus`, `assign_license` |
| `messaging` | `Notifier` | `send_to_user(sensitive=)`, `post_to_channel` |

Uygulamalar: `connectors/graph.py` (`GraphClient` + `GraphIdentityProvider`, `GraphMailProvider`,
`GraphLicenseProvider`, `TeamsGraphNotifier`), `connectors/memory.py` (`InMemoryDirectory`: testler,
geliştirme sahte kipi ve kapı değerlendirmesi; `demo()` Türkçe örnek dizin; `writes` / `messages` ile
yan etkiler gözlenir; `failures[op]` ile hata enjekte edilir). Kurulum `connectors/setup.py`
(`SZ_CONNECTOR_PROVIDER` = `none` | `fake` (yalnızca `SZ_ENVIRONMENT=development`, aksi halde açılış
reddedilir) | `graph` (`SZ_GRAPH_TENANT_ID`, `SZ_GRAPH_CLIENT_ID`, `SZ_GRAPH_CLIENT_SECRET` zorunlu)
| `keycloak`; ya da ayrı ayrı `SZ_IDENTITY_PROVIDER` + `SZ_NOTIFIER_PROVIDER`, bkz. "Sağlayıcı seçimi").
Kayıtlı değilse o türü isteyen araç 503 `unavailable` döner. Keycloak/Slack aynı protokolü uygular.
Kurallar: HTTP yalnızca `httpx.AsyncClient` (kurucu `transport` alır; testte `httpx.MockTransport`,
`tests/test_connectors_graph.py`), gizli bilgiler `SZ_` ayarlarından, yazan her yöntem `dry_run` ve
`idempotency_key` alır, bağlayıcı politika/onay denetimi yapmaz. Graph hataları `ConnectorError`
(`ConnectorNotFoundError`, `ConnectorAuthError` — gizli bilgi içermez, `ConnectorNotSupportedError`) olur.

**Graph istemcisi.** Client credentials: `POST {login}/{tenant}/oauth2/v2.0/token`
(`scope=https://graph.microsoft.com/.default`); belirteç süresinden 60 sn öncesine kadar önbellekte,
401'de bir kez yenilenir. Uç noktalar (v1.0):

- `GET /users/{id}?$select=id,displayName,userPrincipalName,mail,accountEnabled,department,jobTitle,otherMails` (404 → yok)
- `GET /users/{id}/manager`, `GET /users/{id}/memberOf` (`@odata.nextLink` sayfalama, en fazla 20 sayfa; dizin rolleri atlanır)
- `GET /groups/{id}?$select=id,displayName,securityEnabled,isAssignableToRole` (rol atanabilir grup ya da `SZ_IDENTITY_PRIVILEGED_GROUPS` → ayrıcalıklı)
- `POST /groups/{id}/members/$ref` (`{"@odata.id": ".../directoryObjects/{uid}"}`; zaten üye 400 → başarı), `DELETE /groups/{id}/members/{uid}/$ref` (404 → başarı)
- `POST /users/{id}/revokeSignInSessions`, `PATCH /users/{id}` `{"accountEnabled": false}`
- Parola: `GET /users/{id}/authentication/passwordMethods` + `POST /users/{id}/authentication/methods/{methodId}/resetPassword` `{"newPassword"}` (202 + `Location`)
- MFA: `GET /users/{id}/authentication/methods` + türüne göre `DELETE /users/{id}/authentication/{tür}Methods/{id}` (parola yöntemi korunur)
- Lisans: `GET /subscribedSkus` (`prepaidUnits.enabled − consumedUnits`), `POST /users/{id}/assignLicense`
- Posta: `POST /users/{SZ_GRAPH_SENDER_MAILBOX}/sendMail`; hassas bildirim (geçici parola) yalnızca kullanıcının `otherMails` ikincil adresine
- Teams: kanal başına **gelen webhook** URL'si (`SZ_TEAMS_WEBHOOK_URLS`, JSON `{"kanal": "url"}`; gizli bilgi gibi saklanır), Adaptive Card zarfı; ek Graph izni gerekmez

**Desteklenmeyenler (Graph'ta açık `ConnectorNotSupportedError`; davranış uydurulmaz):**
- `unlock_account`: Entra ID bulutta doğrudan "kilidi aç" API'si yoktur (Akıllı Kilitleme süre dolunca
  kendiliğinden açılır). Kilit şirket içi AD'de (`lockoutTime`) LDAP ile açılır — **kapsam dışı**. Arayüz
  ve sahte sağlayıcı bunu destekler; şirket içi AD bağlayıcısı (ör. LDAP) eklenince aynı arayüzle bağlanır.
- `grant_shared_access`: Graph v1.0'da posta kutusu izni (FullAccess / SendAs) API'si yoktur; Exchange
  Online PowerShell / REST (`Add-MailboxPermission`, `Add-RecipientPermission`) gerekir — kapsam dışı.
  Graph kipinde bu adım `failed` olur ve talep insan tarafından yeniden denenir/elle yapılır.

### Doğrulanmalı (gerçek kiracıda sınanmadı)

Aşağıdakiler Microsoft belgelerine göre yazıldı, testler `MockTransport` ile; üretime almadan önce
bir test kiracısında doğrulanmalıdır:

1. Uygulama izinleri (yönetici onayı): `User.Read.All`, `GroupMember.Read.All`,
   `GroupMember.ReadWrite.All`, `User.RevokeSessions.All` (ya da `User.ReadWrite.All`),
   `User.ReadWrite.All` (accountEnabled), `UserAuthenticationMethod.ReadWrite.All`,
   `Organization.Read.All`, `LicenseAssignment.ReadWrite.All` (ya da `User.ReadWrite.All`), `Mail.Send`
   (uygulama erişim ilkesiyle gönderen posta kutusuna sınırlanmalı). Ayrıcalıklı kullanıcıları devre
   dışı bırakmak / parolasını sıfırlamak uygulamaya ek dizin rolü gerektirebilir.
2. `resetPassword` (kimlik doğrulama yöntemleri API'si) uygulama izniyle (yalnızca yetkilendirilmiş/
   delegated değil) desteklenip desteklenmediği, 202 uzun süren işlemin izlenmesi ve yeni parolayla ilk
   girişte değişimin zorunlu olup olmadığı. Desteklenmiyorsa alternatif `PATCH /users/{id}`
   `passwordProfile` (`forceChangePasswordNextSignIn`) uygulamaya Parola/Kullanıcı yöneticisi rolü ister.
3. MFA sıfırlamada varsayılan yöntemin, başka yöntemler varken silinememesi ve yöntem türü → uç nokta
   kesimi eşlemesi (`MFA_METHOD_SEGMENTS`).
4. Zaten üye olan kullanıcıyı eklerken dönen 400 mesajının metin eşleşmesi ("already exist").
5. `assignLicense`'ta lisans yokken / kullanım konumu (`usageLocation`) boşken dönen hata biçimi.
6. Teams Workflows webhook'unun Adaptive Card zarfını kabul etmesi ve eski Office 365 bağlayıcı
   webhook'larının emeklilik takvimi.
7. `memberOf`'un geçişli olmayan (doğrudan) üyelik döndürmesinin onay grubu denetimi için yeterliliği
   (iç içe gruplar `transitiveMemberOf` gerektirir).

### Slack (`connectors/slack.py`)

`SZ_SLACK_BOT_TOKEN` (ya da yalnızca `SZ_SLACK_WEBHOOK_URL`) tanımlıysa her `SZ_CONNECTOR_PROVIDER`
kipinde `messaging` bağlayıcısı `SlackNotifier` olur (`setup._with_slack`; yalnızca
`SZ_NOTIFIER_PROVIDER` boşken — açık `teams`/`log` seçimi Slack'i dışlar); kimlik sağlayıcı ve
önceki bildirici korunur. Nöbetçi uyarısı (`notifications.get_notifier`): `SZ_ONCALL_WEBHOOK_URL`
öncelikli (davranış değişmez), yoksa bot + `SZ_SLACK_ONCALL_CHANNEL`, yoksa Slack webhook'u.

- `post_to_channel`: yalnızca yapılandırılmış kanallar — `default` (`SZ_SLACK_DEFAULT_CHANNEL`),
  `oncall` (`SZ_SLACK_ONCALL_CHANNEL`) ya da bu kanal kimliklerinin kendisi; başka kanal/kullanıcı
  kimliği `ConnectorNotFoundError`. `POST chat.postMessage` (`Authorization: Bearer`, JSON;
  `unfurl_links/unfurl_media=false`). Webhook kipinde (bot yok) yalnızca `default` kanal.
- `send_to_user`: Slack kimliği (`U…`/`W…`) doğrudan; e-posta `GET users.lookupByEmail`; dizin
  kimliği kimlik sağlayıcıdan posta/UPN ile çözülür. `POST conversations.open {"users"}` -> DM,
  ardından `chat.postMessage`. `dry_run` yalnızca okur (DM açmaz). `sensitive=True` asla Slack'e
  gitmez: önceki bildiriciye (Graph `otherMails` / sahte) devredilir, yoksa `ConnectorError`.
- Biçim: Block Kit (`header` plain_text ≤150, `section` mrkdwn ≤3000, `fields` ≤10 × ≤2000,
  `context`) + kaçışlı `text` yedeği. Tüm metinde `&`→`&amp;`, `<`→`&lt;`, `>`→`&gt;` (böylece
  `<!channel>`, `<@U…>`, `<#C…>`, `<url|metin>` çalışmaz); düz `@channel/@here/@everyone` sıfır
  genişlikli boşlukla bozulur. Kesme varlığı bölmez.
- Semantik: HTTP 200 + `{"ok": false}` hatadır (`invalid_auth`, `missing_scope` vb. →
  `ConnectorAuthError`, `missing_scope`'ta `needed` kapsamı mesajda; `channel_not_found`,
  `users_not_found` → `ConnectorNotFoundError`; diğerleri `ConnectorError`). 429 + `Retry-After`
  ≤ 30 sn ise bir kez beklenip yeniden denenir, ikinci 429 ya da daha uzun bekleme hata. Belirteç ve
  webhook URL'si `SecretStr`; hiçbir mesaj/`repr`/günlükte yer almaz.

**Slack — doğrulanmalı (gerçek çalışma alanında sınanmadı):**

8. Bot kapsamları: `chat:write`, `im:write`, `users:read`, `users:read.email`; botun kanala üye
   olması gerektiği (`not_in_channel`) ve kanal adının (kimlik yerine) `chat.postMessage`'ta kabulü.
9. `users.lookupByEmail`'in Entra `mail`/UPN ile eşleşmesi (Slack profil e-postası farklı olabilir)
   ve misafir/silinmiş kullanıcı yanıt biçimi.
10. `header` (`plain_text`) içinde `&amp;`/`&lt;` varlıklarının çözülüp çözülmediği (çözülmüyorsa
    başlıkta görsel kusur; güvenlik etkisi yok) ve düz `@channel`'ın `link_names` gönderilmeden
    yayın bahsetmesine dönüşmediği.
11. Block Kit sınırları (header 150, section 3000, fields 10×2000, context 10 öğe) ve `text` yedeği
    40.000 karakter sınırı.
12. Gelen webhook'un `blocks` kabulü ve hata gövdeleri (`invalid_payload`, `no_service`,
    `channel_is_archived`, `action_prohibited`); webhook 429 davranışı ve `Retry-After` başlığı.
13. `notify.post_to_channel` araç açıklaması hâlâ "Teams kanalı" der; Slack kipinde kanal adları
    `default`/`oncall` (araç metni güncellenmeli — araç dosyası bu değişikliğin dışında).

### Keycloak (`connectors/keycloak.py`)

`SZ_CONNECTOR_PROVIDER=keycloak` yalnızca `identity` bağlayıcısını kurar (`KeycloakIdentityProvider`;
posta/lisans/bildirim Keycloak'ta yok — `messaging` için Slack ayarlanabilir). Zorunlu:
`SZ_KEYCLOAK_SERVER_URL` (Keycloak <17: `.../auth`), `SZ_KEYCLOAK_REALM`, `SZ_KEYCLOAK_CLIENT_ID`,
`SZ_KEYCLOAK_CLIENT_SECRET` (`SecretStr`); isteğe bağlı `SZ_KEYCLOAK_MANAGER_ATTRIBUTE` (varsayılan
`manager`). Testler `tests/test_keycloak_connector.py` (`MockTransport`).

- Belirteç: hizmet hesabı **client credentials**, `POST {server}/realms/{realm}/protocol/openid-connect/token`;
  süresinden ≤60 sn önceye kadar önbellek, Admin API 401'de bir kez yenileme. Gizli anahtar/belirteç
  hata mesajına, `repr`'e ya da istisna zincirine girmez; hata mesajı yalnızca HTTP kodu + tanımlayıcı
  biçimli `error` kodunu taşır (`error_description` taşınmaz).
- Admin API (`{server}/admin/realms/{realm}`): kullanıcı `GET /users/{id}` → `GET /users?username=..&exact=true`
  → (`@` varsa) `GET /users?email=..&exact=true`, sonuç istemcide de tam eşleşmeyle süzülür; farklı
  kullanıcılara çıkan başvuru `AmbiguousReferenceError`. Üyelik `GET /users/{id}/groups?first=&max=`
  (doğrudan), `PUT`/`DELETE /users/{id}/groups/{groupId}`; `POST /users/{id}/logout`;
  `PUT /users/{id}` `{"enabled"}`; `PUT /users/{id}/reset-password` `{"type":"password","temporary":true}`;
  MFA `GET /users/{id}/credentials` + `DELETE /users/{id}/credentials/{id}` (`otp`, `webauthn`,
  `webauthn-passwordless`, `recovery-authn-codes`; parola ve bilinmeyen türler korunur); kilit açma
  `GET`+`DELETE /attack-detection/brute-force/users/{id}` (**Keycloak'ta kilit açma desteklenir**,
  Entra'dan farklı).
- Parola sıfırlama varsayılanı geçici paroladır (`temporary=true`); `execute-actions-email`
  (UPDATE_PASSWORD) seçilmedi: bağlantıyı birincil e-postaya gönderir (ele geçirilmiş hesapta saldırgan
  okur) ve arayüzün geçici parola sözleşmesini karşılamaz. Keycloak'ta ikincil e-posta alanı olmadığından
  `other_mails` boştur: hassas teslimat (geçici parola bildirimi) bu sağlayıcıyla yapılamaz.
- Yönetici: yerleşik ilişki yok; `SZ_KEYCLOAK_MANAGER_ATTRIBUTE` özniteliği yöneticinin **kullanıcı adını**
  taşır (yalnızca kullanıcı adıyla tam eşleşme). Birden fazla farklı değer → hata; bulunamayan/kendisi → `None`
  (talep yalnızca admin onayına düşer).
- Grup başvurusu kimlik, tam yol (`/ust/alt`) ya da tam ad; ad/yol `GET /groups?search=<yaprak>&first=&max=`
  ile aranır, ağaç düzleştirilip **NFKC + casefold** ile tam eşleşme aranır (bulanık eşleşme yok); birden
  fazla eşleşme → `AmbiguousReferenceError`, yazma yapılmaz. Ayrıcalıklı: `SZ_IDENTITY_PRIVILEGED_GROUPS`
  (kimlik/yol/ad, normalize) **ve alt grupları** (alt grup üst grubun rollerini devralır) ya da
  `GET /groups/{id}/role-mappings`'te doğrudan `realm-management` istemci rolü; bilinmeyen/belirsiz grup
  ayrıcalıklıdır.

**Keycloak — doğrulanmalı (gerçek sunucuda sınanmadı):**

- **K1.** Hizmet hesabı rolleri: `view-users`, `query-groups`, `manage-users` (`realm-management`);
  grup rol eşlemelerini ve brute-force durumunu okumak için gereken en az rol.
- **K2.** `GET /users?exact=true` (sürüm desteği) ve kullanıcı adı/e-postanın sunucuda küçük harfe
  çevrilerek arandığı; e-posta tekilliği (`duplicateEmailsAllowed`) kapalıyken davranış.
- **K3.** `GET /groups?search=` aramasının büyük/küçük harfe duyarsız alt dize araması olduğu ve eşleşen
  alt grupların `subGroups` içinde döndüğü (KC 23+ `populateHierarchy`/`subGroupCount` değişiklikleri);
  dönmüyorsa alt grup yol/ad başvurusu `None` (→ ayrıcalıklı, yazma yok) olur.
- **K4.** `PUT /users/{id}` yalnızca `{"enabled"}` kısmi gösterimiyle diğer alanları (öznitelikler, KC 24+
  kullanıcı profili) silmediği.
- **K5.** `PUT /users/{id}/groups/{gid}` zaten üyeyken 204 döndüğü (tekrarlanabilirlik).
- **K6.** `POST /users/{id}/logout`'un çevrim dışı (offline) oturumları da kapatıp kapatmadığı; verilmiş
  erişim belirteçleri süreleri dolana dek geçerlidir.
- **K7.** MFA kimlik bilgisi türleri (`MFA_CREDENTIAL_TYPES`) ve silme sonrası yeniden kaydın realm akışınca
  istendiği (zorunlu eylem eklenmez).
- **K8.** Brute-force durum gövdesi (`disabled`, `numFailures`) ve kalıcı kilitlemenin kullanıcıyı
  `enabled=false` yapıp yapmadığı (yapıyorsa kilit açma tek başına yetmez; `set_account_enabled` ayrı işlemdir).
- **K9.** Keycloak 24+ bildirime dayalı kullanıcı profilinde yönetici özniteliğinin tanımlı olması ve
  `GET /users/{id}` yanıtında dönmesi.
- **K10.** Araç katmanındaki `identity_standard_groups` denetimi grup başvurusunu ham dizeyle karşılaştırır;
  Keycloak'ta izin listesine grup **kimliklerini** yazın (yol/ad varyantı R3'e düşer — güvenli ama insansız
  çözümü engeller).

### Sağlayıcı seçimi (`connectors/setup.py`, `resolve_providers`)

Kimlik ve bildirim sağlayıcısı ayrı seçilir. İkisi de boşsa eski davranış (`SZ_CONNECTOR_PROVIDER` +
Slack katmanı) **aynen** geçerlidir; geliştirme sahte kipi (`SZ_CONNECTOR_PROVIDER=fake`) değişmedi.
Açılışta (`check_startup`, `main.lifespan`) denetlenir; hata `ConnectorConfigError`, Türkçe, yalnızca
eksik ayarların **adını** yazar (değer, gizli bilgi yok). Boş gizli değer (`SecretStr("")`, ör.
`.env.example`'dan kopyalanan `SZ_KEYCLOAK_CLIENT_SECRET=`) eksik sayılır.

| `SZ_IDENTITY_PROVIDER` | Kurulan | Zorunlu |
|---|---|---|
| `graph` | identity + mail + license (+ temel bildirici `TeamsGraphNotifier`) | `SZ_GRAPH_TENANT_ID`, `SZ_GRAPH_CLIENT_ID`, `SZ_GRAPH_CLIENT_SECRET` |
| `keycloak` | yalnızca identity | `SZ_KEYCLOAK_SERVER_URL`, `SZ_KEYCLOAK_REALM`, `SZ_KEYCLOAK_CLIENT_ID`, `SZ_KEYCLOAK_CLIENT_SECRET` |
| `memory` | sahte dizin (identity + mail + license + temel bildirici) | `SZ_ENVIRONMENT=development` |
| boş | `SZ_CONNECTOR_PROVIDER`'dan (`fake` → `memory`); ikisi farklı sağlayıcı seçerse açılış reddedilir | |

| `SZ_NOTIFIER_PROVIDER` | `messaging` | Zorunlu |
|---|---|---|
| `teams` | `TeamsGraphNotifier` (Graph `sendMail` + Teams webhook); Slack ayarlı olsa da Teams | kimlik `graph` + `SZ_GRAPH_SENDER_MAILBOX` |
| `slack` | `SlackNotifier`; hassas bildirim kimlik yığınının temel bildiricisine devredilir (Graph: `otherMails`, `memory`: dizin, Keycloak: **yok** → `ConnectorError`) | `SZ_SLACK_BOT_TOKEN` ya da `SZ_SLACK_WEBHOOK_URL` |
| `log` | kaydedilmez (`notify.send_*`, `identity.reset_password` 503) | — |
| boş | eski davranış: temel bildirici, Slack ayarlıysa Slack | |

Nöbetçi uyarısı (`notifications.get_notifier`): `SZ_ONCALL_WEBHOOK_URL` her zaman öncelikli; açık
`teams` ya da `log` seçiminde Slack kullanılmaz (log), `slack` ya da boşta önceki Slack sırası.
Hassas bildirimin teslim edilemeyeceği bileşimlerde (kimlik `keycloak`; ya da `graph` +
`SZ_GRAPH_SENDER_MAILBOX` boş) açılışta uyarı loglanır (açılış engellenmez). Testler
`tests/test_connector_selection.py`.

### Kapı ölçümü hedef yığına göre (`connectors/fake_stacks.py`)

`uv run python evals/run_automation_eval.py --stack memory|entra|keycloak` (`automation_eval.run(...,
stack=)`; `--save` `memory` dışında `evals/results/automation_<yığın>.json`). Aynı veri seti/etiketler;
sahte dizin, gerçek bağlayıcının **belgelenmiş** kısıtlarıyla sarılır. Ağ yok.

- `memory`: `InMemoryDirectory` (varsayılan; davranış değişmedi).
- `entra`: `EntraShapedDirectory` — `unlock_account` ve `grant_shared_access` `connectors.graph` ile aynı
  `ConnectorNotSupportedError` (dry_run dahil).
- `keycloak`: `KeycloakShapedDirectory` (kilit açma desteklenir; `other_mails` her zaman boş) yalnızca
  `identity`; `messaging` **gerçek** `SlackNotifier`, Slack Web API'si `FakeSlackWorkspace`
  (`httpx.MockTransport`: `users.lookupByEmail`, `conversations.open`, `chat.postMessage`); `mail` ve
  `license` kayıtlı değil (`install_stack` yığında olmayan türleri kayıttan çıkarır).

Ölçüm (200 talep, 155 kapanan standart talep; üçünde de yanlış insansız 0, ihlal 0, LLM çağrısı 0):

| Yığın | Standart insansız | Fark |
|---|---|---|
| `memory` | 134 / 155 (%86,5) — GEÇTİ | |
| `entra` | 113 / 155 (%72,9) — GEÇTİ | `account_unlock` 0/25 (`failed`: bulutta kilit açma yok); `shared_mailbox_access` ve `new_employee_account` `failed` (posta kutusu izni yok) |
| `keycloak` | 83 / 155 (%53,5) — GEÇTİ (pay %3,5) | `account_unlock` 21/25 çalışır; `password_reset` 0/40 (geçici parola teslim edilemez: ikincil adres yok, Slack'e gönderilmez); `m365_license` 0/20 (lisans sağlayıcısı yok) |

**Yığınlar — doğrulanmalı / sınırlar:**

- **Y1.** Sahteler gerçek sunucu değildir: Keycloak biçimli sahte grubu yalnızca kimlikle bulur ve
  ayrıcalığı yapılandırmadan alır (gerçek bağlayıcı yol/ad ve `realm-management` rol eşlemesi de
  kullanır — K3, K10), kalıcı brute-force kilidinin `enabled=false` yapması (K8) modellenmez. Keycloak
  sonucu bu yüzden üst sınırdır.
- **Y2.** Etiketler (`expected_touchless`) yığından bağımsız yazıldı; `entra`/`keycloak` "kaçırılan
  otomasyon" listesi yığın kısıtından gelir, etiket hatası değildir.
- **Y3.** Keycloak + Slack yığınında parola sıfırlama için ikincil teslim kanalı yok: doğrulanmış ikincil
  e-posta özniteliği (KC 24+ kullanıcı profili) ya da SMS bağlayıcısı tasarlanmadan bu katalog öğesi
  insansız çözülemez. Keycloak'ta `m365_license` / `shared_mailbox_access` öğeleri devre dışı
  bırakılmalı ya da ayrı bir M365 bağlayıcısı eklenmeli.
- **Y4.** `SZ_NOTIFIER_PROVIDER=teams` için `SZ_GRAPH_SENDER_MAILBOX` zorunlu tutuldu (kullanıcı bildirimi
  onsuz çalışmaz); `SZ_TEAMS_WEBHOOK_URLS` isteğe bağlı (boşsa kanal mesajı `ConnectorNotFoundError`).

## Kayıtlı araçlar (temel katman: 33; toplam 61 — hizmet talebi 20, sorun yönetimi 8)

| Alan | R0 | R1 | R2 |
|---|---|---|---|
| incident | `search`, `get` | `create` (P1/P2 → R2), `classify`, `set_priority` (koşullu), `assign`, `link_ci`, `set_on_hold` (koşullu) | `resolve` |
| change | `search`, `get`, `calendar`, `check_conflicts`, `list_templates` | `draft_rfc`, `assess_risk` (risk ajanı; kendi politikası) | `submit_for_cab`, `record_pir` |
| kb / known_error | `kb.search`, `kb.get` | `kb.draft_article`, `kb.update_draft` (yalnızca taslak), `known_error.create` (taslak) | `kb.publish`, `kb.retire` |
| cmdb | `search_ci`, `get_ci`, `impact_analysis` | | |
| sla | `get_status`, `list_at_risk` | | |
| notify | | `oncall_page` (mesaj maskelenir, commit sonrası) | |
| audit | `verify_chain`, `search` (yalnızca admin) | | |

İnsan rolleri: olay/KB okuma agent+approver, yazma agent; değişiklik/CMDB okuma agent+cab+change_manager,
değişiklik yazma agent+change_manager; denetim admin. Hizmet aramaları için `services.incidents.list_incidents`
ve `services.changes.list_changes` `q` (numara/başlık metin araması) parametresi aldı.

## Hizmet kataloğu ve hizmet talepleri

Modüller: `models.CatalogItem`, `models.ServiceRequest`, `services/catalog.py`, `services/requests.py`,
`services/request_schemas.py`, `tools/builtin/catalog.py`, `tools/builtin/identity.py`,
`api/catalog.py`, `api/requests.py`, `request_evaluation.py` + `evals/run_request_eval.py`. Testler:
`tests/test_catalog.py`, `tests/test_requests.py`, `tests/test_connectors_graph.py`, kapı
`tests/test_gate_no_unapproved_risk.py` bölüm 6.

**Katalog öğesi.** `key`, `name`, `description`, `category`, `form_schema` (JSON şeması alt kümesi:
`string` (`enum`, `minLength`/`maxLength`, `format: user` = kullanıcı seçici, `format: group` = grup
seçici), `boolean`; `required`, `default`), `approval_policy` (`none` | `manager` | `group` +
`approval_group`), `fulfilment_plan` (sıralı adımlar `{tool, title, args, when?}`; `args` şablonu
`{{requester}}`, `{{form.alan}}`, `{{request.id}}`, `{{request.number}}` başvurur; araç kayıtlı olmalı,
`request.*` adımı yasak), `standard`, `active`, `version`. Tohum: 10 Türkçe öğe
(`catalog.seed_default_items`, idempotent; dev sahte kipte açılışta, `POST /catalog/seed` admin):

| Öğe | Standart | İş onayı | Adımlar (risk) |
|---|---|---|---|
| Şifre sıfırlama (`password_reset`) | evet | yok | verify_requester (R0), reset_password (R1 self, aksi R2) |
| Hesap kilidi açma (`account_unlock`) | evet | yok | verify_requester, unlock_account (doğrulanmış self-servis R1, aksi R2), bildirim (R1) |
| Standart yazılım grubu (`standard_software_group`) | evet | yok | add_to_group (izin listesinde R1, aksi R3), bildirim |
| Teams ekip kanalı (`teams_channel_access`) | evet | yok | add_to_group, bildirim |
| VPN erişim grubu (`vpn_access`) | evet | yok | add_to_group, bildirim |
| M365 lisans talebi (`m365_license`) | evet | yok | check_available (R0), license.assign (R1), bildirim |
| Paylaşılan posta kutusu (`shared_mailbox_access`) | hayır | yönetici | grant_shared_access (R2) |
| Yeni çalışan hesabı (`new_employee_account`) | hayır | grup (İK) | lisans, gruplar, posta kutusu (R2), ek yetki (R3) |
| Yetkili grup erişimi (`privileged_group_access`) | hayır | yönetici | add_to_group ayrıcalıklı → R3 |
| MFA sıfırlama (`mfa_reset`) | hayır | yok | verify_requester, reset_mfa (R2) |

Grup/SKU/posta kutusu kimlikleri sahte dizinle uyumludur; üretimde kiracıdaki gerçek kimliklerle
güncellenmeli (`PATCH /catalog/items/{id}`) ve standart grup izin listesi `SZ_IDENTITY_STANDARD_GROUPS`
ile ayarlanmalıdır.

**Hizmet talebi.** `REQ0000001`; durumlar `submitted → awaiting_approval → fulfilling → fulfilled`, ayrıca
`rejected` / `failed` / `cancelled`. Plan açılışta talebe kopyalanır (sonradan katalog değişikliği açık
talebi etkilemez). **İş onayı risk onayından ayrıdır ve önce gelir**: yönetici dizinden
(`IdentityProvider.get_manager`) ya da grup üyeliğinden (`list_groups`) bulunur; yapay zekâ, talep eden ve
talebi açan onaylayamaz (kimlik dizinde de karşılaştırılır), admin her zaman onaylayabilir. Karşılama her
adımı `tools.execute_tool` ile **`ai:fulfilment`** aktörüyle çalıştırır (deterministik plan, güven 1.0):
R1 adımlar yalnızca politika izin verirse (gölge kapalı + eşik) çalışır; R2/R3 adımlar (ve koşullu riski
yükselen R1) `ActionRequest` (`request_id` bağlı) açar ve talep bekler; istek yürütülünce
`tools.actions` → `requests.on_action_resolved` karşılamayı sürdürür, reddedilirse talep `rejected`,
başarısız/süresi dolmuşsa `failed`. Adım idempotency anahtarı `{talep}:{adım}:{deneme}`. İptal bekleyen
eylem isteğini reddeder (sonradan onaylanıp çalışamaz).

**İnsansız (touchless).** Talep `fulfilled` **ve** insan onayı (iş onayı ya da eylem isteği onayı) sayısı
0 **ve** insan düzenlemesi (form düzenleme, yeniden deneme, el ile adım, insanın tetiklediği adım) 0 ise
`touchless=true`; değilse `touchless_reason` (kim, ne). Ölçüm `requests.touchless_metrics` /
`GET /requests/metrics`: kapanan (karşılanan + reddedilen + başarısız; iptal hariç) standart taleplerde
oran, hedef %50. `uv run python evals/run_request_eval.py` gerçekçi karışımı uçtan uca çalıştırır
(şu an 20/23 = %87) ve değişmezleri denetler (insansız talepte eylem isteği yok; yürütülen her R2/R3
gerekli insan onayına sahip).

**Kapı ölçümü (etiketli veri seti).** `uv run python evals/run_automation_eval.py`
(`src/serviszeka/automation_eval.py`, veri `evals/data/requests.jsonl` + kart `REQUESTS_CARD.md`,
üretici `evals/generate_requests_dataset.py`): 200 gerçekçi Türkçe talep (10 öğenin hepsi; standart
öğelerde otomatikleşmemesi gereken 21 talep: ayrıcalıklı / bilinmeyen / izin listesi dışı grup,
doğrulanmamış talep eden, devre dışı hesap, ikincil e-posta yok, boş lisans yok) altın etiket
`{item, expected_touchless, reason}` ile gerçek boru hattından geçer: ayrı bellek içi veritabanı, veri
setindeki profillerden kurulan sahte dizin, gölge mod kapalı, varsayılan eşik, LLM yok (çağrılırsa
sayılır, kapı kalır). İnsanlar simüle edilir (yönetici / İK onay grubu iş onayı, iki BT onaylayan,
servis masası el ile adım). Kapı: standart oran ≥ %50 **ve** yanlış insansız = 0 **ve** değişmez ihlali
0 **ve** kapanmayan talep 0. Son ölçüm: **134/155 = %86,5**, yanlış insansız 0, ihlal 0, eylem
istekleri R2 40 (36 yürütüldü, 4 reddedildi) / R3 17 (11 yürütüldü, 6 reddedildi), etiket uyumu %100.
Testler `tests/test_automation_eval.py` ve kapı bölüm 6
(`test_gate_labelled_requests_half_touchless_zero_false_touchless`).

**Portal iş onayı.** `ServiceRequestRead.can_decide` oturumdaki kullanıcının bu talebe şimdi iş onayı
verip veremeyeceğini söyler (`services.requests.can_decide`; talep eden/açan/yapay zekâ hiçbir zaman).
Yönetici / onay grubu üyesi `/taleplerim?sekme=onay` ("Onayımı bekleyenler",
`GET /requests?awaiting_my_approval=true`) listesinden talebe gider ve "İş onayı" kartında onaylar ya da
gerekçeyle reddeder (`expected_version`; 409'da "Talep değişti").

**Araçlar (20).**

| Araç | Sınıf | Not |
|---|---|---|
| `catalog.list_items`, `catalog.get_item` | R0 | yalnızca etkin öğeler |
| `request.create` | R1 | adımlar yine kendi risk sınıfıyla değerlendirilir |
| `request.get` | R0 | adım sonuçları + bağlı eylem istekleri |
| `request.run_fulfillment_step` | R1 | kaçan kancayı işler / sıradaki adımı çalıştırır; insan çağırırsa dokunuş |
| `request.complete` | R1 | el ile adımı tamamlar (insansız sayılmaz) |
| `identity.get_user`, `identity.verify_requester`, `license.check_available` | R0 | |
| `identity.reset_password` | R1 / R2 | R1 yalnızca talep hesabın sahibince kendi oturumuyla açılmış, karşılanıyor, planda bu adım var ve dizin eşleşiyorsa; geçici parola hiçbir sonuç/denetim/istekte yer almaz, yalnızca ikincil adrese gider |
| `identity.unlock_account` | R1 / R2 | `reset_password` ile aynı self-servis doğrulaması: başkası adına / talepsiz / devre dışı ya da dizinde olmayan hesapta R2 (Faz 3 kapı ölçümü bu açığı yakaladı) |
| `license.assign`, `notify.send_to_user`, `notify.post_to_channel` | R1 | kanal mesajı PII maskeli |
| `identity.add_to_group` | R1 / R3 | R1 yalnızca `SZ_IDENTITY_STANDARD_GROUPS`'ta ve ayrıcalıklı değilse; ayrıcalıklı/bilinmeyen R3 |
| `identity.reset_mfa`, `identity.remove_from_group`, `identity.revoke_sessions`, `mailbox.grant_shared_access` | R2 | |
| `identity.disable_user` | R3 | |

**HTTP.** `/catalog`: `GET /catalog/items` (`q`, `category`, `standard`; etkin olmayanlar yalnızca
admin), `GET /catalog/items/{id|key}`, `POST /catalog/items`, `PATCH /catalog/items/{id}`
(`expected_version`), `POST /catalog/seed` (admin). `/requests`: `POST /requests` (requester kendi
adına; `requester` alanı yalnızca agent/admin), `GET /requests` (requester yalnızca kendi;
`awaiting_my_approval`, `mine`, `state`, `item`, `standard`, `touchless`), `GET /requests/metrics`,
`GET /requests/{id|number}` (başkasınınki 404), `POST /requests/{id}/approve|reject` (iş onayı),
`POST /requests/{id}/cancel` (talep eden ya da agent), `PATCH /requests/{id}` (form, agent),
`POST /requests/{id}/retry`, `POST /requests/{id}/complete-step` (agent). `ai_agent` belirteci bu uçları
kullanamaz (MCP araçlarını kullanır).

## Göç

`migrations/versions/0007_action_requests_tool_invocations.py`: `action_requests`, `action_approvals`
(`(action_request_id, approver_key)` tekil), `tool_invocations` (`(actor, idempotency_key)` tekil) ve
`ai_decisions.action_request_id` (FK'siz, indeksli: SQLite'ta tablo kopyalamadan eklenir). Önceki göçlerle
aynı idempotent üslup: tablo/indeks yalnızca yoksa kurulur, sütun yalnızca yoksa eklenir.

`migrations/versions/0008_service_catalog_requests.py`: `catalog_items` (`key` tekil), `service_requests`
(`number` tekil; durum/öğe/talep eden/insansız indeksleri) ve `action_requests.request_id` (FK'siz,
indeksli). Aynı idempotent üslup; enum sütunları VARCHAR.

## Açık konular (sonraki adımlar)

- Portal ekranları hazır (`portal/README.md`): eylem onayları `/onaylar?sekme=eylem`, katalog `/katalog`, talep
  ayrıntısı + adım zaman çizelgesi + iş onayı `/taleplerim/:id`, onayımı bekleyenler `/taleplerim?sekme=onay`,
  sorunlar `/problemler`. Açık: el ile adım/yeniden deneme (agent) ve dizin araması (kullanıcı/grup seçici) ekranı.
- Graph bağlayıcısı gerçek kiracıda doğrulanmadı (yukarıdaki "Doğrulanmalı" listesi). Hesap kilidi açma
  (şirket içi AD/LDAP) ve paylaşılan posta kutusu izni (Exchange Online) için bağlayıcı yok.
- Keycloak + Slack yığınında geçici parola teslim kanalı yok (Y3); kapı bu yığında %53,5 ile sınırda.
- Self-servis doğrulaması oturum kimliğine dayanır; MFA adım yükseltme / Verified ID gibi güçlü doğrulama yok.
- Bekleyen P1/P2 `incident.set_priority` / `incident.create` isteğinde nöbetçi uyarısı (triage'daki
  gibi) henüz yok; ajan `notify.oncall_page` ile ayrıca uyarabilir.
- Ajanların eylem isteği sonucunu sorgulayacağı salt okunur bir araç (`action.get` benzeri) yok
  (`request.get` bağlı eylem isteklerini gösterir).
- Bekleyen iş onayı için onaylayana bildirim gönderilmiyor. Süresi dolan eylem isteği talebi, süre aşımı
  işlendiği anda (`GET /actions` taraması ya da karar denemesi) `failed` yapar; zamanlanmış süpürücü yok.

## Sorun yönetimi (ITIL 4 Problem Management)

Modüller: `models.Problem` (+ `problem_incidents`, `problem_cis`, `problem_changes`), `services/problems.py`,
`services/problem_schemas.py`, `agents/problem.py`, `tools/builtin/problem.py`, `api/problems.py`; göç
`0009_problem_management.py`. Testler: `tests/test_problems.py`, `tests/test_problem_migration.py`, kapı
`tests/test_gate_no_unapproved_risk.py` bölüm 5.

**Model.** `Problem`: `number` (`PRB0000001`), `title`, `description`, `state`, `priority` (P1-P5),
`category` / `assignment_group` (taksonomi), `root_cause`, `rca` (kesin, insan), `rca_draft` (taslak;
yapay zekâ/araç), `workaround`, `known_error_article_id` (KEDB makalesi, `KnowledgeArticle.is_known_error`),
`incident_ids`, `affected_ci_ids`, `change_ids` (kalıcı çözüm RFC'leri), `detected_by` (`ai:problem` ya da
insan), `created_by`, `resolution`, `resolved_at`, `closed_at`, `version`. RCA şeması: `{"method": "5_why" |
"timeline", "steps": [{"question"?, "answer", "at"?}], "root_cause"?}` (5 Neden: her adımda soru, en fazla
10; zaman çizelgesi: her adımda saat dilimli `at`).

**Durum makinesi** (`services.problems.transition`): `new → investigating | closed`;
`investigating → known_error | resolved | closed`; `known_error → investigating | resolved | closed`;
`resolved → investigating | closed`. `known_error` kök neden ya da geçici çözüm, `resolved` kesin kök
neden ister; çözülmemiş sorun yalnızca kapanış notuyla (ör. yinelenen kayıt) kapanır. **Yapay zekâ aktörü
(`ai:`) çözemez/kapatamaz, kesin RCA yazamaz/kabul edemez, bilinen hatayı yayımlayamaz**
(`ProblemForbiddenError`). Her yazma sürümü artırır (`expected_version` / `If-Match` → 409) ve denetime
yazılır (`problem.create|update|link_*|unlink_incident|rca_draft|rca|rca_accept|transition|known_error.publish`).

**Ajan** (`agents/problem.py`, aktör `ai:problem`): doğrudan yazmaz; her yazma `execute_tool` boru
hattından geçer (politika + `AIDecision` + denetim; AUTO_APPLIED değilse `ActionRequest`).

- `detect_trends(session, window_days)` — deterministik, salt okuma. Son N gündeki olaylar (`kind=incident`,
  iptal hariç; kapanmamış soruna ya da bekleyen `problem.create` isteğine bağlı olanlar hariç; en yeni 2000):
  `ci_category` kümesi = aynı CI + aynı kategori, en az 3 olay, güven `0.70 + 0.05 × (boyut − 3)` (en fazla
  0.95; kategorisiz −0.10); `similar_title` kümesi = bilgi bankası BM25 belirteçleyicisi (`knowledge.tokenize`)
  + 5 harf önek kökü üzerinde Jaccard ≥ 0.6, lider kümeleme, en az 3 olay, güven `0.40 + 0.50 × ort.
  benzerlik` (en fazla 0.90). Bir CI kümesinin alt kümesi olan başlık kümesi atılır.
- `propose_problems(...)` — her küme için `problem.create` (idempotency anahtarı = küme imzası).
- `draft_rca(session, problem_id, llm)` — bağlı olayların maskeli metninden (en fazla 20 olay, alan başına
  500 karakter) LLM 5 Neden taslağı (`serviszeka.llm`, akıl yürütme modeli) → `problem.record_rca` yalnızca
  `rca_draft`. Geçersiz LLM yanıtı `RCADraftError` (hiçbir şey yazılmaz; API 502). Fake LLM yanıtı var.
- `propose_fix(session, problem_id)` — `problem.propose_fix` ile taslak RFC (güven: kesin kök neden 0.85,
  yalnızca taslak 0.60, yok 0.40).

**Araçlar** (8; katalog toplamı 41):

| Araç | Sınıf | Not |
|---|---|---|
| `problem.search`, `problem.detect_trends` | R0 | |
| `problem.create` | R1 | onaylı istekte `detected_by` = isteği açan |
| `problem.link_incidents` | R1 | |
| `problem.record_rca` | R1 | yalnızca `rca_draft`; `rca` verilmezse LLM taslak üretir |
| `problem.propose_fix` | R1 | değişiklik servisiyle taslak RFC + bağ (roller agent/change_manager) |
| `problem.close` | R2 | kapanış notu zorunlu |
| `known_error.publish_workaround` | R2 | geçici çözümü KEDB makalesi olarak açar/günceller ve yayımlar; sorun `known_error` |

`problem_id` alan araçlar, sorun yoksa istek açılmadan 404 verir (`risk_fn` ön koşulu, sınıf değişmez).
İnsan rolleri agent/approver (admin hepsi).

**HTTP** (`/problems`, agent / approver / admin; requester ve `ai_agent` belirteci 403):
`GET /problems?state=&q=&incident_id=&ci_id=`, `POST /problems`, `GET|PATCH /problems/{id}` (`ETag` /
`If-Match`), `POST /problems/{id}/transition {to_state, note}`, `POST /problems/{id}/incidents`,
`DELETE /problems/{id}/incidents/{incident_id}`, `POST /problems/{id}/cis`, `POST /problems/{id}/changes`,
`PUT /problems/{id}/rca` (kesin RCA, insan), `POST /problems/{id}/rca/accept` (taslağı kabul, insan),
`POST /problems/{id}/known-error/publish` (insan), yapay zekâ uçları (aktör `ai:problem`, sonuç
`AgentResultRead`): `POST /problems/trends` (salt okuma), `POST /problems/trends/propose`,
`POST /problems/{id}/rca/draft`, `POST /problems/{id}/fix`.

Portal: `/problemler` (liste + eğilim analizi), `/problemler/:id` (kanıt, RCA düzenleyici, geçici çözüm/bilinen hata,
kalıcı çözüm, durum). Açık: eylem isteğinde sorun bağı sütunu yok
(`ActionRequest` yalnızca olay/değişiklik/talep bağlar; sorun kimliği `args`'ta).
