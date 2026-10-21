import { describe, expect, it } from 'vitest';
import { decisionAnswers, doneBy, madeSinceBuild, questionAnswers } from '../src/api/doneState';
import { decision } from './fakeBackend';

const row = { sid: 'term:A', findings: ['f:1', 'f:2'] };

describe('doneState', () => {
  it('a decision citing one of the row findings answers it', () => {
    expect(decisionAnswers(row, decision({ subject: 'term:A', evidence: [{ kind: 'finding', id: 'f:2' }] }))).toBe(true);
  });
  it('a decision citing no finding is a blanket answer', () => {
    expect(decisionAnswers(row, decision({ subject: 'term:A', evidence: [] }))).toBe(true);
    expect(decisionAnswers(row, decision({ subject: 'term:A', evidence: [{ kind: 'other', detail: 'x' }] }))).toBe(true);
  });
  it('a decision citing another finding does not', () => {
    expect(decisionAnswers(row, decision({ subject: 'term:A', evidence: [{ kind: 'finding', id: 'f:9' }] }))).toBe(false);
  });
  it('also_affects counts as targeting the row; another subject does not; withdrawn never', () => {
    expect(decisionAnswers(row, decision({ subject: 'term:B', also_affects: ['term:A'] }))).toBe(true);
    expect(decisionAnswers(row, decision({ subject: 'term:B' }))).toBe(false);
    expect(decisionAnswers(row, decision({ subject: 'term:A', status: 'withdrawn' }))).toBe(false);
  });
  it('madeSinceBuild compares numbers within one prefix only', () => {
    expect(madeSinceBuild({ id: 'DEC-0010' }, 'DEC-0010')).toBe(true);
    expect(madeSinceBuild({ id: 'DEC-0009' }, 'DEC-0010')).toBe(false);
    expect(madeSinceBuild({ id: 'H-0099' }, 'DEC-0010')).toBe(false);
    expect(madeSinceBuild({ id: 'H-0003' }, 'H-0002')).toBe(true);
    expect(madeSinceBuild({ id: 'DEC-0011' }, null)).toBe(false);
  });
  it('doneBy picks the newest answering decision made since the build', () => {
    const ds = [decision({ id: 'DEC-0005', subject: 'term:A' }), decision({ id: 'DEC-0011', subject: 'term:A' }),
      decision({ id: 'DEC-0012', subject: 'term:A', evidence: [{ kind: 'finding', id: 'f:9' }] })];
    expect(doneBy(row, ds, 'DEC-0010')?.id).toBe('DEC-0011');
  });
  it('a vote answers a row when sid and review type match', () => {
    const q = { sid: 'term:A', review_type: 'v/links' } as never;
    expect(questionAnswers(row, 'v/links', q)).toBe(true);
    expect(questionAnswers(row, 'v/other', q)).toBe(false);
  });
});
