import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { provideApi } from '../../core/api/providers';
import { LoreBook, TargetLink } from './lore.model';
import { WorldLinks } from './world-links';

function world(id: string, name: string): LoreBook {
  return {
    id,
    kind: 'world',
    character_id: null,
    name,
    description: '',
    scan_depth: 4,
    token_budget: null,
    status: 'active',
    entry_count: 0,
    created_at: '2026-11-27T10:00:00.000Z',
    updated_at: '2026-11-27T10:00:00.000Z',
  };
}

function link(id: string, book: LoreBook, overrides: Partial<TargetLink> = {}): TargetLink {
  return {
    id,
    book_id: book.id,
    character_id: 'c1',
    scenario_id: null,
    conversation_id: null,
    created_at: '2026-11-27T10:00:00.000Z',
    book_name: book.name,
    book_status: book.status,
    ...overrides,
  };
}

const ALDARA = world('w1', 'Aldara');
const BRUME = world('w2', 'Brume');

describe('WorldLinks', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<WorldLinks>;
  let root: HTMLElement;

  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      fixture.detectChanges();
    }
  }

  /** Answers the two reads of a load: the links of the target and the active worlds. */
  async function answerLoad(links: readonly TargetLink[], worlds: readonly LoreBook[]): Promise<void> {
    await vi.waitFor(() => backend.expectOne('/api/v1/lore/links?character_id=c1').flush({ items: links }));
    backend.expectOne('/api/v1/lore/worlds?status=active').flush({ items: worlds });
    await settle();
  }

  function button(label: string): HTMLButtonElement {
    const found = [...root.querySelectorAll<HTMLButtonElement>('button')].find((item) => (item.textContent ?? '').trim() === label);
    if (found === undefined) {
      throw new Error(`no button says ${label}`);
    }
    return found;
  }

  function pick(id: string): void {
    const select = root.querySelector<HTMLSelectElement>('select');
    if (select === null) {
      throw new Error('no picker');
    }
    select.value = id;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    fixture.detectChanges();
  }

  beforeEach(async () => {
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting(), provideRouter([])] });
    backend = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(WorldLinks);
    fixture.componentRef.setInput('kind', 'character');
    fixture.componentRef.setInput('targetId', 'c1');
    root = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
    await answerLoad([link('l1', ALDARA)], [ALDARA, BRUME]);
  });

  afterEach(() => backend.verify());

  it('lists the linked worlds and offers only the worlds not linked yet', () => {
    expect(root.querySelectorAll('[data-testid="world-link"]')).toHaveLength(1);
    expect(root.querySelector('[data-testid="world-link"]')?.textContent).toContain('Aldara');
    const options = [...root.querySelectorAll('option')].map((option) => option.textContent?.trim());
    expect(options).toEqual(['Scegli un mondo', 'Brume']);
    expect(button('Collega').disabled).toBe(true);
  });

  it('links the chosen world and reads the list again', async () => {
    pick('w2');
    expect(button('Collega').disabled).toBe(false);
    void button('Collega').click();
    const post = backend.expectOne('/api/v1/lore/books/w2/links');
    expect(post.request.method).toBe('POST');
    expect(post.request.body).toEqual({ character_id: 'c1' });
    post.flush(link('l2', BRUME), { status: 201, statusText: 'Created' });
    await answerLoad([link('l1', ALDARA), link('l2', BRUME)], [ALDARA, BRUME]);

    expect(root.querySelectorAll('[data-testid="world-link"]')).toHaveLength(2);
    expect([...root.querySelectorAll('option')].map((option) => option.textContent?.trim())).toEqual(['Scegli un mondo']);
  });

  it('unlinks a world without touching the world, then shows that none is linked', async () => {
    void button('Scollega').click();
    const removal = backend.expectOne('/api/v1/lore/links/l1');
    expect(removal.request.method).toBe('DELETE');
    removal.flush(null, { status: 204, statusText: 'No Content' });
    await answerLoad([], [ALDARA, BRUME]);

    expect(root.querySelectorAll('[data-testid="world-link"]')).toHaveLength(0);
    expect(root.textContent).toContain('Nessun mondo collegato.');
  });

  it("shows the server's reason and keeps the list when a link is refused", async () => {
    pick('w2');
    void button('Collega').click();
    backend
      .expectOne('/api/v1/lore/books/w2/links')
      .flush({ code: 'CONFLICT', detail: 'Il mondo è già collegato a questa destinazione.' }, { status: 409, statusText: 'Conflict' });
    await settle();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('Il mondo è già collegato');
    expect(root.querySelectorAll('[data-testid="world-link"]')).toHaveLength(1);
  });
});
