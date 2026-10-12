import { describe, expect, it } from 'vitest';
import { SERVICE_LABELS, SERVICE_OPTIONS, serviceLabel } from './services';
import { looksLikeSecret, SECRET_MESSAGE, TITLE_MAX, validateNewRequest } from './validation';

const valid = {
  title: 'VPN bağlanmıyor',
  description: 'Evden VPN ile bağlanmaya çalışınca zaman aşımı alıyorum.',
  service: '',
  urgency: '',
};

describe('validateNewRequest', () => {
  it('geçerli girdiyi kırpar, boş seçimleri null yapar', () => {
    const r = validateNewRequest({ ...valid, title: '  VPN bağlanmıyor  ' });
    expect(r).toEqual({
      ok: true,
      values: {
        title: 'VPN bağlanmıyor',
        description: valid.description,
        service: null,
        urgency: null,
      },
    });
  });

  it('zorunlu alanlar ve uzunluk sınırları', () => {
    const r = validateNewRequest({ title: ' ', description: 'kısa', service: '', urgency: '' });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.errors.title).toBe('Kısa bir başlık yazın.');
    expect(r.errors.description).toMatch(/en az 10 karakter/);

    const long = validateNewRequest({ ...valid, title: 'x'.repeat(TITLE_MAX + 1) });
    expect(long.ok).toBe(false);
    if (!long.ok) expect(long.errors.title).toMatch(/en fazla 300/);
  });

  it('taksonomi dışı hizmet ve aciliyet reddedilir', () => {
    const r = validateNewRequest({ ...valid, service: 'uydurma', urgency: 'kritik' });
    expect(r.ok).toBe(false);
    if (r.ok) return;
    expect(r.errors.service).toBe('Listeden bir hizmet seçin.');
    expect(r.errors.urgency).toBe('Listeden bir aciliyet seçin.');
  });

  it('parola paylaşımını engeller', () => {
    const r = validateNewRequest({
      ...valid,
      description: 'Kullanıcı adım ali, şifrem: Gizli123!',
    });
    expect(r.ok).toBe(false);
    if (!r.ok) expect(r.errors.description).toBe(SECRET_MESSAGE);
  });
});

describe('looksLikeSecret', () => {
  it.each([
    ['parola: abc123', true],
    ['Password=hunter2', true],
    ['şifre = 1234', true],
    ['PIN: 4455', true],
    ['Şifremi unuttum, sıfırlamak istiyorum', false],
    ['Parola sıfırlama ekranı açılmıyor', false],
  ])('%s → %s', (text, expected) => {
    expect(looksLikeSecret(text)).toBe(expected);
  });
});

describe('hizmet listesi', () => {
  it('taxonomy.json ile aynı 14 hizmet, Türkçe sıralı', () => {
    expect(SERVICE_OPTIONS).toHaveLength(14);
    expect(Object.keys(SERVICE_LABELS)).toContain('vpn');
    const labels = SERVICE_OPTIONS.map((o) => o.label);
    expect(labels).toEqual([...labels].sort((a, b) => a.localeCompare(b, 'tr')));
  });

  it('serviceLabel', () => {
    expect(serviceLabel('vpn')).toBe('Uzaktan erişim VPN');
    expect(serviceLabel(null)).toBe('Belirtilmedi');
    expect(serviceLabel('bilinmeyen')).toBe('bilinmeyen');
  });
});
