/** Yönetim ekranları etiketleri. Bilinmeyen değerler ham kodla (kod yazı tipinde) gösterilir. */
import type { ExternalSystem } from '@/api/types';
import type { Tone } from '@/components';

export const AUDIT_ACTION_LABELS: Record<string, string> = {
  'incident.create': 'Kayıt oluşturuldu',
  'incident.update': 'Kayıt güncellendi',
  'ai_decision.triage': 'Yapay zekâ triage kararı',
  'ai_decision.approve': 'Yapay zekâ önerisi onaylandı',
  'ai_decision.approve_rejected': 'Onay reddedildi (bayat öneri)',
  'knowledge.create': 'Makale oluşturuldu',
  'knowledge.update': 'Makale güncellendi',
  'knowledge.publish': 'Makale yayınlandı',
  'knowledge.retire': 'Makale arşive kaldırıldı',
  'sync.incident_imported': 'Dış sistemden kayıt alındı',
  'sync.incident_updated': 'Dış sistemden kayıt güncellendi',
  'sync.remote_created': 'Dış sistemde kayıt açıldı',
  'sync.conflict_resolved': 'Senkron çakışması çözüldü',
  'notification.sent': 'Nöbetçi bildirimi gönderildi',
  'notification.failed': 'Nöbetçi bildirimi gönderilemedi',
};

export const ENTITY_TYPE_LABELS: Record<string, string> = {
  incident: 'Kayıt',
  ai_decision: 'Yapay zekâ kararı',
  knowledge_article: 'Makale',
};

export { SYSTEM_LABELS, systemLabel } from '@/lib/labels';

export const SYSTEMS: readonly ExternalSystem[] = ['servicenow', 'jira_sm'];

export const SYNC_STATUS_LABELS: Record<string, { label: string; tone: Tone }> = {
  ok: { label: 'Başarılı', tone: 'success' },
  skipped: { label: 'Atlandı', tone: 'neutral' },
  error: { label: 'Hata', tone: 'danger' },
  conflict: { label: 'Çakışma', tone: 'warning' },
};
