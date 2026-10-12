import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import { Button } from './Button';
import { ConfirmDialog, Dialog } from './Dialog';

function Harness({ dismissible = true }: { dismissible?: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button
        onClick={() => {
          setOpen(true);
        }}
      >
        Aç
      </Button>
      <Dialog
        open={open}
        onClose={() => {
          setOpen(false);
        }}
        title="Kaydı beklemeye al"
        description="Bekleme nedenini seçin."
        dismissible={dismissible}
        footer={
          <Button
            onClick={() => {
              setOpen(false);
            }}
          >
            Tamam
          </Button>
        }
      >
        <input aria-label="Not" />
      </Dialog>
    </>
  );
}

describe('Dialog', () => {
  it('erişilebilir ad/açıklama, odak ve Escape ile kapanma, odağı geri verme', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = screen.getByRole('button', { name: 'Aç' });
    await user.click(opener);
    const dialog = screen.getByRole('dialog', { name: 'Kaydı beklemeye al' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog).toHaveAccessibleDescription('Bekleme nedenini seçin.');
    // İlk odaklanabilir öğe: Kapat
    expect(screen.getByRole('button', { name: 'Kapat' })).toHaveFocus();
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });

  it('Tab odağı dialog içinde tutar', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'Aç' }));
    expect(screen.getByRole('button', { name: 'Kapat' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('textbox', { name: 'Not' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Tamam' })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole('button', { name: 'Kapat' })).toHaveFocus();
    await user.tab({ shift: true });
    expect(screen.getByRole('button', { name: 'Tamam' })).toHaveFocus();
  });

  it('odak dialog dışına düşerse (ör. buton busy ile devre dışı kalınca) Tab geri getirir', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'Aç' }));
    const dialog = screen.getByRole('dialog');
    // Odaklı buton devre dışı kalınca tarayıcı odağı body'ye bırakır
    (document.activeElement as HTMLElement).blur();
    expect(document.body).toHaveFocus();
    await user.tab();
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
    // Arka plandaki bir öğeye odak kaçmışsa da
    screen.getByRole('button', { name: 'Aç', hidden: true }).focus();
    await user.tab({ shift: true });
    expect(dialog).toContainElement(document.activeElement as HTMLElement);
  });

  it('açan öğe kapanınca DOM’dan kalktıysa odak ana içeriğe döner (body’de kaybolmaz)', async () => {
    function Vanishing() {
      const [open, setOpen] = useState(false);
      const [done, setDone] = useState(false);
      return (
        <main id="main" tabIndex={-1}>
          {!done && (
            <Button
              onClick={() => {
                setOpen(true);
              }}
            >
              Kaydet
            </Button>
          )}
          <Dialog
            open={open}
            onClose={() => {
              // Ör. çakışma sonrası form yeniden kurulur: açan buton artık yok
              setOpen(false);
              setDone(true);
            }}
            title="Çakışma"
          />
        </main>
      );
    }
    const user = userEvent.setup();
    render(<Vanishing />);
    await user.click(screen.getByRole('button', { name: 'Kaydet' }));
    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.getByRole('main')).toHaveFocus();
  });

  it('dismissible=false iken Escape ve arka plan kapatmaz', async () => {
    const user = userEvent.setup();
    render(<Harness dismissible={false} />);
    await user.click(screen.getByRole('button', { name: 'Aç' }));
    await user.keyboard('{Escape}');
    await user.click(screen.getByTestId('dialog-overlay'));
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Kapat' })).not.toBeInTheDocument();
  });

  it('arka plana tıklayınca kapanır', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'Aç' }));
    await user.click(screen.getByTestId('dialog-overlay'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });
});

describe('ConfirmDialog', () => {
  it('alertdialog; Vazgeç varsayılan odak; onay', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="Öneriyi onayla"
        confirmLabel="Onayla ve uygula"
        onConfirm={onConfirm}
        onCancel={onCancel}
      >
        P2 → P1 yükseltmesi uygulanacak.
      </ConfirmDialog>,
    );
    expect(screen.getByRole('alertdialog', { name: 'Öneriyi onayla' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Vazgeç' })).toHaveFocus();
    await user.click(screen.getByRole('button', { name: 'Onayla ve uygula' }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });

  it('busy iken butonlar kilitli', () => {
    render(
      <ConfirmDialog
        open
        busy
        title="Onay"
        confirmLabel="Onayla"
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );
    expect(screen.getByRole('button', { name: 'Vazgeç' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Onayla' })).toBeDisabled();
  });
});
