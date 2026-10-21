import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { HistoryRow, ReviewIndex, SubjectIndex, SubjectView, Verdicts } from '../src/api/types';
import { CURATOR, decision, fakeBackend, LOCAL, VOTER } from './fakeBackend';
import { renderAt } from './render';

const REVIEW: ReviewIndex = {
  version: 2, title: 'Proj', schema_fingerprint: 'fp', decisions_next_id: 'DEC-0010',
  progress: { waiting: 12, percent_settled: 40.5 }, more_links: [{ label: 'Site', url: 'https://example.org' }],
  tracks: [
    { id: 'vocabulary', title: 'Vocabulary', blurb: 'Words.', kinds: ['term'], total: 12, types: [
      { id: 'links', title: 'Links', question: 'Linked?', answers: 'Accept · Reject', help: 'Help.', count: 7, sids: [] },
      { id: 'signoff', title: 'Sign-off', question: 'Right?', answers: 'Looks right', help: 'Quick.', count: 0, sids: [] },
    ] },
  ],
};

describe('review home', () => {
  it('shows tracks, cards and counts; empty types are dimmed', async () => {
    renderAt('/p/proj', fakeBackend('local', LOCAL, { review: REVIEW }));
    expect(await screen.findByText('Vocabulary')).toBeInTheDocument();
    expect(screen.getByTestId('progress')).toHaveTextContent('12 items waiting · 40.5% settled.');
    expect(screen.getByTestId('progress')).toHaveTextContent('saved as pending');
    const card = screen.getByRole('link', { name: /Links 7/ });
    expect(card).toHaveAttribute('href', '/p/proj/review/vocabulary/links');
    expect(screen.getByRole('link', { name: /Sign-off 0/ })).toHaveClass('type-card--empty');
  });

  it('card button previews are plain chips named after the real buttons, not links', async () => {
    renderAt('/p/proj', fakeBackend('local', LOCAL, { review: REVIEW }));
    await screen.findByText('Vocabulary');
    const previews = screen.getAllByTestId('answers');
    expect(previews.map((p) => p.textContent)).toEqual(['Buttons:AcceptReject', 'Buttons:Looks right']);
    const chips = previews.flatMap((p) => [...p.querySelectorAll('.type-card__answer')]);
    expect(chips.map((c) => c.textContent)).toEqual(['Accept', 'Reject', 'Looks right']);
    expect(chips.every((c) => c.tagName === 'SPAN' && !c.closest('a.type-card__answer'))).toBe(true);
    expect(screen.getByRole('list', { name: 'How it works' }).textContent).toContain('saved as a pending decision');
  });

  it('hosted: the steps say a click is a vote', async () => {
    renderAt('/p/proj', fakeBackend('hosted', VOTER, { review: REVIEW, questions: [] }));
    await screen.findByText('Vocabulary');
    expect(screen.getByRole('list', { name: 'How it works' }).textContent).toContain('Each answer is your vote');
  });

  it('hosted: answered counts, and waiting counts for curators', async () => {
    const q = (sid: string, mine: boolean, total: number) => ({
      sid, review_type: 'vocabulary/links', state: 'open' as const, outcome: null, decision_id: null, answers: [], total,
      stale: 0, mine: mine ? { vote_id: 1, answer: 'x', proposal: null, comment: null, stale: false } : null,
    });
    const data = { review: REVIEW, questions: [q('a', true, 1), q('b', false, 2), q('c', false, 0)] };
    renderAt('/p/proj', fakeBackend('hosted', CURATOR, data));
    expect(await screen.findByText('1 you have answered · 2 with votes waiting for a decision')).toBeInTheDocument();
    expect(screen.getByText(/Your answers are votes/)).toBeInTheDocument();
  });
});

const INDEX: SubjectIndex = {
  'slot:a': { kind: 'slot', name: 'a', label: 'a', slug: 'slot-a', hash: 'ha', container: null, layer: 2, retired: false, state: 'open', score: 1 },
  'term:X': { kind: 'term', name: 'X', label: 'ex', slug: 'term-x', hash: 'hx', container: null, layer: 1, retired: false, state: 'open', score: 1 },
  'term:Y': { kind: 'term', name: 'Y', label: 'why', slug: 'term-y', hash: 'hy', container: null, layer: 1, retired: false, state: 'open', score: 1 },
};
const VERDICTS: Verdicts = {
  term: {
    keep: { label: 'Keep as is', terminal: true },
    merge: { label: 'Merge', terminal: true, operation: { required: { into: 'sid:term', mappings: 'choice:move|drop' } } },
    set_flags: { label: 'Flags', terminal: true, operation: { required: { n: 'int' }, optional: { flag: 'bool', tags: 'str_list', map: 'map?' } } },
    seeded: { label: 'Seed', terminal: true, seed_only: true },
  },
};
const VIEW: SubjectView = {
  subject: { sid: 'term:X', kind: 'term', name: 'X', label: 'ex', slug: 'term-x', hash: 'hx', payload: { label: 'ex', tags: ['a', 'b'] } },
  status: { state: 'open', score: 1.5, open_findings: [] },
  findings: [{ id: 'f:1', rule: 'r', severity: 'warn', subjects: ['term:X'], message: 'msg', text: 'In plain words.', suggested_verdict: 'keep', open: true }],
  decisions: [decision({ id: 'DEC-0003', subject: 'term:X', status: 'pending' })],
  aliases: { forward: null, former: [] }, prompts: [], panels: [{ title: 'Usage', text: '5 files.' }], browse: null,
};

describe('subject page and decision control', () => {
  it('renders the subject, panels, history; verdict fields come from the schema', async () => {
    const b = fakeBackend('local', LOCAL, { index: INDEX, subject: VIEW, verdicts: VERDICTS });
    renderAt('/p/proj/subject/term-x', b);
    expect(await screen.findByRole('heading', { name: 'ex' })).toBeInTheDocument();
    expect(screen.getByText('5 files.')).toBeInTheDocument();
    expect(screen.getByText('DEC-0003', { selector: '.history-entry span' })).toBeInTheDocument();
    // The decision shows its verdict label, not the id; the finding its plain words, not its message.
    expect(screen.getByText('Keep as is', { selector: '.history-entry .badge' })).toBeInTheDocument();
    expect(screen.getByText('In plain words.')).toBeInTheDocument();
    expect(screen.queryByText('msg')).toBeNull();
    const select = screen.getByLabelText('Verdict') as HTMLSelectElement;
    expect(select.value).toBe('keep'); // suggested by the finding
    expect([...select.options].map((o) => o.value)).toEqual(['', 'keep', 'merge', 'set_flags']); // seed-only dropped
    await userEvent.selectOptions(select, 'set_flags');
    expect(screen.getByLabelText('n *')).toHaveAttribute('type', 'number');
    expect((screen.getByLabelText('flag') as HTMLSelectElement).tagName).toBe('SELECT');
    expect(screen.getByLabelText('tags').tagName).toBe('TEXTAREA');
    expect(screen.getByLabelText('map')).toHaveAttribute('placeholder', 'JSON');
  });

  it('save sends also_affects from sid fields and supersedes the latest decision', async () => {
    const b = fakeBackend('local', LOCAL, { index: INDEX, subject: VIEW, verdicts: VERDICTS });
    renderAt('/p/proj/subject/term-x', b);
    await screen.findByRole('heading', { name: 'ex' });
    await userEvent.selectOptions(screen.getByLabelText('Verdict'), 'merge');
    await userEvent.type(screen.getByLabelText('into *'), 'term:Y');
    await userEvent.selectOptions(screen.getByLabelText('mappings *'), 'move');
    const save = screen.getByRole('button', { name: 'Save as pending' });
    expect(save).toBeDisabled(); // rationale required
    await userEvent.type(screen.getByLabelText('Rationale'), 'Same thing.');
    await waitFor(() => expect(save).toBeEnabled());
    await userEvent.click(save);
    await waitFor(() => expect(b.decide).toHaveBeenCalled());
    expect(b.decide.mock.calls[0][0]).toEqual({
      subject: 'term:X', verdict: 'merge', operation: { op: 'merge', into: 'term:Y', mappings: 'move' },
      rationale: 'Same thing.', also_affects: ['term:Y'], evidence: [{ kind: 'finding', id: 'f:1' }],
      subject_hashes: { 'term:X': 'hx', 'term:Y': 'hy' }, supersedes: 'DEC-0003',
    });
    expect(screen.getByRole('button', { name: 'Withdraw' })).toBeInTheDocument();
  });

  it('hosted: voters get Vote (answer key = verdict); curators also get Decide', async () => {
    const b = fakeBackend('hosted', VOTER, { index: INDEX, subject: VIEW, verdicts: VERDICTS });
    renderAt('/p/proj/subject/term-x', b);
    await screen.findByRole('heading', { name: 'ex' });
    expect(screen.queryByRole('button', { name: 'Decide' })).toBeNull();
    await userEvent.type(screen.getByLabelText('Rationale'), 'Fine.');
    await userEvent.click(screen.getByRole('button', { name: 'Vote' }));
    await waitFor(() => expect(b.answer).toHaveBeenCalled());
    expect(b.answer.mock.calls[0][0]).toMatchObject({ sid: 'term:X', reviewType: 'subject', action: { answer: 'keep' }, hash: 'hx' });
    const c = fakeBackend('hosted', CURATOR, { index: INDEX, subject: VIEW, verdicts: VERDICTS });
    renderAt('/p/proj/subject/term-x', c);
    expect(await screen.findAllByRole('button', { name: 'Decide' })).toHaveLength(1);
  });
});

describe('subject page: where it is waiting', () => {
  it('links to the review pages that list the item', async () => {
    const review: ReviewIndex = { ...REVIEW, tracks: [{ ...REVIEW.tracks[0], types: [
      { ...REVIEW.tracks[0].types[0], sids: ['term:X'] }, { ...REVIEW.tracks[0].types[1], sids: ['term:Y'] },
    ] }] };
    renderAt('/p/proj/subject/term-x', fakeBackend('local', LOCAL, { index: INDEX, subject: VIEW, verdicts: VERDICTS, review }));
    await screen.findByRole('heading', { name: 'ex' });
    const line = await screen.findByTestId('waiting-on');
    expect(within(line).getAllByRole('link').map((a) => [a.textContent, a.getAttribute('href')]))
      .toEqual([['Vocabulary: Links', '/p/proj/review/vocabulary/links']]);
  });
});

describe('apply', () => {
  it('groups pending decisions under verdict labels and spells out operations', async () => {
    const b = fakeBackend('local', LOCAL, { index: INDEX, verdicts: VERDICTS });
    b.pending.mockResolvedValue({ ok: true, errors: {}, files: [], decisions: [
      { ...decision({ id: 'DEC-0001', subject: 'term:X', verdict: 'keep' }), effects: null },
      { ...decision({ id: 'DEC-0002', subject: 'term:Y', verdict: 'set_flags', operation: { op: 'set_flags', n: 2, flag: false } }), effects: null },
    ] });
    renderAt('/p/proj/apply', b);
    expect(await screen.findByRole('heading', { name: /Keep as is/ })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /Flags/ })).toBeInTheDocument();
    expect(screen.queryByText('set_flags')).toBeNull();
    expect(screen.getByText('n:').parentElement).toHaveTextContent('n: 2');
    expect(screen.getByText('flag:').parentElement).toHaveTextContent('flag: no');
    expect(screen.getByRole('button', { name: 'Apply all (2)' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Preview changes' })).toBeInTheDocument();
  });
});

describe('history', () => {
  const rows: HistoryRow[] = [
    { ...decision({ id: 'DEC-0002', subject: 'term:X', verdict: 'keep', status: 'pending', rationale: 'alpha' }), now: null, name: 'ex', kind: 'term', layer: 1, slug: 'term-x' },
    { ...decision({ id: 'DEC-0001', subject: 'slot:a', verdict: 'set_cardinality', status: 'applied', rationale: 'beta' }), now: null, name: 'a', kind: 'slot', layer: 2, slug: 'slot-a' },
  ];
  it('filters by kind and status, and searches', async () => {
    const b = fakeBackend('local', LOCAL, { index: INDEX, verdicts: VERDICTS });
    b.history.mockResolvedValue(rows);
    renderAt('/p/proj/history', b);
    await screen.findByText('DEC-0002');
    expect(screen.getByTestId('count')).toHaveTextContent('Showing 2 of 2.');
    // Verdicts in words: the label when verdicts.yaml knows the kind, else the id with spaces.
    expect(screen.getByText('Keep as is', { selector: '.badge' })).toBeInTheDocument();
    expect(screen.getByText('set cardinality', { selector: '.badge' })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'slot' } });
    expect(screen.getByTestId('count')).toHaveTextContent('Showing 1 of 2.');
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: 'pending' } });
    expect(screen.getByTestId('count')).toHaveTextContent('Showing 1 of 2.');
    fireEvent.change(screen.getByLabelText('Status'), { target: { value: '' } });
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'BETA' } });
    expect(screen.getByTestId('count')).toHaveTextContent('Showing 1 of 2.');
    expect(screen.queryByText('DEC-0002')).toBeNull();
  });
});

describe('team', () => {
  it('superadmins add, change and remove members', async () => {
    const can = { ...CURATOR, adminTeam: true, adminAccounts: true };
    const b = fakeBackend('hosted', can, {});
    b.team.mockResolvedValue([{ user_id: 1, name: 'Ann', email: 'a@x', role: 'voter' }]);
    b.approvedUsers.mockResolvedValue([{ id: 1, name: 'Ann', email: 'a@x' }, { id: 2, name: 'Bo', email: 'b@x' }]);
    renderAt('/p/proj/team', b);
    expect(await screen.findByText('Ann')).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText('Role of Ann'), 'curator');
    expect(b.setMember).toHaveBeenCalledWith(1, 'curator');
    const person = screen.getByLabelText('Person') as HTMLSelectElement;
    await waitFor(() => expect([...person.options].map((o) => o.value)).toEqual(['', '2'])); // Ann is already on the team
    await userEvent.selectOptions(person, '2');
    await userEvent.selectOptions(screen.getByLabelText('New role'), 'curator');
    await userEvent.click(screen.getByRole('button', { name: 'Add' }));
    expect(b.setMember).toHaveBeenLastCalledWith(2, 'curator');
    await userEvent.click(screen.getByRole('button', { name: 'Remove' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Remove' }));
    await waitFor(() => expect(b.removeMember).toHaveBeenCalledWith(1));
  });

  it('is not shown to anyone else', async () => {
    const b = fakeBackend('hosted', CURATOR, { review: REVIEW });
    renderAt('/p/proj/team', b);
    expect(await screen.findByText('Vocabulary')).toBeInTheDocument(); // redirected home
    expect(b.team).not.toHaveBeenCalled();
  });
});
