// The route table of docs/frontend.md (Routes): every screen loads lazily; unknown paths go to the chat list.
import { Route, Routes } from '@angular/router';

import { TextKey } from './core/i18n/translator';
import { leaveWithConfirmation } from './features/characters/leave-guard';

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

/**
 * Returns the route of the Character Creator. It is left with a confirmation when the form holds unsaved changes
 * (docs/frontend.md, screen 3); the `id` segment, when the path has one, is bound to the screen's `id` input.
 */
function creator(path: string, screen: TextKey): Route {
  return {
    path,
    loadComponent: () =>
      import('./features/characters/character-creator-screen').then((module) => module.CharacterCreatorScreen),
    canDeactivate: [leaveWithConfirmation],
    data: { screen },
  };
}

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'chats' },
  {
    path: 'chats',
    loadComponent: () => import('./features/chats/chat-list-screen').then((module) => module.ChatListScreen),
    data: { screen: 'screen.chats' satisfies TextKey },
  },
  {
    path: 'chats/:conversationId',
    loadComponent: () =>
      import('./features/conversation/conversation-screen').then((module) => module.ConversationScreen),
    data: { screen: 'screen.conversation' satisfies TextKey },
  },
  unavailable('characters', 'screen.characters'),
  creator('characters/new', 'screen.characterNew'),
  {
    path: 'characters/:id',
    loadComponent: () =>
      import('./features/characters/character-profile-screen').then((module) => module.CharacterProfileScreen),
    data: { screen: 'screen.characterProfile' satisfies TextKey },
  },
  creator('characters/:id/edit', 'screen.characterEdit'),
  {
    path: 'characters/:id/memory',
    loadComponent: () =>
      import('./features/memory/memory-studio-screen').then((module) => module.MemoryStudioScreen),
    data: { screen: 'screen.characterMemory' satisfies TextKey },
  },
  unavailable('gallery', 'screen.gallery'),
  {
    path: 'scenarios',
    loadComponent: () => import('./features/scenarios/scenarios-screen').then((module) => module.ScenariosScreen),
    data: { screen: 'screen.scenarios' satisfies TextKey },
  },
  unavailable('settings', 'screen.settings'),
  unavailable('settings/models', 'screen.settingsModels'),
  unavailable('settings/resources', 'screen.settingsResources'),
  unavailable('settings/data', 'screen.settingsData'),
  { path: '**', redirectTo: 'chats' },
];
