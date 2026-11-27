import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { KeywordTest } from './keyword-test';

describe('KeywordTest', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<KeywordTest>;
  let root: HTMLElement;

  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      fixture.detectChanges();
    }
  }

  function type(value: string): void {
    const control = root.querySelector<HTMLTextAreaElement>('#keyword-test-text');
    if (control === null) {
      throw new Error('no text field');
    }
    control.value = value;
    control.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
  }

  function submit(): void {
    root.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
  }

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    backend = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(KeywordTest);
    fixture.componentRef.setInput('bookId', 'w1');
    root = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
  });

  afterEach(() => backend.verify());

  it('refuses a blank text before any request', async () => {
    type('   ');
    submit();
    await settle();
    backend.expectNone(() => true);
    expect(root.querySelector('#keyword-test-error')?.textContent).toContain('Scrivi un testo');
  });

  it('sends the pasted text and lists every entry with whether it would be active and why', async () => {
    type('Il faro brilla nella notte.');
    submit();
    const request = backend.expectOne('/api/v1/lore/books/w1/match');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ text: 'Il faro brilla nella notte.' });
    request.flush({
      items: [
        {
          entry_id: 'e1',
          title: 'Il faro',
          active: true,
          reason: 'matched',
          matched_key: 'faro',
          matched_secondary_key: null,
          excluded_key: null,
        },
        {
          entry_id: 'e2',
          title: 'La tempesta',
          active: false,
          reason: 'no_key_matched',
          matched_key: null,
          matched_secondary_key: null,
          excluded_key: null,
        },
      ],
    });
    await settle();

    expect(root.querySelector('[data-testid="keyword-test-summary"]')?.textContent).toContain('1 su 2 voci');
    const entries = root.querySelectorAll('[data-testid="keyword-test-entry"]');
    expect(entries).toHaveLength(2);
    expect(entries[0].textContent).toContain('Il faro');
    expect(entries[0].textContent).toContain('Compare la parola chiave «faro».');
    expect(entries[0].getAttribute('data-active')).toBe('true');
    expect(entries[1].textContent).toContain('Non attiva');
    expect(entries[1].textContent).toContain('Nessuna parola chiave compare nel testo.');
  });

  it("shows the server's reason and keeps the last result when a test fails", async () => {
    type('Il faro.');
    submit();
    backend.expectOne('/api/v1/lore/books/w1/match').flush({ items: [] });
    await settle();
    expect(root.textContent).toContain('Questo libro non ha voci da provare.');

    submit();
    backend
      .expectOne('/api/v1/lore/books/w1/match')
      .flush({ code: 'NOT_FOUND', message: 'x', detail: 'Libro di lore non trovato.' }, { status: 404, statusText: 'Not Found' });
    await settle();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('Libro di lore non trovato.');
    expect(root.textContent).toContain('Questo libro non ha voci da provare.');
  });
});
