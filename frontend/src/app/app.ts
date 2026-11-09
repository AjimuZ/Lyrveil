// Root component: the brand with the notification bell, the main navigation and the outlet of the routed screens.
import { ChangeDetectionStrategy, Component, DestroyRef, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';

import { isConversationUrl, followVisualViewport } from './core/shell/viewport';
import { TranslatePipe } from './core/i18n/translate-pipe';
import { NotificationCenter } from './features/notifications/notification-center';

/**
 * The shell of docs/design-system.md (Layout). From 640 px up it is a sidebar (brand, bell and navigation) beside the
 * routed screen; below 640 px the navigation moves to a bar at the bottom, and a conversation takes the whole screen
 * (`immersive`) with neither bar. The shell is as tall as the visible viewport, so the screens scroll inside it and the
 * conversation composer stays above the on-screen keyboard.
 */
@Component({
  selector: 'lyr-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, TranslatePipe, NotificationCenter],
  templateUrl: './app.html',
  styleUrl: './app.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class App {
  private readonly router = inject(Router);

  private readonly url = toSignal(
    this.router.events.pipe(
      filter((event): event is NavigationEnd => event instanceof NavigationEnd),
      map((event) => event.urlAfterRedirects),
    ),
    { initialValue: this.router.url },
  );

  /** True on a conversation: below 640 px the shell hides its header and navigation there. */
  protected readonly immersive = computed(() => isConversationUrl(this.url()));

  constructor() {
    const stop = followVisualViewport(document.documentElement);
    inject(DestroyRef).onDestroy(stop);
  }
}
