import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { GALLERY_PAGE_SIZE, GalleryStore } from './gallery-store';
import { GalleryImage } from './gallery.model';

function image(id: string): GalleryImage {
  return {
    id,
    message_id: null,
    conversation_id: 'c1',
    character_id: 'ch1',
    purpose: 'chat',
    width: 832,
    height: 1216,
    user_request: `foto ${id}`,
    aspect: 'portrait',
    outfit_key: 'usual',
    prompt_positive: 'p',
    prompt_negative: 'n',
    seed: 1,
    steps: 28,
    cfg: 5,
    profile_id: 'fake',
    quality_preset: 'alta',
    workflow_id: 'w',
    scene: {},
    created_at: '2026-11-07T10:00:00.000Z',
    url: `/media/images/${id}/original`,
    thumbnail_url: `/media/images/${id}/thumbnail`,
  };
}

const isList = (request: { url: string }): boolean => request.url === '/api/v1/images';

describe('GalleryStore', () => {
  let store: GalleryStore;
  let backend: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    store = TestBed.inject(GalleryStore);
    backend = TestBed.inject(HttpTestingController);
  });

  afterEach(() => backend.verify());

  it('asks for a page of the filters and leaves unset filters out', async () => {
    const loaded = store.load({ characterId: 'ch1', purpose: '' });
    const request = backend.expectOne(isList);
    expect(request.request.params.get('character_id')).toBe('ch1');
    expect(request.request.params.has('purpose')).toBe(false);
    expect(request.request.params.has('cursor')).toBe(false);
    expect(request.request.params.get('limit')).toBe(String(GALLERY_PAGE_SIZE));
    request.flush({ items: [image('a')], next_cursor: 'next' });
    await loaded;
    expect(store.items().map((item) => item.id)).toEqual(['a']);
    expect(store.hasMore()).toBe(true);
    expect(store.loading()).toBe(false);
  });

  it('appends the next page by cursor without repeating a row, and stops on the last page', async () => {
    const loaded = store.load();
    backend.expectOne(isList).flush({ items: [image('a'), image('b')], next_cursor: 'next' });
    await loaded;

    const more = store.loadMore();
    const request = backend.expectOne(isList);
    expect(request.request.params.get('cursor')).toBe('next');
    request.flush({ items: [image('b'), image('c')], next_cursor: null });
    await more;

    expect(store.items().map((item) => item.id)).toEqual(['a', 'b', 'c']);
    expect(store.hasMore()).toBe(false);
    await store.loadMore();
    backend.expectNone(isList);
  });

  it('drops the answer of a load that a newer one overtook', async () => {
    const first = store.load({ characterId: 'x', purpose: '' });
    const stale = backend.expectOne(isList);
    const second = store.load({ characterId: 'y', purpose: '' });
    const fresh = backend.expectOne(isList);
    fresh.flush({ items: [image('y')], next_cursor: null });
    stale.flush({ items: [image('x')], next_cursor: null });
    await Promise.all([first, second]);
    expect(store.items().map((item) => item.id)).toEqual(['y']);
  });

  it('deletes on the server and then removes the row; a refusal keeps it and sets the action error', async () => {
    const loaded = store.load();
    backend.expectOne(isList).flush({ items: [image('a'), image('b')], next_cursor: null });
    await loaded;

    const refused = store.remove(image('a'));
    backend
      .expectOne((request) => request.method === 'DELETE' && request.url === '/api/v1/images/a')
      .flush({ code: 'CONFLICT' }, { status: 409, statusText: 'Conflict' });
    expect(await refused).toBe(false);
    expect(store.actionError()).not.toBeNull();
    expect(store.items()).toHaveLength(2);

    const removed = store.remove(image('a'));
    backend
      .expectOne((request) => request.method === 'DELETE' && request.url === '/api/v1/images/a')
      .flush(null, { status: 204, statusText: 'No Content' });
    expect(await removed).toBe(true);
    expect(store.items().map((item) => item.id)).toEqual(['b']);
    expect(store.actionError()).toBeNull();
  });

  it('regenerates with the seed choice', async () => {
    const accepted = store.regenerate(image('a'), true);
    const request = backend.expectOne((r) => r.method === 'POST' && r.url === '/api/v1/images/a/regenerate');
    expect(request.request.body).toEqual({ keep_seed: true });
    request.flush({});
    expect(await accepted).toBe(true);
  });
});
