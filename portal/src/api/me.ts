import { api } from './client';
import type { Me } from './types';

export const meApi = {
  get: (signal?: AbortSignal) => api.get<Me>('/me', { signal }),
};
