import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Button } from './Button';
import { ToastProvider } from './Toast';
import { useToast, type ToastOptions } from './toastContext';

function Trigger(props: ToastOptions) {
  const toast = useToast();
  return <Button onClick={() => toast.show(props)}>Göster</Button>;
}

describe('Toast', () => {
  it('bildirim gösterir, kapatılabilir', async () => {
    const user = userEvent.setup();
    render(
      <ToastProvider>
        <Trigger title="Talebiniz alındı" description="INC0000042" tone="success" />
      </ToastProvider>,
    );
    await user.click(screen.getByRole('button', { name: 'Göster' }));
    const region = screen.getByRole('region', { name: 'Bildirimler' });
    expect(region).toHaveTextContent('Talebiniz alındı');
    expect(screen.getByRole('status')).toHaveTextContent('INC0000042');
    await user.click(screen.getByRole('button', { name: 'Bildirimi kapat' }));
    expect(region).not.toHaveTextContent('Talebiniz alındı');
  });

  it('hata alert rolündedir ve süre dolunca kapanır', async () => {
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <Trigger title="Kaydedilemedi" tone="error" duration={1000} />
        </ToastProvider>,
      );
      act(() => {
        screen.getByRole('button', { name: 'Göster' }).click();
      });
      expect(screen.getByRole('alert')).toHaveTextContent('Kaydedilemedi');
      act(() => {
        vi.advanceTimersByTime(1100);
      });
      expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it('provider dışında hata verir', () => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    expect(() => render(<Trigger title="x" />)).toThrow(/ToastProvider/);
  });
});
