import { useQuery } from '@tanstack/react-query';
import { useId, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { catalogApi, queryKeys, type CatalogItem } from '@/api';
import { Badge, EmptyState, ErrorState, Select, Spinner, TextInput } from '@/components';
import { CATALOG_APPROVAL_LABELS } from '@/lib/labels';
import { usePageTitle } from '@/lib/usePageTitle';
import { matchesQuery } from './search';
import styles from './Catalog.module.css';

const LIST_QUERY = {} as const;
const collator = new Intl.Collator('tr-TR');

/** /katalog — hizmet kataloğu (sahibi: catalog). GET /catalog/items */
export default function CatalogPage() {
  usePageTitle('Hizmet kataloğu');
  const [search, setSearch] = useState('');
  const [category, setCategory] = useState('');

  const query = useQuery({
    queryKey: queryKeys.catalog.list(LIST_QUERY),
    queryFn: ({ signal }) => catalogApi.list(LIST_QUERY, signal),
  });

  const items = useMemo(() => query.data ?? [], [query.data]);
  const categories = useMemo(
    () => [...new Set(items.map((i) => i.category))].sort((a, b) => collator.compare(a, b)),
    [items],
  );
  const visible = useMemo(
    () =>
      items.filter(
        (i) =>
          (!category || i.category === category) &&
          matchesQuery(search, i.name, i.description, i.category),
      ),
    [items, category, search],
  );
  const groups = useMemo(() => {
    const map = new Map<string, CatalogItem[]>();
    for (const item of visible) {
      const list = map.get(item.category) ?? [];
      list.push(item);
      map.set(item.category, list);
    }
    return [...map.entries()]
      .sort(([a], [b]) => collator.compare(a, b))
      .map(
        ([name, list]) => [name, list.sort((a, b) => collator.compare(a.name, b.name))] as const,
      );
  }, [visible]);

  let content;
  if (query.isPending) {
    content = <Spinner block label="Katalog yükleniyor…" showLabel />;
  } else if (query.isError) {
    content = (
      <ErrorState
        error={query.error}
        title="Katalog yüklenemedi"
        onRetry={() => void query.refetch()}
      />
    );
  } else if (items.length === 0) {
    content = (
      <EmptyState
        icon="🗂"
        title="Katalogda henüz hizmet yok"
        description="Yöneticiniz katalog öğelerini tanımladığında burada listelenecek. Bir sorun yaşıyorsanız “Yeni talep” ile bildirebilirsiniz."
      />
    );
  } else {
    content = (
      <>
        <div className={styles.toolbar}>
          <TextInput
            type="search"
            label="Katalogda ara"
            placeholder="ör. şifre, VPN, posta kutusu"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
            }}
          />
          <Select
            label="Kategori"
            placeholder="Tüm kategoriler"
            options={categories.map((c) => ({ value: c, label: c }))}
            value={category}
            onChange={(e) => {
              setCategory(e.target.value);
            }}
          />
        </div>
        <p className={styles.count} aria-live="polite">
          {visible.length} hizmet gösteriliyor
        </p>
        {groups.length === 0 ? (
          <EmptyState
            icon="🔍"
            title="Aramanızla eşleşen hizmet yok"
            description="Farklı bir sözcük deneyin ya da kategori seçimini kaldırın."
          />
        ) : (
          groups.map(([name, list]) => <CategoryGroup key={name} name={name} items={list} />)
        )}
      </>
    );
  }

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Hizmet kataloğu</h1>
          <p className="muted" style={{ margin: 0 }}>
            İhtiyacınız olan hizmeti seçin. Standart talepler çoğunlukla insan müdahalesi olmadan
            dakikalar içinde karşılanır.
          </p>
        </div>
      </header>
      {content}
    </div>
  );
}

function CategoryGroup({ name, items }: { name: string; items: readonly CatalogItem[] }) {
  const id = useId();
  return (
    <section className={styles.group} aria-labelledby={id}>
      <h2 id={id} className={styles.groupHeading}>
        {name}
      </h2>
      <ul className={styles.grid}>
        {items.map((item) => (
          <li key={item.id}>
            <CatalogCard item={item} />
          </li>
        ))}
      </ul>
    </section>
  );
}

function CatalogCard({ item }: { item: CatalogItem }) {
  const id = useId();
  return (
    <article className={styles.card} aria-labelledby={id}>
      <h3 id={id} className={styles.cardTitle}>
        <Link to={`/katalog/${encodeURIComponent(item.key)}`}>{item.name}</Link>
      </h3>
      <p className={styles.cardText}>{item.description}</p>
      <div className={styles.badges}>
        {item.standard && (
          <Badge tone="success" dot>
            Standart talep
          </Badge>
        )}
        {item.approval_policy !== 'none' && (
          <Badge tone="warning">{CATALOG_APPROVAL_LABELS[item.approval_policy]}</Badge>
        )}
      </div>
    </article>
  );
}
