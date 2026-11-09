// Root component: the brand with the notification bell, the main navigation and the outlet of the routed screens.
import { ChangeDetectionStrategy, Component, DestroyRef, Injector, afterNextRender, computed, inject } from '@angular/core';
import { takeUntilDestroyed, toSignal } from '@angular/core/rxjs-interop';
import { NavigationEnd, Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { filter, map } from 'rxjs';

import { Announcer } from './core/shell/announcer';
import { focusMainHeading, pathOf } from './core/shell/focus';
import { isConversationUrl, followVisualViewport } from './core/shell/viewport';
import { TranslatePipe } from './core/i18n/translate-pipe';
import { NotificationCenter } from './features/notifications/notification-center';
import { ShortcutHelp } from './features/shortcuts/shortcut-help';
import { ShortcutService } from './features/shortcuts/shortcut-service';

/**
 * The shell of docs/design-system.md (Layout). From 640 px up it is a sidebar (brand, bell and navigation) beside the
 * routed screen; below 640 px the navigation moves to a bar at the bottom, and a conversation takes the whole screen
 * (`immersive`) with neither bar. The shell is as tall as the visible viewport, so the screens scroll inside it and the
 * conversation composer stays above the on-screen keyboard.
 *
 * It also owns what is shared by every screen for keyboard and assistive technology use: a skip link, the global
 * shortcuts (`ShortcutService`) with their help dialog, the polite live region that `Announcer` feeds, and the move of
 * focus to the main heading when the path changes.
 */
@Component({
  selector: 'lyr-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, TranslatePipe, NotificationCenter, ShortcutHelp],
  templateUrl: './app.html',
  styleUrl: './app.scss',
  host: { '(document:keydown)': 'shortcuts.handle($event)' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class App {
  private readonly router = inject(Router);
  private readonly injector = inject(Injector);
  protected readonly shortcuts = inject(ShortcutService);
  protected readonly announcer = inject(Announcer);

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

    // A route change moves focus to the new screen's heading, so a keyboard or screen reader user starts at the top of
    // it instead of on a link that no longer exists. The first navigation is left alone (the page just loaded), as is
    // a change of query string only, and a pending Ctrl+K or Ctrl+N request, which puts focus somewhere better itself.
    let lastPath: string | null = null;
    this.router.events
      .pipe(
        filter((event): event is NavigationEnd => event instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe((event) => {
        const path = pathOf(event.urlAfterRedirects);
        const moved = lastPath !== null && path !== lastPath;
        lastPath = path;
        if (moved && this.shortcuts.pending() === null) {
          afterNextRender(() => focusMainHeading(), { injector: this.injector });
        }
      });
  }

  /** The skip link: it jumps to the screen's heading instead of following its `#content` href. */
  protected skipToContent(event: Event): void {
    event.preventDefault();
    focusMainHeading();
  }
}
