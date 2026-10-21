// The plain-words helpers: verdict labels, operation summaries, and matching a
// saved decision back to the button that made it.
import { describe, expect, it } from 'vitest';
import type { Verdicts } from '../src/api/types';
import { actionForDecision, describeOperation, verdictLabel } from '../src/lib/draft';
import { action, item } from './fakeBackend';

const VERDICTS: Verdicts = {
  term: { keep: { label: 'Keep as is', terminal: true }, keep_atomic: { label: 'Keep atomic', terminal: true } },
  slot: { keep: { label: 'Keep slot', terminal: true } },
};

describe('verdictLabel', () => {
  it('uses the label for the kind, any kind, else the id in words', () => {
    expect(verdictLabel('keep', 'term', VERDICTS)).toBe('Keep as is');
    expect(verdictLabel('keep', 'slot', VERDICTS)).toBe('Keep slot');
    expect(verdictLabel('keep_atomic', null, VERDICTS)).toBe('Keep atomic');
    expect(verdictLabel('set_cardinality', 'term', VERDICTS)).toBe('set cardinality');
    expect(verdictLabel('set_cardinality')).toBe('set cardinality');
  });
});

describe('describeOperation', () => {
  it('drops op and empty values, spells booleans and lists', () => {
    expect(describeOperation(null)).toEqual([]);
    expect(describeOperation({ op: 'set_cardinality', required: false, recommended: true, comment: '' }))
      .toEqual([['required', 'no'], ['recommended', 'yes']]);
    expect(describeOperation({ op: 'set_examples', examples: [{ value: 'a' }, { value: 'b' }] })).toEqual([['examples', 'a, b']]);
    expect(describeOperation({ object_label: 'x', tags: ['p', 'q'], n: 3 })).toEqual([['object label', 'x'], ['tags', 'p, q'], ['n', '3']]);
  });
});

describe('actionForDecision', () => {
  const accept = action({ label: 'Accept', verdict: 'adopt_mapping', operation: { predicate: 'p', object: 'A' } });
  const acceptB = action({ label: 'Accept B', verdict: 'adopt_mapping', operation: { predicate: 'p', object: 'B' } });
  const keep = action({ label: 'No match exists', verdict: 'keep' });
  const save = action({ label: 'Save new definition', verdict: 'edit_description', input: 'description' });
  const it1 = item(1, { choices: [{ text: 'c', detail: null, actions: [accept, acceptB] }], actions: [keep, save] });

  it('matches by verdict and the operation the button carries', () => {
    expect(actionForDecision(it1, { verdict: 'adopt_mapping', operation: { op: 'adopt_mapping', predicate: 'p', object: 'B' } })?.label).toBe('Accept B');
    expect(actionForDecision(it1, { verdict: 'keep', operation: null })?.label).toBe('No match exists');
    expect(actionForDecision(it1, { verdict: 'edit_description', operation: { op: 'edit_description', description: 'new' } })?.label)
      .toBe('Save new definition');
    expect(actionForDecision(it1, { verdict: 'merge', operation: null })).toBeUndefined();
  });
});
