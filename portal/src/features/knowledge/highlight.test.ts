import { describe, expect, it } from 'vitest';
import { foldTurkish, highlightSegments, queryNeedles } from './highlight';

const marked = (text: string, q: string) =>
  highlightSegments(text, q)
    .filter((s) => s.match)
    .map((s) => s.text);

describe('highlight', () => {
  it('Türkçe küçük harfe çevirirken uzunluğu korur', () => {
    expect(foldTurkish('İSTANBUL Işık')).toBe('istanbul ışık');
    expect(foldTurkish('İSTANBUL Işık')).toHaveLength('İSTANBUL Işık'.length);
  });

  it('kök önekiyle eşleşir ve kelimenin tamamını vurgular', () => {
    expect(marked('VPN Sertifikası nasıl yenilenir?', 'sertifika vpn')).toEqual([
      'VPN',
      'Sertifikası',
    ]);
  });

  it('İ/ı harflerini doğru eşler, kelime ortasını vurgulamaz', () => {
    expect(marked('İnternet yok, intranet var', 'internet')).toEqual(['İnternet']);
    expect(marked('Kablosuz ağ', 'osuz')).toEqual([]);
  });

  it('bağlaçları ve tek harfleri yok sayar; metni eksiksiz parçalara böler', () => {
    expect(queryNeedles('VPN ve a e-posta')).toEqual(['vpn', 'posta']);
    const segs = highlightSegments('<b>VPN</b> kapalı', 'vpn');
    expect(segs.map((s) => s.text).join('')).toBe('<b>VPN</b> kapalı');
    expect(segs.filter((s) => s.match).map((s) => s.text)).toEqual(['VPN']);
  });

  it('sorgu boşsa vurgulama yapmaz', () => {
    expect(highlightSegments('Metin', '  ')).toEqual([{ text: 'Metin', match: false }]);
    expect(highlightSegments('', 'vpn')).toEqual([]);
  });
});
