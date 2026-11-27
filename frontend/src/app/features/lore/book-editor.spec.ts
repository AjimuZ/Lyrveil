import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { BookEditor } from './book-editor';
import { LoreBook, LoreEntry } from './lore.model';

function book(overrides: Partial<LoreBook> = {}): LoreBook {
  return {
    id: 'w1',
    kind: 'world',
    character_id: null,
    name: 'Aldara',
    description: 'Un regno di mare.',
    scan_depth: 4,
    token_budget: null,
    status: 'active',
    entry_count: 1,
    created_at: '2026-11-27T10:00:00.000Z',
    updated_at: '2026-11-27T10:00:00.000Z',
    ...overrides,
  };
}

function entry(id: string, overrides: Partial<LoreEntry> = {}): LoreEntry {
  return {
    id,
    book_id: 'w1',
    title: `Voce ${id}`,
    content: 'Un faro bianco sulla scogliera.',
    keys: ['faro'],
    secondary_keys: [],
    exclude_keys: [],
    case_sensitive: false,
    constant: false,
    enabled: true,
    priority: 0,
    insertion_order: 100,
    note: '',
    never_inserted: false,
    created_at: '2026-11-27T10:00:00.000Z',
    updated_at: '2026-11-27T10:00:00.000Z',
    ...overrides,
  };
}

const unprocessable = { status: 422, statusText: 'Unprocessable Entity' };

describe('BookEditor', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<BookEditor>;
  let root: HTMLElement;

  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      fixture.detectChanges();
    }
  }

  /** Opens the editor on `shown` with `entries` and answers its two reads. */
  async function open(shown: LoreBook, entries: readonly LoreEntry[]): Promise<void> {
    fixture = TestBed.createComponent(BookEditor);
    fixture.componentRef.setInput('bookId', shown.id);
    root = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
    backend.expectOne(`/api/v1/lore/books/${shown.id}`).flush(shown);
    backend.expectOne((request) => request.url === `/api/v1/lore/books/${shown.id}/entries`).flush({ items: entries });
    await settle();
  }

  /** Answers the reads that follow a change of the entries: the list and the book. */
  async function answerRefresh(shown: LoreBook, entries: readonly LoreEntry[]): Promise<void> {
    await vi.waitFor(() =>
      backend.expectOne((request) => request.url === `/api/v1/lore/books/${shown.id}/entries`).flush({ items: entries }),
    );
    await vi.waitFor(() => backend.expectOne(`/api/v1/lore/books/${shown.id}`).flush(shown));
    await settle();
  }

  function button(label: string): HTMLButtonElement {
    const found = [...root.querySelectorAll<HTMLButtonElement>('button')].find((item) =>
      (item.textContent ?? '').trim() === label,
    );
    if (found === undefined) {
      throw new Error(`no button says ${label}`);
    }
    return found;
  }

  function type(id: string, value: string): void {
    const control = root.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${id}`);
    if (control === null) {
      throw new Error(`no field ${id}`);
    }
    control.value = value;
    control.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
  }

  function submit(selector: string): void {
    root.querySelector(selector)?.dispatchEvent(new Event('submit', { cancelable: true }));
  }

  const text = (selector: string): string => root.querySelector(selector)?.textContent?.trim() ?? '';

  /** The `index`th element that matches `selector`; throws when there is none. */
  function nth(selector: string, index: number): HTMLElement {
    const found = root.querySelectorAll<HTMLElement>(selector)[index];
    if (found === undefined) {
      throw new Error(`no ${selector} at ${index}`);
    }
    return found;
  }

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => backend.verify());

  it('shows the settings and the entries, marking an entry too long to be inserted', async () => {
    await open(book(), [entry('a'), entry('b', { never_inserted: true, enabled: false, constant: true })]);

    expect(root.querySelector<HTMLInputElement>('#book-name')?.value).toBe('Aldara');
    expect(root.querySelector<HTMLInputElement>('#book-scan-depth')?.value).toBe('4');
    expect(root.querySelectorAll('.card')).toHaveLength(2);
    const second = nth('.card', 1);
    expect(second.textContent).toContain('Sempre attiva');
    expect(second.textContent).toContain('Disattivata');
    expect(second.querySelector('.warning')?.textContent).toContain('nessuna risposta la leggerà mai');
    expect(nth('.card', 0).querySelector('.warning')).toBeNull();
  });

  it('refuses a scan depth out of range before any request, and shows the server refusal next to its field', async () => {
    await open(book(), []);

    type('book-scan-depth', '21');
    submit('.form');
    fixture.detectChanges();
    expect(text('#book-scan-depth-error')).toBe('I messaggi da leggere devono essere un numero intero da 1 a 20.');
    backend.expectNone((request) => request.method === 'PUT');

    type('book-scan-depth', '6');
    type('book-budget', '300');
    type('book-name', 'Aldara II');
    submit('.form');
    const put = backend.expectOne('/api/v1/lore/books/w1');
    expect(put.request.method).toBe('PUT');
    expect(put.request.body).toEqual({
      name: 'Aldara II',
      description: 'Un regno di mare.',
      scan_depth: 6,
      token_budget: 300,
    });
    put.flush(
      { code: 'CONTENT_POLICY_BLOCKED', detail: 'Il campo «nome» descrive un minore o un contenuto che l\'app non consente.' },
      { status: 422, statusText: 'Unprocessable Entity' },
    );
    await settle();
    expect(text('#book-name-error')).toContain('«nome»');
    expect(root.querySelector('.form .error')).toBeNull();

    submit('.form');
    backend.expectOne('/api/v1/lore/books/w1').flush(book({ name: 'Aldara II', scan_depth: 6, token_budget: 300 }));
    await settle();
    expect(text('.saved')).toBe('Impostazioni salvate.');
    expect(root.querySelector('#book-name-error')).toBeNull();
  });

  it('creates an entry, reads the list again and announces an entry that is too long', async () => {
    await open(book({ entry_count: 0 }), []);
    expect(root.textContent).toContain('Questo libro non ha ancora voci.');

    button('Nuova voce').click();
    fixture.detectChanges();
    type('entry-content', 'Un testo lunghissimo.');
    submit('lyr-entry-form form');
    const post = backend.expectOne('/api/v1/lore/books/w1/entries');
    expect(post.request.method).toBe('POST');
    expect((post.request.body as { content: string }).content).toBe('Un testo lunghissimo.');
    post.flush(entry('n', { title: '', content: 'Un testo lunghissimo.', never_inserted: true }));
    await answerRefresh(book({ entry_count: 1 }), [entry('n', { title: '', content: 'Un testo lunghissimo.', never_inserted: true })]);

    expect(root.querySelector('lyr-entry-form')).toBeNull();
    expect(text('.warning[role="status"]')).toContain('«Un testo lunghissimo.»');
    expect(root.querySelectorAll('.card')).toHaveLength(1);
  });

  it('keeps the form open with the reason when the server refuses an entry', async () => {
    await open(book({ entry_count: 0 }), []);

    button('Nuova voce').click();
    fixture.detectChanges();
    type('entry-content', 'Un testo.');
    type('entry-keys', 'lui');
    submit('lyr-entry-form form');
    backend
      .expectOne('/api/v1/lore/books/w1/entries')
      .flush(
        { code: 'VALIDATION_ERROR', detail: 'La parola chiave «lui» non ha parole utili: contiene solo parole troppo comuni.' },
        unprocessable,
      );
    await settle();

    expect(text('#entry-keys-error')).toContain('«lui»');
    expect(root.querySelector('lyr-entry-form')).not.toBeNull();
  });

  it('switches an entry off, and deletes one after a confirmation', async () => {
    await open(book({ entry_count: 2 }), [entry('a'), entry('b')]);

    nth('button[aria-pressed]', 0).click();
    const put = backend.expectOne('/api/v1/lore/entries/a');
    expect(put.request.method).toBe('PUT');
    expect((put.request.body as { enabled: boolean; keys: string[] }).enabled).toBe(false);
    put.flush(entry('a', { enabled: false }));
    await answerRefresh(book({ entry_count: 2 }), [entry('a', { enabled: false }), entry('b')]);
    expect(nth('.card', 0).textContent).toContain('Disattivata');

    nth('.card .button.danger', 1).click();
    fixture.detectChanges();
    expect(root.textContent).toContain('Eliminare la voce «Voce b»?');
    expect(backend.match(() => true)).toHaveLength(0);
    button('Elimina voce').click();
    const removal = backend.expectOne('/api/v1/lore/entries/b');
    expect(removal.request.method).toBe('DELETE');
    removal.flush(null, { status: 204, statusText: 'No Content' });
    await answerRefresh(book({ entry_count: 1 }), [entry('a', { enabled: false })]);
    expect(root.querySelectorAll('.card')).toHaveLength(1);
  });

  it('archives a world and deletes it after a confirmation, then reports it removed', async () => {
    await open(book(), []);
    let removed = 0;
    fixture.componentInstance.removed.subscribe(() => removed++);

    button('Archivia').click();
    backend.expectOne('/api/v1/lore/books/w1/archive').flush(book({ status: 'archived' }));
    await settle();
    expect(text('.banner')).toContain('archiviato');
    expect(root.textContent).toContain('Ripristina');

    button('Elimina').click();
    fixture.detectChanges();
    expect(root.textContent).toContain('Eliminare il mondo «Aldara» con le sue voci?');
    button('Elimina mondo').click();
    const request = backend.expectOne('/api/v1/lore/books/w1');
    expect(request.request.method).toBe('DELETE');
    request.flush(null, { status: 204, statusText: 'No Content' });
    await settle();
    expect(removed).toBe(1);
  });

  it('edits only the scan depth and the budget of a character\'s own book', async () => {
    await open(book({ id: 'o1', kind: 'character', character_id: 'c1', name: 'Alba', entry_count: 0 }), []);

    expect(root.querySelector('#book-name')).toBeNull();
    expect(root.querySelector('#book-scan-depth')).not.toBeNull();
    expect(root.textContent).not.toContain('Archivia');
    expect(root.textContent).not.toContain('Elimina mondo');
  });
});
