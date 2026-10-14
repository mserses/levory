import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/api/errors';
import { EmptyState } from './EmptyState';
import { ErrorState } from './ErrorState';
import { Spinner } from './Spinner';

describe('EmptyState', () => {
  it('başlık, açıklama ve eylem', () => {
    render(
      <EmptyState
        title="Henüz talebiniz yok"
        description="İlk talebinizi oluşturun."
        action={<button type="button">Yeni talep</button>}
      />,
    );
    expect(screen.getByRole('heading', { name: 'Henüz talebiniz yok' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Yeni talep' })).toBeInTheDocument();
  });
});

describe('ErrorState', () => {
  it('ApiError detayını ve yeniden dene butonunu gösterir', async () => {
    const onRetry = vi.fn();
    render(<ErrorState error={new ApiError(404, 'Olay bulunamadı')} onRetry={onRetry} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Bulunamadı');
    expect(screen.getByText('Olay bulunamadı')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Yeniden dene' }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('bilinmeyen hata', () => {
    render(<ErrorState error={new Error('x')} />);
    expect(screen.getByRole('heading', { name: 'Bir sorun oluştu' })).toBeInTheDocument();
    expect(screen.getByText('Beklenmeyen bir hata oluştu.')).toBeInTheDocument();
  });

  it('sunucu metnini HTML olarak yorumlamaz', () => {
    render(<ErrorState error={new ApiError(400, '<img src=x onerror=alert(1)>')} />);
    expect(screen.getByText('<img src=x onerror=alert(1)>')).toBeInTheDocument();
    expect(document.querySelector('img')).toBeNull();
  });
});

describe('Spinner', () => {
  it('status rolü ve etiket', () => {
    render(<Spinner label="Kayıtlar yükleniyor" />);
    expect(screen.getByRole('status')).toHaveTextContent('Kayıtlar yükleniyor');
  });
  it('dekoratif', () => {
    render(<Spinner label="" />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });
});
