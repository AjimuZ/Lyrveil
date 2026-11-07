import { Location } from '@angular/common';
import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import catalog from '../i18n/it.json';
import { routes } from './app.routes';

// The table of docs/frontend.md (Routes), without the leading slash.
const DOCUMENTED_PATHS = [
  'chats',
  'chats/:conversationId',
  'characters',
  'characters/new',
  'characters/:id',
  'characters/:id/edit',
  'characters/:id/memory',
  'gallery',
  'scenarios',
  'settings',
  'settings/models',
  'settings/resources',
  'settings/data',
];

describe('routes', () => {
  it('has exactly the documented screens, each loaded lazily with a catalog title', () => {
    const screens = routes.filter((route) => route.redirectTo === undefined);
    expect(screens.map((route) => route.path)).toEqual(DOCUMENTED_PATHS);
    for (const route of screens) {
      expect(route.loadComponent, route.path).toBeTypeOf('function');
      expect(route.component, route.path).toBeUndefined();
      expect(Object.keys(catalog), route.path).toContain(route.data?.['screen']);
    }
  });

  it('redirects the root and unknown paths to the chat list', async () => {
    // The chat list requests its rows on entry, so it needs an HTTP client; the requests stay unanswered here.
    TestBed.configureTestingModule({
      providers: [provideRouter(routes, withComponentInputBinding()), provideHttpClient(), provideHttpClientTesting()],
    });
    const harness = await RouterTestingHarness.create();

    await harness.navigateByUrl('/');
    expect(TestBed.inject(Location).path()).toBe('/chats');

    await harness.navigateByUrl('/does-not-exist');
    expect(TestBed.inject(Location).path()).toBe('/chats');
  });

  it('renders a lazy screen with its title and the not-available note', async () => {
    TestBed.configureTestingModule({ providers: [provideRouter(routes, withComponentInputBinding())] });
    const harness = await RouterTestingHarness.create();

    await harness.navigateByUrl('/settings');
    const heading = harness.routeNativeElement?.querySelector('h1');
    expect(heading?.textContent?.trim()).toBe('Impostazioni');
    expect(harness.routeNativeElement?.textContent).toContain('Questa schermata non è ancora disponibile.');
  });
});
