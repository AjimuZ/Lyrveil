import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { provideApi } from '../../core/api/providers';
import { MemoryDetail, MemoryItem } from './memory.model';
import { MemoryStudioScreen } from './memory-studio-screen';

@Component({ selector: 'lyr-stub', template: '' })
class Stub {}

function memory(id: string, overrides: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id,
    character_id: 'c1',
    scope: 'conversation',
    conversation_id: 'chat1',
    kind: 'user_fact',
    content: `Ricordo ${id}`,
    subject_key: null,
    value_text: null,
    provenance: 'stated',
    durability: 'lasting',
    status: 'active',
    importance: 3,
    confidence: 0.95,
    locked: false,
    needs_review: false,
    expires_at: null,
    conflict_group_id: null,
    version: 1,
    updated_at: '2026-11-07T10:00:00.000Z',
    source_count: 1,
    ...overrides,
  };
}

const CHAT = { id: 'chat1', title: 'Dario', character_id: 'c1' };
const isMemories = (request: { url: string }): boolean => request.url === '/api/v1/memories';

describe('MemoryStudioScreen', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<MemoryStudioScreen>;
  let root: HTMLElement;

  /** Lets the pending promises finish and renders. */
  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      await new Promise((resolve) => setTimeout(resolve));
      fixture.detectChanges();
    }
  }

  function buttonIn(scope: Element, name: string): HTMLButtonElement {
    const found = [...scope.querySelectorAll<HTMLButtonElement>('button')].find((candidate) => candidate.textContent?.trim() === name);
    if (found === undefined) {
      throw new Error(`no button named ${name}`);
    }
    return found;
  }

  async function click(scope: Element, name: string): Promise<void> {
    buttonIn(scope, name).click();
    await settle();
  }

  const cards = (): HTMLElement[] => [...root.querySelectorAll<HTMLElement>('[data-testid="memory-card"]')];

  /** Opens the screen for character c1 and answers the chat list and the first page of memories. */
  async function open(items: readonly MemoryItem[]): Promise<void> {
    fixture.componentRef.setInput('id', 'c1');
    fixture.detectChanges();
    backend.expectOne('/api/v1/conversations?character_id=c1').flush({ items: [CHAT] });
    backend.expectOne(isMemories).flush({ items, next_cursor: null });
    await settle();
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideApi(),
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([
          { path: 'characters/:id', component: Stub },
          { path: 'chats/:conversationId', component: Stub },
        ]),
      ],
    });
    backend = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(MemoryStudioScreen);
    root = fixture.nativeElement as HTMLElement;
  });

  afterEach(() => {
    backend.verify();
  });

  it('labels both memories of an unresolved contradiction and shows sources and history on request (T-MEM-11, T-MEM-08)', async () => {
    const rossi = memory('rossi', {
      content: 'Lavora allo Studio Rossi.',
      status: 'uncertain',
      conflict_group_id: 'g1',
      version: 2,
    });
    const bianchi = memory('bianchi', { content: 'Lavora allo Studio Bianchi.', status: 'uncertain', conflict_group_id: 'g1' });
    await open([rossi, bianchi]);

    expect(cards().map((card) => card.textContent)).toEqual([
      expect.stringContaining('Incerto'),
      expect.stringContaining('Incerto'),
    ]);
    for (const card of cards()) {
      expect(card.textContent).toContain('Contraddice un altro ricordo');
      expect(card.textContent).toContain('Affidabilità 95%');
      expect(card.textContent).toContain('Dichiarato');
    }

    await click(cards()[0] as HTMLElement, 'Fonti e cronologia');
    const detail: MemoryDetail = {
      ...rossi,
      sources: [{ message_id: 'm1', conversation_id: 'chat1', seq: 1, author: 'user', excerpt: 'Oggi ho iniziato allo Studio Rossi.', valid: true }],
      conflict: [{ id: 'bianchi', content: bianchi.content, status: 'uncertain', provenance: 'stated', confidence: 0.95 }],
    };
    backend.expectOne('/api/v1/memories/rossi').flush(detail);
    backend.expectOne('/api/v1/memories/rossi/revisions').flush({
      items: [
        {
          version: 1,
          actor: 'extraction',
          reason: 'contradiction',
          created_at: '2026-11-07T10:00:00.000Z',
          content: 'Lavora allo Studio Rossi.',
          status: 'active',
          provenance: 'stated',
          confidence: 0.95,
          importance: 3,
        },
      ],
    });
    await settle();

    const details = root.querySelector('[data-testid="memory-details"]')?.textContent ?? '';
    expect(details).toContain('Messaggio 1');
    expect(details).toContain('Oggi ho iniziato allo Studio Rossi.');
    expect(details).toContain('In contrasto con');
    expect(details).toContain('Lavora allo Studio Bianchi.');
    expect(details).toContain('Versione 1');
    expect(details).toContain('Contraddizione');
    expect(details).toContain('Diceva: Lavora allo Studio Rossi.');
  });

  it('edits a memory only with a valid text, and deletes it after a confirmation', async () => {
    await open([memory('a', { content: 'Ama il mare.' })]);
    const card = cards()[0] as HTMLElement;

    await click(card, 'Modifica');
    const field = root.querySelector<HTMLTextAreaElement>('#memory-text-a');
    expect(field?.value).toBe('Ama il mare.');
    (field as HTMLTextAreaElement).value = '  ';
    field?.dispatchEvent(new Event('input', { bubbles: true }));
    await click(card, 'Salva');
    expect(root.textContent).toContain('Il testo deve avere tra 1 e 280 caratteri.');
    backend.expectNone('/api/v1/memories/a');

    (field as HTMLTextAreaElement).value = 'Ama il lago.';
    field?.dispatchEvent(new Event('input', { bubbles: true }));
    await click(card, 'Salva');
    const patch = backend.expectOne('/api/v1/memories/a');
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ content: 'Ama il lago.', importance: 3 });
    patch.flush({ ...memory('a'), sources: [], conflict: [] });
    await settle();
    backend.expectOne(isMemories).flush({ items: [memory('a', { content: 'Ama il lago.', version: 2 })], next_cursor: null });
    await settle();
    expect(cards()[0]?.textContent).toContain('Ama il lago.');
    expect(root.querySelector('#memory-text-a')).toBeNull();

    await click(cards()[0] as HTMLElement, 'Elimina');
    expect(root.textContent).toContain('Eliminare questo ricordo?');
    backend.expectNone('/api/v1/memories/a');
    await click(cards()[0] as HTMLElement, 'Sì, elimina');
    const removal = backend.expectOne('/api/v1/memories/a');
    expect(removal.request.method).toBe('DELETE');
    removal.flush(null, { status: 204, statusText: 'No Content' });
    await settle();
    backend.expectOne(isMemories).flush({ items: [], next_cursor: null });
    await settle();
    expect(root.textContent).toContain('Non ci sono ricordi con questi filtri.');
  });

  it('shows the relationship in words first, with numbers only in the advanced section, and the events and summaries', async () => {
    await open([]);
    await click(root, 'Rapporto');
    expect(root.textContent).toContain('Scegli una conversazione per vedere questa sezione.');

    const select = root.querySelector<HTMLSelectElement>('#memory-conversation') as HTMLSelectElement;
    select.value = 'chat1';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    await settle();
    backend.expectOne(isMemories).flush({ items: [], next_cursor: null });
    await settle();
    backend.expectOne('/api/v1/conversations/chat1/relationship').flush({
      written: true,
      description: 'vi siete appena conosciuti, è prudente con te',
      narrative: '',
      expectations: '',
      boundaries: '',
      emotional_state: null,
      current_topic: null,
      open_threads: [],
      goals: [],
      narrative_changes: [],
      recent_events: [],
      bands: { familiarity: 'vi siete appena conosciuti', trust: 'è prudente con te', confidence: 'parla poco di sé', tension: 'nessuna tensione' },
      values: { familiarity: 10, trust: 22, confidence: 10, tension: 0 },
    });
    backend.expectOne('/api/v1/conversations/chat1/relationship/history').flush({
      items: [
        {
          id: 'h1',
          event_kind: 'relationship_change',
          dimension: 'trust',
          delta: 2,
          value_before: '20',
          value_after: '22',
          reason: 'Si confida.',
          status: 'applied',
          created_at: '2026-11-07T10:00:00.000Z',
          sources: [{ message_id: 'm5', conversation_id: 'chat1', seq: 5, author: 'user', excerpt: 'Sono stanchissima.', valid: true }],
        },
      ],
    });
    await settle();
    expect(root.querySelector('[data-testid="relationship-description"]')?.textContent).toContain('vi siete appena conosciuti');
    const history = root.querySelector('[data-testid="state-history"]')?.textContent ?? '';
    expect(history).toContain('Si confida.');
    expect(history).toContain('Messaggio 5');
    expect(root.querySelector('details')?.textContent).toContain('Valori numerici (avanzato)');

    await click(root, 'Eventi');
    const episodes = backend.expectOne((request) => request.url === '/api/v1/episodes');
    expect(episodes.request.params.get('conversation_id')).toBe('chat1');
    episodes.flush({
      items: [
        {
          id: 'e1',
          conversation_id: 'chat1',
          title: 'Primo giorno allo Studio Rossi',
          description: 'Racconta il suo primo giorno.',
          occurred_at: '2026-11-07T10:00:00.000Z',
          participants: ['Utente', 'Dario'],
          importance: 3,
          sources: [{ message_id: 'm1', conversation_id: 'chat1', seq: 1, author: 'user', excerpt: 'Ciao!', valid: true }],
          links: [{ direction: 'from', relation: 'causes', episode_id: 'e2', title: 'La festa' }],
        },
      ],
    });
    await settle();
    const episode = root.querySelector('[data-testid="episode"]')?.textContent ?? '';
    expect(episode).toContain('Primo giorno allo Studio Rossi');
    expect(episode).toContain('Con: Utente, Dario');
    expect(episode).toContain('Ha causato: La festa');
    expect(episode).toContain('Messaggio 1');

    await click(root, 'Riassunti');
    backend.expectOne('/api/v1/conversations/chat1/summaries').flush({
      items: [
        {
          id: 's1',
          level: 0,
          from_seq: 1,
          to_seq: 40,
          version: 2,
          status: 'active',
          text: 'Parlano del nuovo lavoro.',
          content: { events: ['Ha iniziato un lavoro.'], source_seqs: [1, 3] },
          token_count: 12,
          created_at: '2026-11-07T10:00:00.000Z',
        },
      ],
    });
    await settle();
    const summary = root.querySelector('[data-testid="summary"]')?.textContent ?? '';
    expect(summary).toContain('Messaggi da 1 a 40');
    expect(summary).toContain('Versione 2');
    expect(summary).toContain('Ha iniziato un lavoro.');
    expect(summary).toContain('Messaggi di origine: 1, 3');
  });
});
