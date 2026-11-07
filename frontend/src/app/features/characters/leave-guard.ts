// Route guard that asks before the Creator is left with unsaved changes.
import { inject } from '@angular/core';
import { CanDeactivateFn } from '@angular/router';

import { Translator } from '../../core/i18n/translator';

/** What the guard needs from a screen with a form. */
export interface HasUnsavedChanges {
  hasUnsavedChanges(): boolean;
}

/** Lets the navigation go when nothing is unsaved, otherwise asks in Italian and obeys the answer. */
export const leaveWithConfirmation: CanDeactivateFn<HasUnsavedChanges> = (screen) =>
  !screen.hasUnsavedChanges() || window.confirm(inject(Translator).t('creator.leave'));
