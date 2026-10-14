import { useEffect, useId, useMemo, useState, type FormEvent } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import type { CIClass, CIEnvironment, CIListQuery, CIStatus } from '@/api/types';
import {
  Button,
  EmptyState,
  ErrorState,
  Select,
  Spinner,
  TextInput,
  optionsFrom,
} from '@/components';
import {
  CI_CLASS_LABELS,
  CI_ENVIRONMENT_LABELS,
  CI_STATUS_LABELS,
  criticalityLabel,
} from '@/lib/labels';
import { groupLabel } from '@/lib/taxonomy';
import { usePageTitle } from '@/lib/usePageTitle';
import styles from './Cmdb.module.css';
import { useCIList } from './hooks';

const pick = <K extends string>(labels: Record<K, string>, value: string | null): K | undefined =>
  value && value in labels ? (value as K) : undefined;

function parseParams(params: URLSearchParams): CIListQuery {
  const q = params.get('q')?.trim();
  return {
    ...(q ? { q } : {}),
    ci_class: pick<CIClass>(CI_CLASS_LABELS, params.get('sinif')),
    environment: pick<CIEnvironment>(CI_ENVIRONMENT_LABELS, params.get('ortam')),
    status: pick<CIStatus>(CI_STATUS_LABELS, params.get('durum')),
    limit: 200,
  };
}

/** /cmdb — yapılandırma öğesi arama ve listesi (sahibi: cmdb). GET /cmdb/cis */
export default function CmdbListPage() {
  usePageTitle('CMDB');
  const [params, setParams] = useSearchParams();
  const query = useMemo(() => parseParams(params), [params]);
  const list = useCIList(query);
  const headingId = useId();
  const [term, setTerm] = useState(query.q ?? '');
  useEffect(() => {
    setTerm(query.q ?? '');
  }, [query.q]);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    setParam('q', term.trim());
  };
  const rows = list.data ?? [];

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>CMDB</h1>
          <p className="muted" style={{ margin: 0 }}>
            Yapılandırma öğeleri (CI), ilişkileri ve “bu değişirse kim etkilenir” analizi.
          </p>
        </div>
      </header>
      <section className="card" aria-label="Arama ve süzgeçler">
        <form className={styles.filters} role="search" onSubmit={submit}>
          <div className={styles.search}>
            <TextInput
              label="Ada göre ara"
              type="search"
              value={term}
              onChange={(e) => {
                setTerm(e.target.value);
              }}
            />
            <Button type="submit" variant="secondary">
              Ara
            </Button>
          </div>
          <Select
            label="Sınıf"
            placeholder="Tümü"
            options={optionsFrom(CI_CLASS_LABELS)}
            value={query.ci_class ?? ''}
            onChange={(e) => {
              setParam('sinif', e.target.value);
            }}
          />
          <Select
            label="Ortam"
            placeholder="Tümü"
            options={optionsFrom(CI_ENVIRONMENT_LABELS)}
            value={query.environment ?? ''}
            onChange={(e) => {
              setParam('ortam', e.target.value);
            }}
          />
          <Select
            label="Durum"
            placeholder="Tümü"
            options={optionsFrom(CI_STATUS_LABELS)}
            value={query.status ?? ''}
            onChange={(e) => {
              setParam('durum', e.target.value);
            }}
          />
        </form>
      </section>
      <section className="stack" aria-labelledby={headingId}>
        <h2 id={headingId} className={styles.sectionTitle}>
          Yapılandırma öğeleri
          {list.data && <span className="muted"> ({rows.length})</span>}
        </h2>
        {list.isError ? (
          <ErrorState error={list.error} onRetry={() => void list.refetch()} />
        ) : list.isPending ? (
          <Spinner block label="CI’lar yükleniyor…" />
        ) : rows.length === 0 ? (
          <EmptyState
            title="Yapılandırma öğesi bulunamadı"
            description="Aramayı ya da süzgeçleri değiştirin."
            headingLevel={3}
          />
        ) : (
          <div className={styles.tableWrap}>
            <table className={styles.table} aria-busy={list.isFetching}>
              <thead>
                <tr>
                  <th scope="col">Ad</th>
                  <th scope="col">Sınıf</th>
                  <th scope="col">Ortam</th>
                  <th scope="col">Kritiklik</th>
                  <th scope="col">Sahip grup</th>
                  <th scope="col">Durum</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((ci) => (
                  <tr key={ci.id}>
                    <td>
                      <Link to={`/cmdb/${encodeURIComponent(ci.id)}`}>{ci.name}</Link>
                    </td>
                    <td>{CI_CLASS_LABELS[ci.ci_class]}</td>
                    <td>{CI_ENVIRONMENT_LABELS[ci.environment]}</td>
                    <td>{criticalityLabel(ci.criticality)}</td>
                    <td>{ci.owner_group ? groupLabel(ci.owner_group) : '—'}</td>
                    <td>{CI_STATUS_LABELS[ci.status]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  );
}
