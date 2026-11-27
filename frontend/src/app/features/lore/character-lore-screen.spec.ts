import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { provideApi } from '../../core/api/providers';
import { CharacterLoreScreen } from './character-lore-screen';
import { LoreBook } from './lore.model';

const OWN: LoreBook = {
  id: 'o1',
  kind: 'character',
  character_id: 'c1',
  name: 'Alba',
  description: '',
  scan_depth: 4,
  token_budget: null,
  status: 'active',
  entry_count: 0,
  created_at: '2026-11-27T10:00:00.000Z',
  updated_at: '2026-11-27T10:00:00.000Z',
};

describe('CharacterLoreScreen', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<CharacterLoreScreen>;
  let root: HTMLElement;

  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      fixture.detectChanges();
    }
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideApi(), provideHttpClientTesting(), provideRouter([])],
    });
    backend = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(CharacterLoreScreen);
    fixture.componentRef.setInput('id', 'c1');
    root = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
  });

  afterEach(() => backend.verify());

  it('opens the own book of the character in the entry editor, with a way back to the profile', async () => {
    backend.expectOne('/api/v1/characters/c1/lore').flush(OWN);
    await settle();
    backend.expectOne('/api/v1/lore/books/o1').flush(OWN);
    backend.expectOne((request) => request.url === '/api/v1/lore/books/o1/entries').flush({ items: [] });
    await settle();

    expect(root.querySelector('h1')?.textContent?.trim()).toBe('Lore del personaggio');
    expect(root.querySelector('a.button')?.getAttribute('href')).toBe('/characters/c1');
    expect(root.querySelector('lyr-book-editor')).not.toBeNull();
    expect(root.textContent).toContain('Nuova voce');
    expect(root.querySelector('#book-name')).toBeNull();
  });

  it('says that the character does not exist and offers to try again', async () => {
    backend
      .expectOne('/api/v1/characters/c1/lore')
      .flush({ code: 'NOT_FOUND', detail: 'Personaggio non trovato.' }, { status: 404, statusText: 'Not Found' });
    await settle();

    expect(root.querySelector('[role="alert"]')?.textContent).toContain('Elemento non trovato.');
    expect(root.querySelector('lyr-book-editor')).toBeNull();
  });
});
