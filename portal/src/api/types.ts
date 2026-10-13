/**
 * Üretilmiş OpenAPI tiplerine kısa adlar. `schema.d.ts` elle düzenlenmez:
 * `npm run sync:api` (backend'den openapi.json + tip üretimi) ile yenilenir.
 */
import type { components, operations } from './schema';

type Schemas = components['schemas'];

/**
 * openapi-typescript, varsayılanı olan alanları zorunlu üretir (yanıtlar için doğru).
 * İstek gövdelerinde sunucu varsayılanı olan alanları burada isteğe bağlı yapın.
 */
type WithOptional<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>;

export type Incident = Schemas['IncidentRead'];
export type IncidentCreate = WithOptional<Schemas['IncidentCreateRequest'], 'description'>;
export type IncidentPatch = Schemas['IncidentPatch'];
export type AIDecision = Schemas['AIDecisionRead'];
export type DecisionInboxItem = Schemas['DecisionInboxItem'];
export type SLAAlert = Schemas['SLAAlertRead'];
export type Me = Schemas['MeRead'];
export type Article = Schemas['ArticleOut'];
export type ArticleCreate = WithOptional<Schemas['ArticleCreate'], 'is_known_error' | 'state'>;
export type ArticleUpdate = Schemas['ArticleUpdate'];
export type SearchResponse = Schemas['SearchResponse'];
export type SearchHit = Schemas['SearchHitOut'];
export type AuditPage = Schemas['AuditPage'];
export type AuditEntry = Schemas['AuditEntryOut'];
export type AuditVerify = Schemas['VerifyOut'];
export type SystemStatus = Schemas['SystemStatus'];
export type SyncEvent = Schemas['SyncEventOut'];

// Faz 2: değişiklik yönetimi + CMDB
export type Change = Schemas['ChangeRead'];
export type ChangeCreate = WithOptional<
  Schemas['ChangeCreate'],
  'description' | 'implementation_plan' | 'backout_plan' | 'test_plan'
>;
export type ChangePatch = Schemas['ChangePatch'];
export type ChangeTransition = Omit<Schemas['TransitionRequest'], 'expected_version'>;
export type ChangeApproval = Schemas['ApprovalRead'];
export type ChangeApprovalCreate = WithOptional<Schemas['ApprovalCreate'], 'comment'>;
export type ChangeApprovalList = Schemas['ApprovalListRead'];
export type ChangeApprovalStatus = Schemas['ApprovalStatusRead'];
export type ChangeTemplate = Schemas['TemplateRead'];
export type FreezeWindow = Schemas['FreezeWindowRead'];
export type ScheduleConflict = Schemas['ScheduleConflictRead'];
export type ChangeCalendar = Schemas['CalendarRead'];
export type ChangeState = Schemas['ChangeState'];
export type ChangeType = Schemas['ChangeType'];
export type RiskLevel = Schemas['RiskLevel'];
export type ApprovalDecision = Schemas['ApprovalDecision'];
export type ConfigurationItem = Schemas['CIRead'];
export type CIRelationship = Schemas['CIRelationshipRead'];
export type ImpactAnalysis = Schemas['ImpactRead'];
export type ImpactedCI = Schemas['ImpactedCIRead'];
export type CIClass = Schemas['CIClass'];
export type CIEnvironment = Schemas['CIEnvironment'];
export type CIStatus = Schemas['CIStatus'];
export type CIRelationshipType = Schemas['CIRelationshipType'];

export type Priority = Schemas['Priority'];
export type IncidentStatus = Schemas['IncidentStatus'];
export type Impact = Schemas['Impact'];
export type Urgency = Schemas['Urgency'];
export type TicketKind = Schemas['TicketKind'];
export type OnHoldReason = Schemas['OnHoldReason'];
export type DecisionOutcome = Schemas['DecisionOutcome'];
export type InboxOutcome = Schemas['InboxOutcome'];
export type ExternalSystem = Schemas['ExternalSystem'];

type QueryOf<Op extends keyof operations> = operations[Op] extends {
  parameters: { query?: infer Q };
}
  ? NonNullable<Q>
  : never;

export type IncidentListQuery = QueryOf<'list_incidents_incidents_get'>;
export type DecisionInboxQuery = QueryOf<'decision_inbox_incidents_decisions_get'>;
export type ArticleListQuery = QueryOf<'list_articles_knowledge_articles_get'>;
export type ArticleSearchQuery = QueryOf<'search_articles_knowledge_search_get'>;
export type AuditQuery = QueryOf<'list_audit_audit_get'>;
export type ChangeListQuery = QueryOf<'list_changes_changes_get'>;
export type CIListQuery = QueryOf<'list_cis_cmdb_cis_get'>;
