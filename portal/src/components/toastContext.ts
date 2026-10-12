import { createContext, useContext } from 'react';

export type ToastTone = 'success' | 'error' | 'warning' | 'info';

export interface ToastOptions {
  title: string;
  description?: string;
  tone?: ToastTone;
  /** ms; 0 = kullanıcı kapatana kadar. Varsayılan 5000 (hata: 8000) */
  duration?: number;
}

export interface ToastApi {
  show: (options: ToastOptions) => number;
  dismiss: (id: number) => void;
}

export const ToastContext = createContext<ToastApi | null>(null);

/** Bildirim göster: const toast = useToast(); toast.show({ title: 'Kaydedildi', tone: 'success' }) */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast, <ToastProvider> içinde kullanılmalı.');
  return ctx;
}
