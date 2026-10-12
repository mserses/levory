import { useState, type FormEvent } from 'react';
import { errorMessage, isApiError } from '@/api/errors';
import type { Impact, Incident, TicketKind, Urgency } from '@/api/types';
import { Badge, Button, Select, TextArea, TextInput, optionsFrom } from '@/components';
import { IMPACT_LABELS, KIND_LABELS, PRIORITY_LABELS, URGENCY_LABELS } from '@/lib/labels';
import { taxonomyOptions } from '@/lib/taxonomy';
import { calculatePriority } from '../rules';
import styles from './forms.module.css';
import type { Patch, SaveFn } from './types';

interface Draft {
  title: string;
  description: string;
  kind: TicketKind;
  caller: string;
  category: string;
  service: string;
  impact: Impact;
  urgency: Urgency;
  assignment_group: string;
  assignee: string;
}

const LABELS: Record<keyof Draft, string> = {
  title: 'Başlık',
  description: 'Açıklama',
  kind: 'Tür',
  caller: 'Arayan',
  category: 'Kategori',
  service: 'Hizmet',
  impact: 'Etki',
  urgency: 'Aciliyet',
  assignment_group: 'Atama grubu',
  assignee: 'Atanan',
};

const NULLABLE: ReadonlySet<keyof Draft> = new Set([
  'category',
  'service',
  'assignment_group',
  'assignee',
]);

function toDraft(i: Incident): Draft {
  return {
    title: i.title,
    description: i.description,
    kind: i.kind,
    caller: i.caller,
    category: i.category ?? '',
    service: i.service ?? '',
    impact: i.impact,
    urgency: i.urgency,
    assignment_group: i.assignment_group ?? '',
    assignee: i.assignee ?? '',
  };
}

/** Yalnızca değişen alanlar gönderilir (başkasının değişikliğinin üzerine yazmamak için). */
export function diffDraft(base: Incident, draft: Draft): Patch {
  const original = toDraft(base);
  const patch: Record<string, string | null> = {};
  for (const key of Object.keys(draft) as (keyof Draft)[]) {
    const value = draft[key].trim();
    if (value === original[key].trim()) continue;
    patch[key] = NULLABLE.has(key) && value === '' ? null : value;
  }
  return patch as Patch;
}

export interface IncidentEditFormProps {
  /** Düzenlemenin başladığı andaki kayıt (sürümü If-Match ile gönderilir) */
  base: Incident;
  onSave: SaveFn;
  onCancel: () => void;
  onSaved: () => void;
}

export function IncidentEditForm({ base, onSave, onCancel, onSaved }: IncidentEditFormProps) {
  const [draft, setDraft] = useState<Draft>(() => toDraft(base));
  const [errors, setErrors] = useState<Partial<Record<keyof Draft, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const set =
    <K extends keyof Draft>(key: K) =>
    (value: Draft[K]) => {
      setDraft((d) => ({ ...d, [key]: value }));
    };

  const newPriority = calculatePriority(draft.impact, draft.urgency);
  const priorityChanges = newPriority !== base.priority;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const nextErrors: Partial<Record<keyof Draft, string>> = {};
    if (!draft.title.trim()) nextErrors.title = 'Başlık boş olamaz.';
    if (!draft.description.trim()) nextErrors.description = 'Açıklama boş olamaz.';
    if (!draft.caller.trim()) nextErrors.caller = 'Arayan boş olamaz.';
    setErrors(nextErrors);
    setFormError(null);
    if (Object.keys(nextErrors).length > 0) return;

    const patch = diffDraft(base, draft);
    const fields = Object.keys(patch) as (keyof Draft)[];
    if (fields.length === 0) {
      setFormError('Değişiklik yok.');
      return;
    }
    setSaving(true);
    const result = await onSave(
      base.version,
      patch,
      fields.map((f) => LABELS[f]),
    );
    setSaving(false);
    if (result.ok) {
      onSaved();
    } else if (!result.conflict) {
      const err = result.error;
      if (isApiError(err) && err.fieldErrors.length > 0) {
        const fe: Partial<Record<keyof Draft, string>> = {};
        for (const f of err.fieldErrors) {
          const key = f.field.split('.').pop() as keyof Draft | undefined;
          if (key && key in LABELS) fe[key] = f.message;
        }
        setErrors(fe);
      }
      setFormError(errorMessage(err));
    }
  };

  return (
    <form
      className={styles.form}
      onSubmit={(e) => void submit(e)}
      noValidate
      aria-label="Kaydı düzenle"
    >
      <TextInput
        label="Başlık"
        required
        value={draft.title}
        error={errors.title}
        maxLength={200}
        onChange={(e) => {
          set('title')(e.target.value);
        }}
      />
      <TextArea
        label="Açıklama"
        required
        value={draft.description}
        error={errors.description}
        onChange={(e) => {
          set('description')(e.target.value);
        }}
      />
      <div className={styles.grid}>
        <Select
          label="Tür"
          options={optionsFrom(KIND_LABELS)}
          value={draft.kind}
          onChange={(e) => {
            set('kind')(e.target.value as TicketKind);
          }}
        />
        <TextInput
          label="Arayan"
          required
          value={draft.caller}
          error={errors.caller}
          autoComplete="off"
          onChange={(e) => {
            set('caller')(e.target.value);
          }}
        />
        <Select
          label="Kategori"
          placeholder="Belirtilmedi"
          options={taxonomyOptions('category', draft.category)}
          value={draft.category}
          error={errors.category}
          onChange={(e) => {
            set('category')(e.target.value);
          }}
        />
        <Select
          label="Hizmet"
          placeholder="Belirtilmedi"
          options={taxonomyOptions('service', draft.service)}
          value={draft.service}
          error={errors.service}
          onChange={(e) => {
            set('service')(e.target.value);
          }}
        />
        <Select
          label="Etki"
          options={optionsFrom(IMPACT_LABELS)}
          value={draft.impact}
          onChange={(e) => {
            set('impact')(e.target.value as Impact);
          }}
        />
        <Select
          label="Aciliyet"
          options={optionsFrom(URGENCY_LABELS)}
          value={draft.urgency}
          onChange={(e) => {
            set('urgency')(e.target.value as Urgency);
          }}
        />
        <Select
          label="Atama grubu"
          placeholder="Belirtilmedi"
          options={taxonomyOptions('assignment_group', draft.assignment_group)}
          value={draft.assignment_group}
          error={errors.assignment_group}
          onChange={(e) => {
            set('assignment_group')(e.target.value);
          }}
        />
        <TextInput
          label="Atanan"
          value={draft.assignee}
          error={errors.assignee}
          autoComplete="off"
          onChange={(e) => {
            set('assignee')(e.target.value);
          }}
        />
      </div>
      <p className={styles.note} aria-live="polite">
        Öncelik etki ve aciliyetten hesaplanır:{' '}
        <Badge tone={priorityChanges ? 'warning' : 'neutral'}>{PRIORITY_LABELS[newPriority]}</Badge>
        {priorityChanges && (
          <> (şu an {PRIORITY_LABELS[base.priority]}; kaydedince SLA vadeleri yeniden hesaplanır)</>
        )}
      </p>
      {formError && (
        <p className={styles.formError} role="alert">
          {formError}
        </p>
      )}
      <div className={styles.actions}>
        <Button type="submit" loading={saving}>
          Kaydet
        </Button>
        <Button variant="secondary" onClick={onCancel} disabled={saving}>
          Vazgeç
        </Button>
      </div>
    </form>
  );
}
