import { useSearchParams } from 'react-router-dom';
import type { InboxOutcome } from '@/api/types';
import { usePageTitle } from '@/lib/usePageTitle';
import { DecisionInbox } from './components/DecisionInbox';
import { SyncConflicts } from './components/SyncConflicts';
import { Tabs, type TabItem } from './components/Tabs';

type TabKey = 'onay' | 'oneri' | 'yonlendirilen' | 'cakisma';

const TABS: readonly (TabItem<TabKey> & { outcome?: InboxOutcome; heading: string })[] = [
  {
    key: 'onay',
    label: 'Onay bekleyen',
    outcome: 'needs_approval',
    heading: 'Onay bekleyen öneriler',
  },
  { key: 'oneri', label: 'Öneriler', outcome: 'suggested', heading: 'Yapay zekâ önerileri' },
  {
    key: 'yonlendirilen',
    label: 'İnsana yönlendirilen',
    outcome: 'escalated',
    heading: 'İnsana yönlendirilen kararlar',
  },
  { key: 'cakisma', label: 'Senkron çakışmaları', heading: 'Senkron çakışmaları' },
];

const isTabKey = (v: string | null): v is TabKey => TABS.some((t) => t.key === v);

/** /onaylar — olaylar arası onay kutusu + senkron çakışmaları (rol: approver). */
export default function ApprovalsPage() {
  usePageTitle('Onaylar');
  const [params, setParams] = useSearchParams();
  const raw = params.get('sekme');
  const active: TabKey = isTabKey(raw) ? raw : 'onay';
  const tab = TABS.find((t) => t.key === active) ?? TABS[0];

  return (
    <div className="page">
      <header className="page-header">
        <div>
          <h1>Onaylar</h1>
          <p className="muted" style={{ margin: 0 }}>
            Yapay zekâ önerir, siz onaylarsınız. Her öneriyi gerekçesi, güveni ve kaynaklarıyla
            inceleyin.
          </p>
        </div>
      </header>
      <Tabs
        label="Onay kutusu bölümleri"
        items={TABS}
        active={active}
        onChange={(key) => {
          setParams(key === 'onay' ? {} : { sekme: key }, { replace: true });
        }}
      >
        {tab?.outcome ? (
          <DecisionInbox key={tab.outcome} outcome={tab.outcome} heading={tab.heading} />
        ) : (
          <SyncConflicts />
        )}
      </Tabs>
    </div>
  );
}
