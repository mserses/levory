import { describe, expect, it } from 'vitest';
import { ApiError } from '@/api';
import { makeArticle } from '@/test/fixtures';
import {
  serverFieldErrors,
  toCreateBody,
  toUpdateBody,
  validateArticle,
  valuesFromArticle,
} from './form';

describe('makale formu', () => {
  it('zorunlu alanları ve bilinen hata geçici çözümünü doğrular', () => {
    const v = { title: ' ', body: '', category: '', isKnownError: true, workaround: ' ' };
    expect(validateArticle(v)).toEqual({
      title: 'Başlık zorunludur.',
      body: 'İçerik zorunludur.',
      workaround: 'Bilinen hata kayıtları için geçici çözümü yazın.',
    });
  });

  it('oluşturma gövdesi satır sonlarını korur, boş kategoriyi null yapar', () => {
    const body = toCreateBody(
      {
        title: ' Başlık ',
        body: 'a\r\n\r\nb\n',
        category: '',
        isKnownError: false,
        workaround: 'x',
      },
      true,
    );
    expect(body).toEqual({
      title: 'Başlık',
      body: 'a\n\nb',
      category: null,
      is_known_error: false,
      workaround: null,
      state: 'published',
    });
  });

  it('güncellemede yalnızca değişen alanlar gönderilir', () => {
    const a = makeArticle({ title: 'T', body: 'B', category: 'ag' });
    expect(toUpdateBody(a, valuesFromArticle(a))).toEqual({});
    expect(toUpdateBody(a, { ...valuesFromArticle(a), category: '', title: 'Yeni' })).toEqual({
      title: 'Yeni',
      category: null,
    });
  });

  it('422 alan hatalarını form alanlarına eşler', () => {
    const e = new ApiError(422, 'x', [{ field: 'title', message: 'çok uzun' }]);
    expect(serverFieldErrors(e)).toEqual({ title: 'çok uzun' });
  });
});
