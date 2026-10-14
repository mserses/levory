import { Button, Dialog } from '@/components';

export interface ConflictDialogProps {
  open: boolean;
  /** Kullanıcının kaydetmeye çalıştığı alanların Türkçe adları */
  fields: string[];
  reloading: boolean;
  onReload: () => void;
}

/** Optimistik kilit çakışması (409): kayıt başkası tarafından değiştirildi. */
export function ConflictDialog({ open, fields, reloading, onReload }: ConflictDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onReload}
      dismissible={false}
      role="alertdialog"
      title="Kayıt başkası tarafından değişti"
      description="Siz düzenlerken bu kayıt başka bir kullanıcı ya da yapay zekâ tarafından güncellendi. Üzerine yazmamak için değişiklikleriniz kaydedilmedi."
      footer={
        <Button onClick={onReload} loading={reloading} data-autofocus>
          Güncel kaydı yükle
        </Button>
      }
    >
      {fields.length > 0 && (
        <>
          <p style={{ margin: 0 }}>Kaydedilemeyen alanlar:</p>
          <ul style={{ margin: 0 }}>
            {fields.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
        </>
      )}
      <p className="muted" style={{ margin: 0 }}>
        Güncel hâli yükledikten sonra değişikliklerinizi gözden geçirip yeniden uygulayın.
      </p>
    </Dialog>
  );
}
