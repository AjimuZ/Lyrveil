import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ApiError } from '../../core/api/api-error';
import { EntryForm } from './entry-form';
import { EntryFields, LoreEntry } from './lore.model';

const ENTRY: LoreEntry = {
  id: 'e1',
  book_id: 'b1',
  title: 'Il faro',
  content: 'Un faro bianco sulla scogliera.',
  keys: ['faro', 'scogliera'],
  secondary_keys: ['mare'],
  exclude_keys: [],
  case_sensitive: true,
  constant: false,
  enabled: false,
  priority: 3,
  insertion_order: 250,
  note: 'Da rivedere',
  never_inserted: false,
  created_at: '2026-11-27T10:00:00.000Z',
  updated_at: '2026-11-27T10:00:00.000Z',
};

describe('EntryForm', () => {
  let fixture: ComponentFixture<EntryForm>;
  let root: HTMLElement;
  let saved: EntryFields[];
  let closed: number;

  function create(inputs: Record<string, unknown> = {}): void {
    fixture = TestBed.createComponent(EntryForm);
    for (const [name, value] of Object.entries(inputs)) {
      fixture.componentRef.setInput(name, value);
    }
    root = fixture.nativeElement as HTMLElement;
    saved = [];
    closed = 0;
    fixture.componentInstance.saved.subscribe((fields) => saved.push(fields));
    fixture.componentInstance.closed.subscribe(() => closed++);
    fixture.detectChanges();
  }

  function field(id: string): HTMLInputElement | HTMLTextAreaElement {
    const found = root.querySelector<HTMLInputElement | HTMLTextAreaElement>(`#${id}`);
    if (found === null) {
      throw new Error(`no field ${id}`);
    }
    return found;
  }

  function type(id: string, value: string): void {
    const control = field(id);
    control.value = value;
    control.dispatchEvent(new Event('input', { bubbles: true }));
    fixture.detectChanges();
  }

  function submit(): void {
    root.querySelector('form')?.dispatchEvent(new Event('submit', { cancelable: true }));
    fixture.detectChanges();
  }

  const text = (selector: string): string => root.querySelector(selector)?.textContent?.trim() ?? '';

  it('emits the typed fields, with the keys split and trimmed, and the defaults of a new entry', () => {
    create();
    type('entry-content', '  Un faro bianco.  ');
    type('entry-keys', ' faro , scogliera,faro');
    submit();

    expect(saved).toEqual([
      {
        title: '',
        content: 'Un faro bianco.',
        keys: ['faro', 'scogliera'],
        secondary_keys: [],
        exclude_keys: [],
        case_sensitive: false,
        constant: false,
        enabled: true,
        priority: 0,
        insertion_order: 100,
        note: '',
      },
    ]);
  });

  it('fills the form from the entry being edited', () => {
    create({ entry: ENTRY });
    expect(field('entry-title').value).toBe('Il faro');
    expect(field('entry-keys').value).toBe('faro, scogliera');
    expect(field('entry-secondary').value).toBe('mare');
    expect(field('entry-priority').value).toBe('3');
    expect(field('entry-order').value).toBe('250');
    submit();
    expect(saved[0]).toMatchObject({ case_sensitive: true, enabled: false, priority: 3, insertion_order: 250 });
  });

  it('shows each limit in Italian next to its field and emits nothing', () => {
    create();
    type('entry-title', 'x'.repeat(81));
    type('entry-keys', Array.from({ length: 17 }, (_, index) => `chiave${index}`).join(','));
    type('entry-secondary', 'y'.repeat(61));
    type('entry-priority', 'alto');
    type('entry-order', '2000000');
    type('entry-note', 'n'.repeat(501));
    submit();

    expect(saved).toEqual([]);
    expect(text('#entry-content-error')).toBe('Il contenuto non può essere vuoto.');
    expect(text('#entry-title-error')).toBe('Il titolo può avere al massimo 80 caratteri.');
    expect(text('#entry-keys-error')).toBe('Puoi indicare al massimo 16 parole.');
    expect(text('#entry-secondary-error')).toBe('Ogni parola può avere al massimo 60 caratteri.');
    expect(text('#entry-priority-error')).toContain('numero intero');
    expect(text('#entry-order-error')).toContain('numero intero');
    expect(text('#entry-note-error')).toBe('La nota può avere al massimo 500 caratteri.');
    expect(field('entry-title').getAttribute('aria-invalid')).toBe('true');
    expect(field('entry-title').getAttribute('aria-describedby')).toBe('entry-title-error');
  });

  it('shows the refusal of the server next to the field it names, or above the form when it names none', () => {
    create({ entry: ENTRY });
    fixture.componentRef.setInput(
      'error',
      new ApiError('VALIDATION_ERROR', 422, 'La parola chiave «mare» non ha parole utili: contiene solo parole troppo comuni.'),
    );
    fixture.detectChanges();
    expect(text('#entry-secondary-error')).toContain('«mare»');
    expect(root.querySelector('.error')).toBeNull();

    fixture.componentRef.setInput('error', new ApiError('CONFLICT', 409, 'Il libro ha già 500 voci.'));
    fixture.detectChanges();
    expect(root.querySelector('#entry-secondary-error')).toBeNull();
    expect(text('.error')).toBe('Il libro ha già 500 voci.');
  });

  it('warns that an entry too long to be inserted is never read, and closes without saving', () => {
    create({ entry: { ...ENTRY, never_inserted: true } });
    expect(text('.warning')).toContain('nessuna risposta la leggerà mai');

    root.querySelector<HTMLButtonElement>('button[type="button"]')?.click();
    expect(closed).toBe(1);
    expect(saved).toEqual([]);
  });
});
