import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { CurationError } from '../src/api/backend';
import type { Question } from '../src/api/types';
import { action, CURATOR, fakeBackend, item, page, VOTER } from './fakeBackend';
import { triggerIntersection } from './io';
import { renderAt } from './render';

const PATH = '/p/proj/review/vocabulary/signoff';
const rows = () => document.querySelectorAll('.review-item');
const row = (sid: string) => document.querySelector(`.review-item[data-sid="${sid}"]`) as HTMLElement;

const voted = (sid: string, answer = 'x'): Question => ({
  sid, review_type: 'vocabulary/signoff', state: 'open', outcome: null, decision_id: null, answers: [], total: 1, stale: 0,
  mine: { vote_id: 1, answer, proposal: { verdict: 'keep' }, comment: null, stale: false },
});

describe('review type page', () => {
  it('renders 40 rows of 500 and appends more when the sentinel shows', async () => {
    const items = Array.from({ length: 500 }, (_, i) => item(i));
    renderAt(PATH, fakeBackend(CURATOR, { page: page(items) }));
    await screen.findByText('term 0');
    expect(rows()).toHaveLength(40);
    act(() => triggerIntersection());
    await waitFor(() => expect(rows()).toHaveLength(80));
    expect(screen.getByTestId('progress')).toHaveTextContent('0 of 500 decided');
    expect(screen.getByTestId('hint')).toHaveTextContent('Decide from tally');
  });

  it('empty states: nothing of this kind, all answered, or no search match', async () => {
    renderAt(PATH, fakeBackend(VOTER, { page: page([]) }));
    expect(await screen.findByTestId('empty')).toHaveTextContent('No items of this kind right now.');
    cleanup();
    renderAt(PATH, fakeBackend(VOTER, { page: page([item(1)]), questions: [voted('term:T1')] }));
    expect(await screen.findByTestId('empty')).toHaveTextContent('All 1 items here are answered. Untick "Hide ones I answered" to see them.');
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'zzz' } });
    expect(screen.getByTestId('empty')).toHaveTextContent('Nothing here matches your search.');
  });

  it('search filters title, reasons and choice text; hide-answered hides answered rows', async () => {
    const items = [item(1), item(2, { reasons: ['Needle here'] }), item(3, { choices: [{ text: 'choice needle', detail: null, actions: [action()] }] })];
    renderAt(PATH, fakeBackend(VOTER, { page: page(items), questions: [voted('term:T1')] }));
    await screen.findByText('term 2');
    await waitFor(() => expect(row('term:T1')).toBeNull());
    expect(screen.getByTestId('progress')).toHaveTextContent('1 of 3 answered');
    fireEvent.click(screen.getByLabelText(/Hide ones I answered/));
    await screen.findByText('term 1');
    fireEvent.change(screen.getByLabelText('Search'), { target: { value: 'NEEDLE' } });
    await waitFor(() => expect(rows()).toHaveLength(2));
    expect(row('term:T1')).toBeNull();
  });

  it('answer sends the exact draft; the note replaces the rationale', async () => {
    const a = action({ label: 'Keep', verdict: 'keep', evidence: 'f:1', also: ['term:T9'], tone: 'primary' });
    const b = fakeBackend(VOTER, {
      page: page([item(1, { actions: [a] })]),
      index: { 'term:T9': { kind: 'term', name: 'T9', label: 'nine', slug: 't9', hash: 'h9', container: null, layer: 1, retired: false, state: 'open', score: 0 } },
    });
    renderAt(PATH, b);
    await screen.findByText('term 1');
    await userEvent.type(screen.getByLabelText('Note'), 'Because.');
    await userEvent.click(screen.getByRole('button', { name: 'Keep' }));
    await waitFor(() => expect(b.answer).toHaveBeenCalledTimes(1));
    expect(b.answer.mock.calls[0][0]).toMatchObject({ sid: 'term:T1', reviewType: 'vocabulary/signoff', hash: 'h1', comment: 'Because.' });
    expect(b.answer.mock.calls[0][1]).toEqual({
      subject: 'term:T1', verdict: 'keep', operation: null, rationale: 'Because.', also_affects: ['term:T9'],
      evidence: [{ kind: 'finding', id: 'f:1' }], subject_hashes: { 'term:T1': 'h1', 'term:T9': 'h9' }, supersedes: null,
    });
    // The vote line names the button that was clicked.
    expect(await screen.findByText(/Your vote: Keep/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retract' })).toBeInTheDocument();
  });

  it('editor actions validate and read the editor into the operation', async () => {
    const save = action({ label: 'Save examples', verdict: 'set_examples', input: 'examples' });
    const b = fakeBackend(VOTER, {
      page: page([item(1, { actions: [save], editor: { field: 'examples', value: '', placeholder: 'One per line' } })]),
    });
    renderAt(PATH, b);
    await screen.findByText('term 1');
    await userEvent.click(screen.getByRole('button', { name: 'Save examples' }));
    expect(await screen.findByText('Add at least one example (one per line).')).toBeInTheDocument();
    expect(b.answer).not.toHaveBeenCalled();
    await userEvent.type(screen.getByLabelText('Examples'), 'a{enter}b');
    await userEvent.click(screen.getByRole('button', { name: 'Save examples' }));
    await waitFor(() => expect(b.answer).toHaveBeenCalled());
    expect(b.answer.mock.calls[0][1].operation).toEqual({ op: 'set_examples', examples: [{ value: 'a' }, { value: 'b' }] });
  });

  it('confirm: cancel sends nothing; a 409 shows the reload hint', async () => {
    const del = action({ label: 'Delete', verdict: 'delete', tone: 'danger', confirm: 'Delete "term 1"?' });
    const b = fakeBackend(VOTER, { page: page([item(1, { actions: [del] })]) });
    b.answer.mockRejectedValueOnce(new CurationError(409, ['term:T1 changed']));
    renderAt(PATH, b);
    await screen.findByText('term 1');
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Delete "term 1"?');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(b.answer).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole('button', { name: 'Delete' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Delete' }));
    expect(await screen.findByText('Conflict: term:T1 changed. Reload the page.')).toBeInTheDocument();
  });

  it('retract takes the vote back and reopens the row', async () => {
    const b = fakeBackend(VOTER, { page: page([item(1)]) });
    renderAt(PATH, b);
    await screen.findByText('term 1');
    await userEvent.click(screen.getByRole('button', { name: 'Looks right' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Retract' }));
    await waitFor(() => expect(b.undo).toHaveBeenCalledWith({ kind: 'vote', vote: expect.objectContaining({ sid: 'term:T1' }) }));
    await waitFor(() => expect(screen.getByTestId('progress')).toHaveTextContent('0 of 1 answered'));
    expect(screen.getByRole('button', { name: 'Looks right' })).toBeInTheDocument();
  });

  it('approve all votes on each unanswered row in order and stops at the first failure', async () => {
    const items = [1, 2, 3, 4].map((i) => item(i, { group: 'G' }));
    const b = fakeBackend(VOTER, { page: page(items) });
    let calls = 0;
    const real = b.answer.getMockImplementation()!;
    b.answer.mockImplementation(async (q, d) => {
      calls += 1;
      if (calls === 3) throw new CurationError(400, ['bad']);
      return real(q, d);
    });
    renderAt(PATH, b);
    await screen.findByText('term 1');
    await userEvent.click(screen.getByRole('button', { name: 'Approve all in G' }));
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent('Approve all 4 undecided items in G as "looks right"? Each one is saved as your vote.');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Approve all' }));
    expect(await screen.findByText(/Stopped after 2 of 4/)).toBeInTheDocument();
    expect(b.answer.mock.calls.map((c) => c[0].sid)).toEqual(['term:T1', 'term:T2', 'term:T3']);
  });
});

describe('votes and tallies', () => {
  const choiceA = action({ label: 'Merge', verdict: 'merge', tone: 'danger' });
  const choiceB = action({ label: 'Different', verdict: 'keep', tone: 'neutral' });
  const it1 = item(1, { choices: [{ text: 'a vs b', detail: null, actions: [choiceA, choiceB] }], actions: [] });
  const question = (over: Partial<Question> = {}): Question => ({
    sid: 'term:T1', review_type: 'vocabulary/signoff', state: 'open', outcome: null, decision_id: null,
    answers: [
      { answer: choiceA.answer, count: 2, stale: 1, vote_ids: [7, 8], proposal: { verdict: 'merge', rationale: 'same' } },
      { answer: choiceB.answer, count: 1, stale: 0, vote_ids: [9], proposal: { verdict: 'keep', rationale: 'diff' } },
    ],
    total: 3, stale: 1, mine: null, ...over,
  });

  it('shows the tally; curators decide from the tally', async () => {
    const b = fakeBackend(CURATOR, { page: page([it1]), questions: [question()] });
    renderAt(PATH, b);
    await screen.findByText('term 1');
    expect(await screen.findByText(/Merge ×2/)).toBeInTheDocument();
    expect(screen.getByText(/1 out of date/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Decide from tally' }));
    await userEvent.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Decide' }));
    await waitFor(() => expect(b.resolve).toHaveBeenCalledWith({
      sid: 'term:T1', reviewType: 'vocabulary/signoff', outcome: 'decision', fromVote: 7,
    }));
    await userEvent.click(screen.getAllByRole('button', { name: 'Decide this' })[1]);
    const dialog = await screen.findByRole('dialog');
    await userEvent.type(within(dialog).getByRole('textbox'), 'Edited.');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Decide' }));
    await waitFor(() => expect(b.resolve).toHaveBeenLastCalledWith(expect.objectContaining({
      outcome: 'decision', decision: expect.objectContaining({ verdict: 'keep', rationale: 'Edited.', subject: 'term:T1' }),
    })));
  });

  it('a tie disables Decide from tally', async () => {
    const q = question();
    q.answers[1].count = 2;
    renderAt(PATH, fakeBackend(CURATOR, { page: page([it1]), questions: [q] }));
    expect(await screen.findByRole('button', { name: 'Decide from tally' })).toBeDisabled();
  });

  it('voters vote and see no decide buttons; their vote shows with Change and Retract', async () => {
    const b = fakeBackend(VOTER, {
      page: page([it1]),
      questions: [question({ mine: { vote_id: 9, answer: choiceB.answer, proposal: { verdict: 'keep' }, comment: null, stale: false } })],
    });
    renderAt(PATH, b);
    // Answered rows start hidden; show them.
    await userEvent.click(await screen.findByLabelText(/Hide ones I answered/));
    expect(await screen.findByText(/Your vote: Different/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Decide from tally' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Decide this' })).toBeNull();
    expect(screen.getByTestId('progress')).toHaveTextContent('1 of 1 answered');
    await userEvent.click(screen.getByRole('button', { name: 'Change' }));
    await userEvent.click(screen.getByRole('button', { name: 'Merge' }));
    await waitFor(() => expect(b.answer).toHaveBeenCalledWith(
      expect.objectContaining({ sid: 'term:T1', action: expect.objectContaining({ answer: choiceA.answer }) }),
      expect.objectContaining({ verdict: 'merge' }),
    ));
    await userEvent.click(screen.getByRole('button', { name: 'Retract' }));
    await waitFor(() => expect(b.undo).toHaveBeenCalledWith({ kind: 'vote', vote: expect.objectContaining({ sid: 'term:T1', review_type: 'vocabulary/signoff' }) }));
  });

  it('a stale vote asks to vote again', async () => {
    renderAt(PATH, fakeBackend(VOTER, {
      page: page([it1]),
      questions: [question({ mine: { vote_id: 9, answer: choiceB.answer, proposal: null, comment: null, stale: true } })],
    }));
    await userEvent.click(await screen.findByLabelText(/Hide ones I answered/));
    expect(await screen.findByText('Your vote is out of date: the term changed. Vote again.')).toBeInTheDocument();
  });
});
