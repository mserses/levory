import { useEffect } from 'react';

/** Belge başlığını ayarlar: "Kayıtlar · ServisZeka" (ekran okuyucular sayfa değişimini duyurur) */
export function usePageTitle(title: string): void {
  useEffect(() => {
    document.title = title ? `${title} · ServisZeka` : 'ServisZeka';
  }, [title]);
}
