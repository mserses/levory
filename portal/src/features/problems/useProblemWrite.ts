import { useQueryClient } from '@tanstack/react-query';
import { errorMessage, isApiError, queryKeys, type Problem } from '@/api';
import { useToast } from '@/components';

/** Sorun yazmalarının ortak sonucu: önbelleği güncelle; 409'da yeniden yükle */
export function useProblemWrite() {
  const queryClient = useQueryClient();
  const toast = useToast();
  return {
    onSuccess: (updated: Problem, message?: string) => {
      queryClient.setQueryData(queryKeys.problems.detail(updated.id), updated);
      void queryClient.invalidateQueries({ queryKey: queryKeys.problems.lists });
      if (message) toast.show({ title: message, tone: 'success' });
    },
    onError: (problemId: string, title: string, e: unknown) => {
      if (isApiError(e) && e.isConflict) {
        void queryClient.invalidateQueries({ queryKey: queryKeys.problems.detail(problemId) });
        toast.show({
          title: 'Sorun kaydı başkası tarafından değiştirildi',
          description: 'Kayıt yeniden yüklendi; değişikliğinizi gözden geçirip tekrar deneyin.',
          tone: 'warning',
        });
        return;
      }
      toast.show({ title, description: errorMessage(e), tone: 'error' });
    },
  };
}
