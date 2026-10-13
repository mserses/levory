import { describe, expect, it } from 'vitest';
import {
  enumOptionLabel,
  initialValues,
  parseFormSchema,
  toPayload,
  validateForm,
  type FormField,
} from './formSchema';

const SCHEMA = {
  type: 'object',
  properties: {
    mailbox: { type: 'string', format: 'user', title: 'Posta kutusu' },
    group: { type: 'string', format: 'group', title: 'Grup', description: 'Grup adı' },
    access: {
      type: 'string',
      enum: ['full_access', 'send_as'],
      title: 'Erişim türü',
      default: 'full_access',
    },
    remote: { type: 'boolean', title: 'Uzaktan mı?', default: false },
    consent: { type: 'boolean', title: 'Koşulları kabul ediyorum' },
    justification: { type: 'string', title: 'Gerekçe', maxLength: 20, minLength: 3 },
    weird: { type: 'number', title: 'Desteklenmeyen' },
  },
  required: ['mailbox', 'consent', 'justification'],
};

describe('parseFormSchema', () => {
  it('desteklenen alt kümeyi alan listesine çevirir, bilinmeyeni ayırır', () => {
    const { fields, unsupported } = parseFormSchema(SCHEMA);
    expect(fields.map((f) => [f.name, f.kind, f.required])).toEqual([
      ['mailbox', 'user', true],
      ['group', 'group', false],
      ['access', 'enum', false],
      ['remote', 'boolean', false],
      ['consent', 'boolean', true],
      ['justification', 'text', true],
    ]);
    expect(unsupported).toEqual(['weird']);
    const justification = fields.find((f) => f.name === 'justification');
    expect(justification?.maxLength).toBe(20);
    expect(justification?.minLength).toBe(3);
    expect(fields.find((f) => f.name === 'group')?.description).toBe('Grup adı');
  });

  it('bozuk/boş şemada boş liste döner', () => {
    expect(parseFormSchema({}).fields).toEqual([]);
    expect(parseFormSchema({ properties: 'x' }).fields).toEqual([]);
    expect(parseFormSchema({ properties: { a: null } }).unsupported).toEqual(['a']);
  });

  it('uzun gerekçe alanı çok satırlı olur', () => {
    const { fields } = parseFormSchema({
      properties: { note: { type: 'string', title: 'Not', maxLength: 500 } },
    });
    expect(fields[0]?.multiline).toBe(true);
  });
});

describe('initialValues / validateForm / toPayload', () => {
  const { fields } = parseFormSchema(SCHEMA);

  it('varsayılanlar uygulanır (boolean varsayılanı yoksa false)', () => {
    expect(initialValues(fields)).toEqual({
      mailbox: '',
      group: '',
      access: 'full_access',
      remote: false,
      consent: false,
      justification: '',
    });
  });

  it('zorunlu, uzunluk, enum ve zorunlu onay kutusu hataları (backend ile aynı kurallar)', () => {
    const errors = validateForm(fields, {
      ...initialValues(fields),
      access: 'bilinmeyen',
      justification: 'ab',
    });
    expect(errors).toEqual({
      mailbox: 'Posta kutusu zorunludur.',
      access: 'Erişim türü için listeden bir değer seçin.',
      consent: 'Devam etmek için “Koşulları kabul ediyorum” işaretlenmeli.',
      justification: 'Gerekçe en az 3 karakter olmalı.',
    });
    expect(
      validateForm(fields, { ...initialValues(fields), justification: 'x'.repeat(21) })
        .justification,
    ).toBe('Gerekçe en fazla 20 karakter olabilir.');
  });

  it('geçerli formda hata yok; yük kırpılır ve boş metinler atlanır', () => {
    const values = {
      ...initialValues(fields),
      mailbox: '  ik@contoso.com ',
      consent: true,
      justification: ' Ekip değişikliği ',
    };
    expect(validateForm(fields, values)).toEqual({});
    expect(toPayload(fields, values)).toEqual({
      mailbox: 'ik@contoso.com',
      access: 'full_access',
      remote: false,
      consent: true,
      justification: 'Ekip değişikliği',
    });
  });

  it('kullanıcı/grup seçicide denetim karakteri reddedilir', () => {
    const user: FormField = { name: 'u', kind: 'user', title: 'Kullanıcı', required: false };
    expect(validateForm([user], { u: 'a\u0007b' }).u).toBe('Kullanıcı geçersiz karakter içeriyor.');
  });
});

describe('enumOptionLabel', () => {
  it('bilinen değerler Türkçe, bilinmeyen değer olduğu gibi', () => {
    expect(enumOptionLabel('full_access')).toBe('Tam erişim');
    expect(enumOptionLabel('SPE_E3')).toBe('Microsoft 365 E3');
    expect(enumOptionLabel('Özel değer')).toBe('Özel değer');
  });
});
