import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { Button, buttonClassName } from './Button';

describe('Button', () => {
  it('varsayılan type="button" ve tıklama', async () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Kaydet</Button>);
    const btn = screen.getByRole('button', { name: 'Kaydet' });
    expect(btn).toHaveAttribute('type', 'button');
    await userEvent.click(btn);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it('loading iken devre dışı ve aria-busy', async () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Gönder
      </Button>,
    );
    const btn = screen.getByRole('button', { name: 'Gönder' });
    expect(btn).toBeDisabled();
    expect(btn).toHaveAttribute('aria-busy', 'true');
    await userEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it('varyant sınıfları', () => {
    expect(buttonClassName('danger', 'sm', true)).toContain('danger');
    expect(buttonClassName('danger', 'sm', true)).toContain('block');
  });
});
