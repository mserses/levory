import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { errorMessage, isApiError, queryKeys, requestsApi, type ServiceRequest } from '@/api';
import { Button, TextArea, useToast } from '@/components';
import styles from './Requests.module.css';

type Decision = 'approve' | 'reject';

/**
 * Hizmet talebine iş onayı / reddi (POST /requests/{id}/approve|reject). Yalnızca backend
 * `can_decide=true` dediğinde gösterilir (yönetici, onay grubu üyesi ya da admin; talep eden ve açan
 * hiçbir zaman). Sürüm gönderilir; talep bu arada değiştiyse 409 -> yeniden yüklenir.
 */
export function BusinessDecision({ request }: { request: ServiceRequest }) {
  const [comment, setComment] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const queryClient = useQueryClient();
  const toast = useToast();

  const decide = useMutation({
    mutationFn: (decision: Decision) =>
      decision === 'approve'
        ? requestsApi.approve(request.id, request.version, comment.trim())
        : requestsApi.reject(request.id, request.version, comment.trim()),
    onSuccess: (updated, decision) => {
      queryClient.setQueryData(queryKeys.requests.detail(updated.id), updated);
      void queryClient.invalidateQueries({ queryKey: queryKeys.requests.lists });
      toast.show({
        title:
          decision === 'approve'
            ? `${updated.number} onaylandı; karşılama başladı`
            : `${updated.number} reddedildi`,
        tone: 'success',
      });
    },
    onError: (e) => {
      if (isApiError(e) && e.isConflict) {
        setConflict(true);
        void queryClient.invalidateQueries({ queryKey: queryKeys.requests.detail(request.id) });
        return;
      }
      toast.show({ title: 'Karar kaydedilemedi', description: errorMessage(e), tone: 'error' });
    },
  });

  const submit = (decision: Decision) => {
    setConflict(false);
    if (decision === 'reject' && !comment.trim()) {
      setError('Ret için gerekçe yazın; talep eden bu yorumu görür.');
      return;
    }
    setError(null);
    decide.mutate(decision);
  };

  return (
    <div className="stack" style={{ marginTop: 'var(--space-3)' }}>
      {conflict && (
        <p className={`${styles.notice} ${styles.noticeWarn}`} role="alert">
          Talep değişti: başka biri karar vermiş ya da talep güncellenmiş olabilir. Güncel durum
          yüklendi.
        </p>
      )}
      <TextArea
        label="Yorum (ret için zorunlu)"
        rows={3}
        maxLength={2000}
        value={comment}
        error={error}
        onChange={(e) => {
          setComment(e.target.value);
          if (error) setError(null);
        }}
      />
      <div className="row" style={{ gap: 'var(--space-2)' }}>
        <Button
          onClick={() => {
            submit('approve');
          }}
          disabled={decide.isPending}
        >
          Onayla
        </Button>
        <Button
          variant="secondary"
          onClick={() => {
            submit('reject');
          }}
          disabled={decide.isPending}
        >
          Reddet
        </Button>
      </div>
    </div>
  );
}
