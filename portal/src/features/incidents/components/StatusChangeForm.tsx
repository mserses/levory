import { useState, type FormEvent } from 'react';
import { errorMessage } from '@/api/errors';
import type { Incident, IncidentStatus, OnHoldReason } from '@/api/types';
import { Badge, Button, Select, TextArea, optionsFrom } from '@/components';
import { ON_HOLD_REASON_LABELS, STATUS_LABELS } from '@/lib/labels';
import { ALLOWED_TRANSITIONS, pausesSla } from '../rules';
import styles from './forms.module.css';
import type { Patch, SaveFn } from './types';

export interface StatusChangeFormProps {
  incident: Incident;
  onSave: SaveFn;
}

/**
 * Durum geçişi. Beklemeye alma neden ister (yalnızca arayan/tedarikçi beklemesi SLA'yı
 * durdurur); çözüldü durumu çözüm metni ister.
 */
export function StatusChangeForm({ incident, onSave }: StatusChangeFormProps) {
  const targets = ALLOWED_TRANSITIONS[incident.status];
  const [target, setTarget] = useState<IncidentStatus | ''>('');
  const [reason, setReason] = useState<OnHoldReason | ''>('');
  const [resolution, setResolution] = useState(incident.resolution ?? '');
  // Kullanıcı geçişi seçtiği andaki sürüm: arada kayıt değişirse 409 alınır
  const [baseVersion, setBaseVersion] = useState<number | null>(null);
  const [errors, setErrors] = useState<{ target?: string; reason?: string; resolution?: string }>(
    {},
  );
  const [formError, setFormError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  if (targets.length === 0) {
    return (
      <p className="muted" style={{ margin: 0 }}>
        Bu kayıt {STATUS_LABELS[incident.status].toLocaleLowerCase('tr-TR')}; durumu artık
        değiştirilemez.
      </p>
    );
  }

  const reset = () => {
    setTarget('');
    setReason('');
    setBaseVersion(null);
    setErrors({});
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const next: typeof errors = {};
    if (!target) next.target = 'Yeni durumu seçin.';
    if (target === 'on_hold' && !reason) next.reason = 'Beklemeye almak için neden seçin.';
    if (target === 'resolved' && !resolution.trim())
      next.resolution = 'Çözüldü durumu için çözüm açıklaması zorunludur.';
    setErrors(next);
    setFormError(null);
    if (Object.keys(next).length > 0 || !target) return;

    const patch: Patch = { status: target };
    if (target === 'on_hold' && reason) patch.on_hold_reason = reason;
    if (target === 'resolved') patch.resolution = resolution.trim();
    setSaving(true);
    const result = await onSave(baseVersion ?? incident.version, patch, [
      `Durum: ${STATUS_LABELS[target]}`,
    ]);
    setSaving(false);
    if (result.ok) reset();
    else if (result.conflict) setBaseVersion(null);
    else setFormError(errorMessage(result.error));
  };

  const pauses = pausesSla(reason || null);

  return (
    <form
      className={styles.form}
      onSubmit={(e) => void submit(e)}
      noValidate
      aria-label="Durum değiştir"
    >
      <Select
        label="Yeni durum"
        placeholder="Seçiniz"
        required
        options={targets.map((s) => ({ value: s, label: STATUS_LABELS[s] }))}
        value={target}
        error={errors.target}
        onChange={(e) => {
          const value = e.target.value as IncidentStatus | '';
          setTarget(value);
          if (baseVersion === null) setBaseVersion(incident.version);
          setErrors({});
        }}
      />
      {target === 'on_hold' && (
        <>
          <Select
            label="Bekleme nedeni"
            placeholder="Seçiniz"
            required
            options={optionsFrom(ON_HOLD_REASON_LABELS)}
            value={reason}
            error={errors.reason}
            hint="SLA saati yalnızca kullanıcı ya da tedarikçi beklenirken durur."
            onChange={(e) => {
              setReason(e.target.value as OnHoldReason | '');
            }}
          />
          {reason && (
            <p className={styles.note} aria-live="polite">
              <Badge tone={pauses ? 'info' : 'warning'} dot>
                {pauses ? 'SLA duraklatılır' : 'SLA işlemeye devam eder'}
              </Badge>{' '}
              {pauses
                ? 'Bekleme süresi SLA vadesine eklenir.'
                : 'Bu bekleme nedeninde SLA süresi işlemeye devam eder.'}
            </p>
          )}
        </>
      )}
      {target === 'resolved' && (
        <TextArea
          label="Çözüm"
          required
          value={resolution}
          error={errors.resolution}
          hint="Kullanıcıya ve bilgi bankasına aktarılacak çözüm açıklaması."
          onChange={(e) => {
            setResolution(e.target.value);
          }}
        />
      )}
      {incident.status === 'on_hold' &&
        target &&
        target !== 'on_hold' &&
        incident.sla_paused_at && (
          <p className={styles.note}>Beklemeden çıkınca SLA saati yeniden işlemeye başlar.</p>
        )}
      {formError && (
        <p className={styles.formError} role="alert">
          {formError}
        </p>
      )}
      <div className={styles.actions}>
        <Button type="submit" loading={saving}>
          Durumu güncelle
        </Button>
        {target && (
          <Button variant="ghost" onClick={reset} disabled={saving}>
            Vazgeç
          </Button>
        )}
      </div>
    </form>
  );
}
