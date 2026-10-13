import { useSearchParams } from 'react-router-dom';
import type { InboxOutcome } from '@/api/types';
import { useAuth } from '@/auth/useAuth';
import { ActionInbox } from '@/features/actions/ActionInbox';
import { Spinner } from '@/components';
import { usePageTitle } from '@/lib/usePageTitle';
import { DecisionInbox } from './components/DecisionInbox';
import { SyncConflicts } from './components/SyncConflicts';
import { Tabs, type TabItem } from './components/Tabs';

type TabKey = 'onay' | 'oneri' | 'yonlendirilen' | 'cakisma' | 'eylem';

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
  { key: 'eylem', label: 'Eylem onayları', heading: 'Eylem onayları' },
];

/** Olay karar kutusu ve senkron çakışmaları yalnızca approver; eylem onayları approver/change_manager */
const DECISION_TABS: ReadonlySet<TabKey> = new Set(['onay', 'oneri', 'yonlendirilen', 'cakisma']);

/**
 * /onaylar — olaylar arası onay kutusu + senkron çakışmaları (approver) ve eylem onayları
 * (ActionRequest; approver / change_manager).
 */
export default function ApprovalsPage() {
  usePageTitle('Onaylar');
  const { hasRole, status } = useAuth();
  // Roller /me ile gelir: gelene kadar sekmeler gösterilir ama içerik (ve istekleri) beklenir
  const ready = status === 'authenticated';
  const canDecisions = !ready || hasRole('approver');
  const tabs = canDecisions ? TABS : TABS.filter((t) => !DECISION_TABS.has(t.key));
  const fallback: TabKey = canDecisions ? 'onay' : 'eylem';
  const [params, setParams] = useSearchParams();
  const raw = params.get('sekme');
  const active: TabKey = tabs.find((t) => t.key === raw)?.key ?? fallback;
  const tab = tabs.find((t) => t.key === active) ?? tabs[0];

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
        items={tabs}
        active={active}
        onChange={(key) => {
          setParams(key === fallback ? {} : { sekme: key }, { replace: true });
        }}
      >
        {!ready ? (
          <Spinner block label="Oturum yükleniyor…" />
        ) : tab?.key === 'eylem' ? (
          <ActionInbox focusId={params.get('istek')} />
        ) : tab?.outcome ? (
          <DecisionInbox key={tab.outcome} outcome={tab.outcome} heading={tab.heading} />
        ) : (
          <SyncConflicts />
        )}
      </Tabs>
    </div>
  );
}
