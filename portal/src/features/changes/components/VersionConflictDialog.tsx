import { Button, Dialog } from '@/components';

/** İyimser kilit çakışması (409): değişiklik başkası tarafından güncellendi */
export function VersionConflictDialog({
  open,
  reloading,
  onReload,
}: {
  open: boolean;
  reloading: boolean;
  onReload: () => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onReload}
      dismissible={false}
      role="alertdialog"
      title="Değişiklik başkası tarafından güncellendi"
      description="Siz bakarken bu değişiklik başka bir kullanıcı tarafından değiştirildi. Yanlış sürüm üzerinde işlem yapmamak için isteğiniz uygulanmadı."
      footer={
        <Button onClick={onReload} loading={reloading} data-autofocus>
          Güncel hâli yükle
        </Button>
      }
    >
      <p className="muted" style={{ margin: 0 }}>
        Güncel hâli yükledikten sonra durumu ve onayları gözden geçirip işlemi yeniden deneyin.
      </p>
    </Dialog>
  );
}
