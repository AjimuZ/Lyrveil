// The route table of docs/frontend.md (Routes): every screen loads lazily; unknown paths go to the chat list.
import { Route, Routes } from '@angular/router';

import { TextKey } from './core/i18n/translator';

/**
 * Returns a lazy route for a screen that a later phase delivers. `data.screen` is the catalog key of its title,
 * bound to the placeholder's `screen` input. Delivering a screen replaces only `loadComponent`.
 */
function unavailable(path: string, screen: TextKey): Route {
  return {
    path,
    loadComponent: () => import('./core/shell/unavailable-screen').then((module) => module.UnavailableScreen),
    data: { screen },
  };
}

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'chats' },
  unavailable('chats', 'screen.chats'),
  unavailable('chats/:conversationId', 'screen.conversation'),
  unavailable('characters', 'screen.characters'),
  unavailable('characters/new', 'screen.characterNew'),
  unavailable('characters/:id', 'screen.characterProfile'),
  unavailable('characters/:id/edit', 'screen.characterEdit'),
  unavailable('characters/:id/memory', 'screen.characterMemory'),
  unavailable('gallery', 'screen.gallery'),
  unavailable('scenarios', 'screen.scenarios'),
  unavailable('settings', 'screen.settings'),
  unavailable('settings/models', 'screen.settingsModels'),
  unavailable('settings/resources', 'screen.settingsResources'),
  unavailable('settings/data', 'screen.settingsData'),
  { path: '**', redirectTo: 'chats' },
];
