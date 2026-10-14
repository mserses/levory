import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';
import { errorMessage, problemsApi, queryKeys, type Problem, type RCAMethod } from '@/api';
import { ROUTE_ROLES } from '@/app/navigation';
import { useAuth } from '@/auth/useAuth';
import { Badge, Button, Select, TextArea, TextInput, optionsFrom, useToast } from '@/components';
import { formatDateTime } from '@/lib/format';
import { RCA_METHOD_LABELS } from '@/lib/labels';
import {
  MAX_RCA_STEPS,
  agentOutcome,
  draftToRca,
  newStep,
  rcaToDraft,
  validateRca,
  type AgentOutcome,
  type RcaDraft,
  type RcaStepDraft,
} from './model';
import { useProblemWrite } from './useProblemWrite';
import styles from './Problems.module.css';

type Json = Record<string, unknown>;

/**
 * Kök neden analizi: kesin RCA (insan) gösterimi + düzenleyici (yapay zekâ taslağından ya da
 * boştan). Yapay zekâ yalnızca `rca_draft` yazar; kesinleştirmek insanın işidir.
 */
export function RcaSection({ problem }: { problem: Problem }) {
  const { hasRole } = useAuth();
  const canOpenApprovals = hasRole(...ROUTE_ROLES.approvals);
  const write = useProblemWrite();
  const queryClient = useQueryClient();
  const toast = useToast();
  const closed = problem.state === 'closed';
  const source = (problem.rca_draft ?? problem.rca) as Json | null;
  const [draft, setDraft] = useState<RcaDraft>(() => rcaToDraft(source));
  const [errors, setErrors] = useState<string[]>([]);
  const [aiOutcome, setAiOutcome] = useState<AgentOutcome | null>(null);
  const errorsRef = useRef<HTMLUListElement>(null);

  // Yeni yapay zekâ taslağı gelince düzenleyiciyi tazele
  const draftJson = JSON.stringify(problem.rca_draft);
  const lastDraft = useRef(draftJson);
  useEffect(() => {
    if (lastDraft.current !== draftJson && problem.rca_draft) {
      setDraft(rcaToDraft(problem.rca_draft as Json));
    }
    lastDraft.current = draftJson;
  }, [draftJson, problem.rca_draft]);

  useEffect(() => {
    if (errors.length > 0) errorsRef.current?.focus();
  }, [errors]);

  const save = useMutation({
    mutationFn: () => problemsApi.recordRca(problem.id, problem.version, draftToRca(draft)),
    onSuccess: (p) => {
      write.onSuccess(p, 'Kök neden analizi kaydedildi');
    },
    onError: (e) => {
      write.onError(problem.id, 'Kök neden analizi kaydedilemedi', e);
    },
  });
  const accept = useMutation({
    mutationFn: () => problemsApi.acceptRcaDraft(problem.id),
    onSuccess: (p) => {
      write.onSuccess(p, 'Taslak kesin RCA olarak kabul edildi');
    },
    onError: (e) => {
      write.onError(problem.id, 'Taslak kabul edilemedi', e);
    },
  });
  const aiDraft = useMutation({
    mutationFn: () => problemsApi.draftRca(problem.id),
    onSuccess: (res) => {
      const o = agentOutcome(res);
      setAiOutcome(o);
      void queryClient.invalidateQueries({ queryKey: queryKeys.problems.detail(problem.id) });
      if (o.kind === 'error') {
        toast.show({ title: 'Taslak üretilemedi', description: o.message, tone: 'error' });
      }
    },
    onError: (e) => {
      toast.show({ title: 'Taslak üretilemedi', description: errorMessage(e), tone: 'error' });
    },
  });

  const onSave = () => {
    const found = validateRca(draft);
    setErrors(found);
    if (found.length === 0) save.mutate();
  };

  const setStep = (key: number, patch: Partial<RcaStepDraft>) => {
    setDraft((d) => ({
      ...d,
      steps: d.steps.map((s) => (s.key === key ? { ...s, ...patch } : s)),
    }));
  };

  return (
    <section className="card stack" aria-labelledby="sorun-rca">
      <h2 id="sorun-rca" className={styles.sectionHeading}>
        Kök neden analizi
      </h2>
      {problem.rca ? (
        <FinalRca rca={problem.rca as Json} />
      ) : (
        <p className={styles.meta}>Henüz kesin kök neden analizi yok.</p>
      )}

      {!closed && (
        <>
          <h3 style={{ margin: 0, fontSize: 'var(--text-md)' }}>
            {problem.rca_draft ? 'RCA taslağı' : 'RCA düzenleyici'}{' '}
            {problem.rca_draft && <Badge tone="accent">Yapay zekâ taslağı</Badge>}
          </h3>
          {aiOutcome?.kind === 'pending_approval' && (
            <p className={styles.notice} role="status">
              Taslak isteği insan onayına düştü: {aiOutcome.message}{' '}
              {canOpenApprovals && aiOutcome.actionRequestId && (
                <Link
                  to={`/onaylar?sekme=eylem&istek=${encodeURIComponent(aiOutcome.actionRequestId)}`}
                >
                  Eylem onayını aç
                </Link>
              )}
            </p>
          )}
          {aiOutcome?.kind === 'executed' && (
            <p className={styles.notice} role="status">
              Yapay zekâ 5 Neden taslağı hazırladı; gözden geçirip kesinleştirin.
            </p>
          )}
          <Select
            label="Yöntem"
            options={optionsFrom(RCA_METHOD_LABELS)}
            value={draft.method}
            onChange={(e) => {
              const m: RCAMethod = e.target.value === 'timeline' ? 'timeline' : '5_why';
              setDraft((d) => ({ ...d, method: m }));
            }}
          />
          {errors.length > 0 && (
            <ul ref={errorsRef} tabIndex={-1} role="alert" className={styles.errors}>
              {errors.map((e) => (
                <li key={e}>{e}</li>
              ))}
            </ul>
          )}
          <ol className={styles.steps} aria-label="RCA adımları">
            {draft.steps.map((s, i) => (
              <li key={s.key} className={styles.step}>
                <p className={styles.stepHead}>
                  <span>Adım {i + 1}</span>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={draft.steps.length === 1}
                    aria-label={`Adım ${String(i + 1)} sil`}
                    onClick={() => {
                      setDraft((d) => ({ ...d, steps: d.steps.filter((x) => x.key !== s.key) }));
                    }}
                  >
                    Sil
                  </Button>
                </p>
                {draft.method === '5_why' ? (
                  <TextInput
                    label={`Adım ${String(i + 1)} — Neden?`}
                    required
                    maxLength={1000}
                    value={s.question}
                    onChange={(e) => {
                      setStep(s.key, { question: e.target.value });
                    }}
                  />
                ) : (
                  <TextInput
                    type="datetime-local"
                    label={`Adım ${String(i + 1)} — Zaman`}
                    required
                    value={s.at}
                    onChange={(e) => {
                      setStep(s.key, { at: e.target.value });
                    }}
                  />
                )}
                <TextArea
                  label={`Adım ${String(i + 1)} — ${draft.method === '5_why' ? 'Yanıt' : 'Olay'}`}
                  required
                  rows={2}
                  maxLength={4000}
                  value={s.answer}
                  onChange={(e) => {
                    setStep(s.key, { answer: e.target.value });
                  }}
                />
              </li>
            ))}
          </ol>
          <div>
            <Button
              size="sm"
              variant="secondary"
              disabled={draft.steps.length >= MAX_RCA_STEPS}
              onClick={() => {
                setDraft((d) => ({ ...d, steps: [...d.steps, newStep()] }));
              }}
            >
              Adım ekle
            </Button>
          </div>
          <TextArea
            label="Kök neden"
            hint="Analizin vardığı kök neden (isteğe bağlı; çözüldü durumu için gerekir)."
            rows={2}
            maxLength={4000}
            value={draft.rootCause}
            onChange={(e) => {
              setDraft((d) => ({ ...d, rootCause: e.target.value }));
            }}
          />
          <div className={styles.buttons}>
            <Button loading={save.isPending} onClick={onSave}>
              Kesin RCA olarak kaydet
            </Button>
            {problem.rca_draft && (
              <Button
                variant="secondary"
                loading={accept.isPending}
                onClick={() => {
                  accept.mutate();
                }}
              >
                Taslağı olduğu gibi kabul et
              </Button>
            )}
            <Button
              variant="ghost"
              loading={aiDraft.isPending}
              onClick={() => {
                aiDraft.mutate();
              }}
            >
              Yapay zekâ taslağı üret
            </Button>
          </div>
        </>
      )}
    </section>
  );
}

function FinalRca({ rca }: { rca: Json }) {
  const method = rca.method === 'timeline' ? 'timeline' : '5_why';
  const steps = Array.isArray(rca.steps)
    ? (rca.steps as unknown[]).filter((s): s is Json => typeof s === 'object' && s !== null)
    : [];
  const text = (v: unknown) => (typeof v === 'string' ? v : '');
  return (
    <div className="stack" style={{ gap: 'var(--space-2)' }} data-testid="final-rca">
      <p className={styles.meta}>Kesin RCA · {RCA_METHOD_LABELS[method]}</p>
      <ol className={styles.readRca}>
        {steps.map((s, i) => (
          <li key={i} className="prewrap">
            {method === '5_why' ? (
              <>
                <strong>{text(s.question)}</strong> {text(s.answer)}
              </>
            ) : (
              <>
                <strong>{formatDateTime(text(s.at))}</strong> {text(s.answer)}
              </>
            )}
          </li>
        ))}
      </ol>
      {text(rca.root_cause) && (
        <p className="prewrap" style={{ margin: 0 }}>
          <strong>Kök neden:</strong> {text(rca.root_cause)}
        </p>
      )}
    </div>
  );
}
