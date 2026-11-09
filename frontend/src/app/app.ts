// Root component: the brand with the notification bell, the main navigation and the outlet of the routed screens.
import { ChangeDetectionStrategy, Component } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { TranslatePipe } from './core/i18n/translate-pipe';
import { NotificationCenter } from './features/notifications/notification-center';

@Component({
  selector: 'lyr-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive, TranslatePipe, NotificationCenter],
  templateUrl: './app.html',
  styleUrl: './app.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class App {}
