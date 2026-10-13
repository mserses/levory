import { useId, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { isApiError } from '@/api/errors';
import type { ConfigurationItem } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { ErrorState, Select, Spinner, StatusBadge } from '@/components';
import { ChangeStateBadge, ChangeTypeBadge } from '@/features/changes/components/ChangeBadges';
import { formatDateTime } from '@/lib/format';
import {
  CI_CLASS_LABELS,
  CI_ENVIRONMENT_LABELS,
  CI_RELATIONSHIP_LABELS,
  CI_STATUS_LABELS,
  criticalityLabel,
} from '@/lib/labels';
import { groupLabel } from '@/lib/taxonomy';
import { usePageTitle } from '@/lib/usePageTitle';
import styles from './Cmdb.module.css';
import { ImpactGraph } from './components/ImpactGraph';
import {
  INCIDENT_SCAN_LIMIT,
  useCI,
  useCIImpact,
  useCIs,
  useLinkedChanges,
  useLinkedIncidents,
  useRelationships,
} from './hooks';

const DEPTH_OPTIONS = [1, 2, 3, 4, 5, 6, 8, 10].map((d) => ({
  value: String(d),
  label: d === 1 ? 'Yalnızca doğrudan' : `${d} seviye`,
}));

/** /cmdb/:id — CI ayrıntısı, ilişkiler, etki grafiği, bağlı kayıtlar (sahibi: cmdb) */
export default function CIDetailPage() {
  const { id = '' } = useParams();
  const query = useCI(id);
  const ci = query.data;
  usePageTitle(ci ? `${ci.name} · CMDB` : 'Yapılandırma öğesi');
  if (query.isPending) return <Spinner block label="Yapılandırma öğesi yükleniyor…" />;
  if (query.isError || !ci) {
    const err = query.error;
    const notFound = isApiError(err) && err.isNotFound;
    return (
      <div className="page">
        <Link to="/cmdb">← CMDB’ye dön</Link>
        <h1 className="visually-hidden">Yapılandırma öğesi</h1>
        <ErrorState
          error={err}
          title={notFound ? 'Yapılandırma öğesi bulunamadı' : undefined}
          onRetry={notFound ? undefined : () => void query.refetch()}
        />
      </div>
    );
  }
  // key: başka CI'a geçince derinlik vb. yerel durum sıfırlansın
  return <CIDetail key={ci.id} ci={ci} />;
}

function CIDetail({ ci }: { ci: ConfigurationItem }) {
  const fieldsId = useId();
  const attributes = Object.entries(ci.attributes);
  const refs = Object.entries(ci.external_refs);
  return (
    <div className="page">
      <header className="stack" style={{ gap: 'var(--space-2)' }}>
        <Link to="/cmdb">← CMDB’ye dön</Link>
        <h1 style={{ margin: 0 }}>{ci.name}</h1>
      </header>
      <div className={styles.grid}>
        <div className="stack">
          <section className="card stack" aria-labelledby={fieldsId}>
            <h2 id={fieldsId} className={styles.sectionTitle}>
              Yapılandırma öğesi
            </h2>
            {ci.description && (
              <p className="prewrap" style={{ margin: 0 }}>
                {ci.description}
              </p>
            )}
            <dl className={styles.fields}>
              <dt>Sınıf</dt>
              <dd>{CI_CLASS_LABELS[ci.ci_class]}</dd>
              <dt>Ortam</dt>
              <dd>{CI_ENVIRONMENT_LABELS[ci.environment]}</dd>
              <dt>Kritiklik</dt>
              <dd>{criticalityLabel(ci.criticality)}</dd>
              <dt>Sahip grup</dt>
              <dd>{ci.owner_group ? groupLabel(ci.owner_group) : '—'}</dd>
              <dt>Durum</dt>
              <dd>{CI_STATUS_LABELS[ci.status]}</dd>
              <dt>Güncellendi</dt>
              <dd>{formatDateTime(ci.updated_at)}</dd>
              {attributes.map(([key, value]) => (
                <KeyValue key={`a-${key}`} name={key} value={value} />
              ))}
              {refs.map(([key, value]) => (
                <KeyValue key={`r-${key}`} name={`Dış kayıt: ${key}`} value={value} />
              ))}
            </dl>
          </section>
          <Relationships ci={ci} />
          <LinkedChanges ciId={ci.id} />
          <LinkedIncidents ciId={ci.id} />
        </div>
        <ImpactSection ci={ci} />
      </div>
    </div>
  );
}

/** Serbest öznitelikler: düz metin (nesneler JSON olarak) */
function KeyValue({ name, value }: { name: string; value: unknown }) {
  const text =
    typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean'
      ? String(value)
      : JSON.stringify(value);
  return (
    <>
      <dt>{name}</dt>
      <dd className="prewrap">{text}</dd>
    </>
  );
}

function Relationships({ ci }: { ci: ConfigurationItem }) {
  const headingId = useId();
  const rels = useRelationships(ci.id);
  const otherIds = [
    ...new Set((rels.data ?? []).map((r) => (r.source_id === ci.id ? r.target_id : r.source_id))),
  ];
  const others = useCIs(otherIds);
  const nameOf = (id: string) => others[otherIds.indexOf(id)]?.data?.name;
  const end = (id: string) => {
    if (id === ci.id) return <strong>{ci.name}</strong>;
    const name = nameOf(id);
    return <Link to={`/cmdb/${encodeURIComponent(id)}`}>{name ?? 'Yükleniyor…'}</Link>;
  };
  return (
    <section className="card stack" aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.sectionTitle}>
        İlişkiler
      </h2>
      {rels.isError ? (
        <ErrorState error={rels.error} onRetry={() => void rels.refetch()} />
      ) : rels.isPending ? (
        <Spinner label="İlişkiler yükleniyor…" />
      ) : rels.data.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          Bu CI için kayıtlı ilişki yok.
        </p>
      ) : (
        <div className={styles.tableWrap}>
          <table className={styles.table}>
            <caption className="visually-hidden">İlişkiler: kaynak, ilişki türü, hedef</caption>
            <thead>
              <tr>
                <th scope="col">Kaynak</th>
                <th scope="col">İlişki</th>
                <th scope="col">Hedef</th>
              </tr>
            </thead>
            <tbody>
              {rels.data.map((r) => (
                <tr key={r.id}>
                  <td>{end(r.source_id)}</td>
                  <td>{CI_RELATIONSHIP_LABELS[r.type]}</td>
                  <td>{end(r.target_id)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function ImpactSection({ ci }: { ci: ConfigurationItem }) {
  const headingId = useId();
  const [depth, setDepth] = useState(5);
  const impact = useCIImpact(ci.id, depth);
  const data = impact.data;
  return (
    <section className="card stack" aria-labelledby={headingId}>
      <div className={styles.panelHead}>
        <h2 id={headingId} className={styles.sectionTitle}>
          Etki analizi
        </h2>
        <Select
          label="Derinlik"
          options={DEPTH_OPTIONS}
          value={String(depth)}
          onChange={(e) => {
            setDepth(Number(e.target.value));
          }}
        />
      </div>
      <p className="muted" style={{ margin: 0 }}>
        Bu CI değişirse etkilenebilecek öğeler (yukarı yönlü bağımlılar).
      </p>
      {impact.isError ? (
        <ErrorState error={impact.error} onRetry={() => void impact.refetch()} />
      ) : !data ? (
        <Spinner label="Etki hesaplanıyor…" />
      ) : (
        <>
          <p style={{ margin: 0 }}>
            {`Etkilenen CI: ${data.affected.length} · En yüksek kritiklik: ${criticalityLabel(data.highest_criticality)}`}
            {data.business_services.length > 0 &&
              ` · İş hizmetleri: ${data.business_services.map((s) => s.name).join(', ')}`}
          </p>
          {data.affected.length === 0 ? (
            <p className="muted" style={{ margin: 0 }}>
              Bu CI’a bağımlı başka öğe bulunmadı.
            </p>
          ) : (
            <>
              <ImpactGraph
                impact={data}
                roots={[ci]}
                title={`Etki grafiği: ${ci.name} değişirse etkilenenler`}
              />
              <div className={styles.tableWrap}>
                <table className={styles.table} aria-busy={impact.isFetching}>
                  <caption>Etkilenen öğeler (tablo görünümü)</caption>
                  <thead>
                    <tr>
                      <th scope="col">CI</th>
                      <th scope="col">Sınıf</th>
                      <th scope="col">Kritiklik</th>
                      <th scope="col">Uzaklık</th>
                      <th scope="col">Yol</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.affected.map((a) => (
                      <tr key={a.ci.id}>
                        <td>
                          <Link to={`/cmdb/${encodeURIComponent(a.ci.id)}`}>{a.ci.name}</Link>
                        </td>
                        <td>{CI_CLASS_LABELS[a.ci.ci_class]}</td>
                        <td>{criticalityLabel(a.ci.criticality)}</td>
                        <td>{a.depth === 1 ? 'Doğrudan' : `${a.depth}. derece`}</td>
                        <td>
                          {a.path
                            .map((id) =>
                              id === ci.id
                                ? ci.name
                                : (data.affected.find((x) => x.ci.id === id)?.ci.name ?? '…'),
                            )
                            .join(' → ')}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </>
      )}
    </section>
  );
}

function LinkedChanges({ ciId }: { ciId: string }) {
  const headingId = useId();
  const changes = useLinkedChanges(ciId);
  return (
    <section className="card stack" aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.sectionTitle}>
        Bağlı değişiklikler
      </h2>
      {changes.isError ? (
        <ErrorState error={changes.error} onRetry={() => void changes.refetch()} />
      ) : changes.isPending ? (
        <Spinner label="Değişiklikler yükleniyor…" />
      ) : changes.data.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          Bu CI’ı etkileyen değişiklik yok.
        </p>
      ) : (
        <ul className={styles.linkList}>
          {changes.data.map((c) => (
            <li key={c.id}>
              <Link to={`/degisiklikler/${encodeURIComponent(c.id)}`}>
                {c.number} · {c.title}
              </Link>
              <ChangeTypeBadge type={c.change_type} />
              <ChangeStateBadge state={c.state} />
              {c.planned_start && <span className="muted">{formatDateTime(c.planned_start)}</span>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function LinkedIncidents({ ciId }: { ciId: string }) {
  const headingId = useId();
  const { hasRole } = useAuth();
  // GET /incidents agent/approver içindir; cab/change_manager olayları göremez
  const allowed = hasRole('agent', 'approver');
  const incidents = useLinkedIncidents(ciId, allowed);
  return (
    <section className="card stack" aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.sectionTitle}>
        Bağlı olaylar
      </h2>
      {!allowed ? (
        <p className="muted" style={{ margin: 0 }}>
          Olay kayıtlarını görme yetkiniz yok.
        </p>
      ) : incidents.isError ? (
        <ErrorState error={incidents.error} onRetry={() => void incidents.refetch()} />
      ) : incidents.isPending ? (
        <Spinner label="Olaylar yükleniyor…" />
      ) : incidents.data.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>
          Son {INCIDENT_SCAN_LIMIT} kayıt içinde bu CI’a bağlı olay yok.
        </p>
      ) : (
        <ul className={styles.linkList}>
          {incidents.data.map((i) => (
            <li key={i.id}>
              <Link to={`/kayitlar/${encodeURIComponent(i.id)}`}>
                {i.number} · {i.title}
              </Link>
              <StatusBadge status={i.status} />
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
