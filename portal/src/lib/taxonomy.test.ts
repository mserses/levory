import { describe, expect, it } from 'vitest';
import { displayValue, fieldLabel } from './fields';
import {
  categoryLabel,
  groupLabel,
  missingShortNames,
  serviceLabel,
  taxonomyOptions,
} from './taxonomy';

describe('taksonomi', () => {
  it('her kategori ve grup anahtarının kısa Türkçe adı var', () => {
    expect(missingShortNames()).toEqual([]);
  });

  it('anahtarları Türkçe ada çevirir, bilinmeyeni "Diğer (…)" gösterir', () => {
    expect(categoryLabel('ag')).toBe('Ağ ve bağlantı');
    expect(serviceLabel('vpn')).toBe('Uzaktan erişim VPN');
    expect(groupLabel('network_ops')).toBe('Ağ operasyonları');
    expect(categoryLabel('xyz')).toBe('Diğer (xyz)');
  });

  it('seçenekler sıralı ve taksonomi dışı mevcut değeri korur', () => {
    const options = taxonomyOptions('category', 'eski_kategori');
    expect(options.at(-1)).toEqual({ value: 'eski_kategori', label: 'Diğer (eski_kategori)' });
    const labels = options.slice(0, -1).map((o) => o.label);
    expect(labels).toEqual([...labels].sort((a, b) => a.localeCompare(b, 'tr')));
  });

  it('displayValue enum ve taksonomi değerlerini ortak biçimde gösterir', () => {
    expect(displayValue('category', 'ag')).toBe('Ağ ve bağlantı');
    expect(displayValue('impact', 'high')).toMatch(/^Yüksek/);
    expect(displayValue('title', '')).toBe('—');
    expect(fieldLabel('assignment_group')).toBe('Atama grubu');
  });
});
