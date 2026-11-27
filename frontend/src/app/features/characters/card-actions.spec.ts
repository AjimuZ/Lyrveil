import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { Component } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { provideApi } from '../../core/api/providers';
import { CardActions } from './card-actions';
import { cardImportFixture } from './card-import.fixtures';

@Component({ selector: 'lyr-stub', template: '' })
class Stub {}

describe('CardActions', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<CardActions>;
  let root: HTMLElement;
  let router: Router;

  /** Waits for the requests that were answered and renders until the screen stops changing. */
  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      fixture.detectChanges();
    }
  }

  /**
   * Picks `file` as a browser does: the input holds it, then `change` fires. A request it starts is still pending
   * afterwards, so the caller answers it before it settles. The text is given here because jsdom's `File` may lack it.
   */
  function pick(name: string, type: string, text: string): void {
    const input = root.querySelector<HTMLInputElement>('#card-import-file');
    if (input === null) {
      throw new Error('no import input');
    }
    const file = new File([text], name, { type });
    Object.defineProperty(file, 'text', { value: () => Promise.resolve(text) });
    Object.defineProperty(input, 'files', { configurable: true, value: { length: 1, item: () => file } });
    input.dispatchEvent(new Event('change', { bubbles: true }));
  }

  function button(label: string): HTMLButtonElement {
    const found = [...root.querySelectorAll<HTMLButtonElement>('button')].find((item) =>
      (item.textContent ?? '').includes(label),
    );
    if (found === undefined) {
      throw new Error(`no button says ${label}`);
    }
    return found;
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideApi(),
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([
          { path: 'characters/import/:importId', component: Stub },
          { path: 'characters/:id/edit', component: Stub },
        ]),
      ],
    });
    backend = TestBed.inject(HttpTestingController);
    router = TestBed.inject(Router);
    fixture = TestBed.createComponent(CardActions);
    root = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
  });

  afterEach(() => backend.verify());

  it('sends a card file as multipart and opens its preview, creating nothing', async () => {
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    pick('mara.png', 'image/png', 'PNG');
    const request = await vi.waitFor(() => backend.expectOne('/api/v1/characters/imports'));
    expect(request.request.method).toBe('POST');
    expect((request.request.body as FormData).get('file')).toBeInstanceOf(File);
    request.flush(cardImportFixture());
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith(['/characters/import', 'imp1']));
    backend.expectNone('/api/v1/characters');
  });

  it('imports a native file through the native route and opens the character', async () => {
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    pick('mara.lyrveil-character.json', 'application/json', '{"format":"lyrveil.character","format_version":1}');
    const request = await vi.waitFor(() => backend.expectOne('/api/v1/characters/import'));
    request.flush({ character: { id: 'c9' }, assets_imported: 0, assets_without_data: 0 });
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith(['/characters', 'c9', 'edit']));
  });

  it('sends a JSON card that is not native to the card import', async () => {
    const navigate = vi.spyOn(router, 'navigate').mockResolvedValue(true);
    pick('mara.json', 'application/json', '{"spec":"chara_card_v2","data":{}}');
    const request = await vi.waitFor(() => backend.expectOne('/api/v1/characters/imports'));
    request.flush(cardImportFixture());
    await vi.waitFor(() => expect(navigate).toHaveBeenCalledWith(['/characters/import', 'imp1']));
  });

  it('shows the refusal of a card with its Italian text and the source path', async () => {
    pick('mara.png', 'image/png', 'PNG');
    const request = await vi.waitFor(() => backend.expectOne('/api/v1/characters/imports'));
    request.flush(
      { code: 'CONTENT_POLICY_BLOCKED', detail: 'Il testo in «data.description» non è consentito.' },
      { status: 422, statusText: 'Unprocessable Entity' },
    );
    await settle();
    expect(root.querySelector('[role="alert"]')?.textContent).toContain('«data.description»');
  });

  it('offers no export for a screen without a stored character', () => {
    expect(root.textContent).toContain('Importa una card');
    expect(root.textContent).not.toContain('Esporta');
  });

  it('exports with the scenarios chosen and offers the original card of an imported character', async () => {
    fixture.componentRef.setInput('characterId', 'c1');
    fixture.detectChanges();
    button('Esporta').click();
    const scenarios = await vi.waitFor(() =>
      backend.expectOne((item) => item.url === '/api/v1/scenarios' && item.params.get('character_id') === 'c1'),
    );
    scenarios.flush({
      items: [
        { id: 's1', title: 'Il porto', opening_message: 'Ehi.' },
        { id: 's2', title: 'La sera', opening_message: 'Salve.' },
      ],
    });
    const source = await vi.waitFor(() => backend.expectOne('/api/v1/characters/c1/source'));
    source.flush(new Blob(['{}']), { headers: { 'Content-Disposition': 'attachment; filename="x.json"' } });
    await settle();

    expect(root.textContent).toContain('Scarica la card originale');
    const boxes = root.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
    boxes[1]?.click();
    boxes[0]?.click();
    await settle();

    // jsdom has no object URLs and would only log a refused navigation, so the browser's save step is replaced.
    const saved = vi.fn(() => 'blob:test');
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: saved });
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    button('Esporta come card PNG').click();
    const request = await vi.waitFor(() => backend.expectOne((item) => item.url === '/api/v1/characters/c1/card'));
    expect(request.request.params.get('format')).toBe('png');
    // Scenarios go in the order the list shows them, whichever was ticked first.
    expect(request.request.params.getAll('scenario_ids')).toEqual(['s1', 's2']);
    request.flush(new Blob([new Uint8Array([1])]), {
      headers: { 'Content-Disposition': 'attachment; filename="Mara.png"' },
    });
    await vi.waitFor(() => expect(saved).toHaveBeenCalled());
  });

  it('says a PNG needs an avatar when the server answers 409', async () => {
    fixture.componentRef.setInput('characterId', 'c1');
    fixture.detectChanges();
    button('Esporta').click();
    const scenarios = await vi.waitFor(() => backend.expectOne((item) => item.url === '/api/v1/scenarios'));
    scenarios.flush({ items: [] });
    const source = await vi.waitFor(() => backend.expectOne('/api/v1/characters/c1/source'));
    source.flush(new Blob([]), { status: 404, statusText: 'Not Found' });
    await settle();

    expect(root.textContent).not.toContain('Scarica la card originale');
    button('Esporta come card PNG').click();
    const request = await vi.waitFor(() => backend.expectOne((item) => item.url === '/api/v1/characters/c1/card'));
    request.flush(new Blob([]), { status: 409, statusText: 'Conflict' });
    await settle();
    expect(root.textContent).toContain('Per esportare un PNG serve un avatar principale.');
  });
});
