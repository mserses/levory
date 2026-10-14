import { useId, useState, type FormEvent } from 'react';
import { scheduleConflictsOf } from '@/api';
import { isApiError } from '@/api/errors';
import type { Change, ChangeState, ScheduleConflict } from '@/api/types';
import { Button, ConfirmDialog, Dialog, TextArea, useToast } from '@/components';
import { CHANGE_STATE_LABELS } from '@/lib/labels';
import styles from '../Changes.module.css';
import { useTransition } from '../hooks';
import {
  DESTRUCTIVE_TRANSITIONS,
  allowedTransitions,
  pirRequired,
  transitionLabel,
  type ChangeActor,
} from '../rules';
import { ConflictList } from './ConflictList';
import { VersionConflictDialog } from './VersionConflictDialog';

const CONFIRM_TITLES: Partial<Record<ChangeState, string>> = {
  cancelled: 'Değişiklik iptal edilsin mi?',
  rejected: 'Değişiklik reddedilsin mi?',
  failed: 'Uygulama başarısız olarak işaretlensin mi?',
};

const CONFIRM_DESCRIPTIONS: Partial<Record<ChangeState, string>> = {
  cancelled: 'İptal geri alınamaz; yeniden planlamak için yeni bir değişiklik açmanız gerekir.',
  rejected: 'Ret geri alınamaz.',
  failed: 'Değişiklik gözden geçirmeye gider ve kapatmadan önce PIR notu zorunlu olur.',
};

export interface TransitionPanelProps {
  change: Change;
  actor: ChangeActor;
  /** Sürüm çakışmasında güncel hâli yükler */
  onReload: () => Promise<void>;
}

/**
 * Durum geçişleri: yalnızca kullanıcının yapabileceği geçişler düğme olarak gösterilir (asıl
 * denetim sunucuda). Sürüm `If-Match` ile gider; 409'da çakışma diyaloğu açılır.
 */
export function TransitionPanel({ change, actor, onReload }: TransitionPanelProps) {
  const headingId = useId();
  const toast = useToast();
  const transition = useTransition(change.id);
  const [confirm, setConfirm] = useState<ChangeState | null>(null);
  const [versionConflict, setVersionConflict] = useState(false);
  const [scheduleConflicts, setScheduleConflicts] = useState<ScheduleConflict[] | null>(null);
  const [reloading, setReloading] = useState(false);
  const allowed = allowedTransitions(change, actor);
  const buttons = allowed.filter((s) => s !== 'closed');
  const canClose = allowed.includes('closed');

  const run = async (to: ChangeState, extra: { review_notes?: string } = {}): Promise<boolean> => {
    try {
      await transition.mutateAsync({ version: change.version, body: { to_state: to, ...extra } });
      toast.show({ title: `${CHANGE_STATE_LABELS[to]} aşamasına geçti`, tone: 'success' });
      return true;
    } catch (error) {
      const conflicts = scheduleConflictsOf(error);
      if (conflicts) setScheduleConflicts(conflicts);
      else if (isApiError(error) && error.isConflict) setVersionConflict(true);
      else
        toast.show({
          title: 'Geçiş yapılamadı',
          description: isApiError(error) ? error.detail : undefined,
          tone: 'error',
        });
      return false;
    } finally {
      setConfirm(null);
    }
  };

  const reload = async () => {
    setReloading(true);
    try {
      await onReload();
    } finally {
      setReloading(false);
      setVersionConflict(false);
    }
  };

  return (
    <section className="card stack" aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.sectionTitle}>
        İşlemler
      </h2>
      {buttons.length > 0 ? (
        <div className="row">
          {buttons.map((to) => (
            <Button
              key={to}
              variant={DESTRUCTIVE_TRANSITIONS.includes(to) ? 'danger' : 'primary'}
              loading={transition.isPending && transition.variables.body.to_state === to}
              disabled={transition.isPending}
              onClick={() => {
                if (DESTRUCTIVE_TRANSITIONS.includes(to)) setConfirm(to);
                else void run(to);
              }}
            >
              {transitionLabel(change, to)}
            </Button>
          ))}
        </div>
      ) : (
        !canClose && (
          <p className="muted" style={{ margin: 0 }}>
            Bu aşamada sizin yapabileceğiniz bir durum geçişi yok.
          </p>
        )
      )}
      {(change.state === 'scheduled' || change.state === 'authorize') && buttons.length > 0 && (
        <p className="muted" style={{ margin: 0, fontSize: 'var(--text-sm)' }}>
          Zamanlama ve uygulama anında onaylar, standart uygunluk ve çakışmalar sunucuda yeniden
          denetlenir; uygulama yalnızca planlanan pencere içinde başlatılabilir.
        </p>
      )}
      {canClose && (
        <PirForm
          change={change}
          busy={transition.isPending}
          onSubmit={(notes) => run('closed', notes ? { review_notes: notes } : {})}
        />
      )}

      <ConfirmDialog
        open={confirm !== null}
        title={confirm ? (CONFIRM_TITLES[confirm] ?? 'Emin misiniz?') : ''}
        description={confirm ? CONFIRM_DESCRIPTIONS[confirm] : undefined}
        confirmLabel={confirm ? transitionLabel(change, confirm) : ''}
        tone="danger"
        busy={transition.isPending}
        onConfirm={() => {
          if (confirm) void run(confirm);
        }}
        onCancel={() => {
          setConfirm(null);
        }}
      />
      <VersionConflictDialog
        open={versionConflict}
        reloading={reloading}
        onReload={() => void reload()}
      />
      <Dialog
        open={scheduleConflicts !== null}
        role="alertdialog"
        onClose={() => {
          setScheduleConflicts(null);
        }}
        title="Planlanan pencerede çakışma var"
        description="Değişiklik bu pencerede zamanlanamaz ya da uygulanamaz. Pencereyi değiştirin ya da çakışmayı giderin."
        footer={
          <Button
            data-autofocus
            onClick={() => {
              setScheduleConflicts(null);
            }}
          >
            Tamam
          </Button>
        }
      >
        <ConflictList conflicts={scheduleConflicts ?? []} />
      </Dialog>
    </section>
  );
}

/** Uygulama sonrası gözden geçirme (PIR) ve kapatma; acil/başarısızda not zorunlu */
function PirForm({
  change,
  busy,
  onSubmit,
}: {
  change: Change;
  busy: boolean;
  onSubmit: (notes: string) => Promise<boolean>;
}) {
  const required = pirRequired(change);
  const [notes, setNotes] = useState(change.review_notes ?? '');
  const [error, setError] = useState<string | null>(null);
  const titleId = useId();
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (required && !notes.trim()) {
      setError('Acil ya da başarısız değişiklikte PIR notu zorunludur.');
      return;
    }
    setError(null);
    void onSubmit(notes.trim());
  };
  return (
    <form className="stack" aria-labelledby={titleId} noValidate onSubmit={submit}>
      <h3 id={titleId} className={styles.subTitle}>
        Uygulama sonrası gözden geçirme
      </h3>
      <p className="muted" style={{ margin: 0 }}>
        Sonuç:{' '}
        {change.implementation_outcome === 'failed'
          ? 'Uygulama başarısız oldu.'
          : change.implementation_outcome === 'succeeded'
            ? 'Uygulama başarıyla tamamlandı.'
            : 'Belirtilmedi.'}
      </p>
      <TextArea
        label="PIR notu"
        required={required}
        hint={
          required
            ? 'Acil ya da başarısız değişiklik bu not olmadan kapatılamaz: ne oldu, kök neden, alınan dersler.'
            : 'İsteğe bağlı: sonuç, sapmalar ve alınan dersler.'
        }
        value={notes}
        maxLength={10000}
        error={error}
        onChange={(e) => {
          setNotes(e.target.value);
        }}
      />
      <div className="row">
        <Button type="submit" loading={busy}>
          Gözden geçirmeyi kaydet ve kapat
        </Button>
      </div>
    </form>
  );
}
