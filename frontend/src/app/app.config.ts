// Application providers: zoneless change detection, the router with route data bound to component inputs, HTTP.
import {
  ApplicationConfig,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import { provideRouter, withComponentInputBinding } from '@angular/router';

import { routes } from './app.routes';
import { provideApi } from './core/api/providers';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    // Zoneless is the default since Angular 21; stating it keeps the choice of ADR 0015 explicit.
    provideZonelessChangeDetection(),
    provideRouter(routes, withComponentInputBinding()),
    provideApi(),
  ],
};
