import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AuthContext, type AuthContextValue } from '@/auth/AuthContext';
import { ConfidenceMeter } from './ConfidenceMeter';

describe('ConfidenceMeter', () => {
  it('eşik üstü', () => {
    render(<ConfidenceMeter value={0.91} />);
    const meter = screen.getByRole('meter', { name: 'Güven' });
    expect(meter).toHaveAttribute('aria-valuenow', '91');
    expect(meter).toHaveAttribute('aria-valuetext', '%91');
    expect(screen.queryByText(/Eşiğin/)).not.toBeInTheDocument();
  });

  it('eşik altı uyarısı ve sınırlama', () => {
    render(<ConfidenceMeter value={1.4} threshold={0.75} />);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '100');
  });

  it('eşik altında insan incelemesi notu', () => {
    render(<ConfidenceMeter value={0.42} />);
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuetext', '%42, eşiğin altında');
    expect(screen.getByText(/insan incelemesi gerekir/)).toBeInTheDocument();
  });

  it('eşik verilmezse oturumdaki (/me) politika eşiğini kullanır', () => {
    const auth = { aiConfidenceThreshold: 0.6 } as AuthContextValue;
    render(
      <AuthContext.Provider value={auth}>
        <ConfidenceMeter value={0.65} />
      </AuthContext.Provider>,
    );
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuetext', '%65');
    expect(screen.queryByText(/Eşiğin/)).not.toBeInTheDocument();
  });
});
