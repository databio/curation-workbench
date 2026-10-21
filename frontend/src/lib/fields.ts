// Operation fields of a verdict, from its field schema in verdicts.yaml
// (`str`, `bool`, `int`, `choice:a|b`, `sid`, `sid:kind`, `sids:kind`, `map`,
// `list`, `str_list`; a trailing `?` means optional). Ported from the first
// site's DecisionControl.astro.

export type Widget = 'select' | 'textarea' | 'number' | 'text' | 'sid';

export interface FieldSpec {
  name: string;
  type: string;          // without the trailing '?'
  required: boolean;
  widget: Widget;
  choices: string[];     // for select
  sidKind: string;       // for sid pickers ('' = any kind)
  many: boolean;         // sids:kind
  placeholder: string;
}

export function fieldSpec(name: string, rawType: string, required: boolean): FieldSpec {
  const t = rawType.replace(/\?$/, '');
  const base = { name, type: t, required, choices: [] as string[], sidKind: '', many: false, placeholder: '' };
  if (t.startsWith('choice:') || t === 'bool') {
    return { ...base, widget: 'select', choices: t === 'bool' ? ['true', 'false'] : t.slice(7).split('|') };
  }
  if (t === 'map' || t === 'list' || t === 'str_list') {
    return { ...base, widget: 'textarea', placeholder: t === 'str_list' ? 'one per line' : 'JSON' };
  }
  if (t === 'int') return { ...base, widget: 'number' };
  if (t.startsWith('sid')) {
    const kind = t.includes(':') ? t.split(':')[1] : '';
    const many = t.startsWith('sids:');
    return { ...base, widget: 'sid', sidKind: kind, many,
      placeholder: many ? `${kind} SIDs, comma-separated` : `${kind || 'subject'} SID` };
  }
  return { ...base, widget: 'text' };
}

/** The text a prefilled value shows in its input. */
export function showValue(spec: FieldSpec, value: unknown): string {
  if (value === undefined || value === null) return '';
  if (spec.type === 'map' || spec.type === 'list') return JSON.stringify(value);
  if (spec.type === 'str_list') return (value as string[]).join('\n');
  if (spec.many) return (value as string[]).join(', ');
  return String(value);
}

/** The operation value of an input's text (undefined when empty). Throws on bad JSON. */
export function readValue(spec: FieldSpec, text: string): unknown {
  const v = text.trim();
  if (v === '') return undefined;
  if (spec.type === 'bool') return v === 'true';
  if (spec.type === 'int') return parseInt(v, 10);
  if (spec.type === 'map' || spec.type === 'list') return JSON.parse(v);
  if (spec.type === 'str_list') return v.split('\n').map((s) => s.trim()).filter(Boolean);
  if (spec.many) return v.split(/[,\s]+/).filter(Boolean);
  return v;
}
