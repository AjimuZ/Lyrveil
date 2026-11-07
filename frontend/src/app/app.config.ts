// Application providers: zoneless change detection, the router with route data bound to component inputs, HTTP.
import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import { APP_BASE_HREF } from '@angular/common';
import { provideRouter, withComponentInputBinding } from '@angular/router';

import { routes } from './app.routes';
import { provideApi } from './core/api/providers';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    // Zoneless is the default since Angular 21; stating it keeps the choice of ADR 0015 explicit.
    provideZonelessChangeDetection(),
    // The app is served from the root. index.html has no <base> element, because the Content-Security-Policy says
    // base-uri 'none'; the build writes absolute asset URLs (deployUrl in angular.json), so a client route that is
    // two segments deep, such as /chats/:conversationId, loads on a direct visit or a reload.
    { provide: APP_BASE_HREF, useValue: '/' },
    provideRouter(routes, withComponentInputBinding()),
    provideApi(),
  ],
};
