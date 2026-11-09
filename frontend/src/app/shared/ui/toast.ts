// ob-toast: short messages in the bottom corner, fed by the ToastService.
import { ChangeDetectionStrategy, Component, Injectable, inject, signal } from '@angular/core';

import { ObIcon } from './icon';

/** How long a toast stays on screen. */
export const TOAST_MS = 4000;

export type ToastKind = 'info' | 'success' | 'error';

export interface Toast {
  readonly id: number;
  readonly kind: ToastKind;
  readonly text: string;
}

/**
 * Queue of the toasts on screen. `show` adds one and removes it after TOAST_MS. A toast is a courtesy: an error must
 * also appear where the user is looking at it (an inline message), because the toast disappears by itself.
 */
@Injectable({ providedIn: 'root' })
export class ToastService {
  private nextId = 0;
  private readonly items = signal<readonly Toast[]>([]);
  /** The toasts on screen, oldest first. */
  readonly toasts = this.items.asReadonly();

  show(text: string, kind: ToastKind = 'info'): void {
    const id = this.nextId++;
    this.items.update((items) => [...items, { id, kind, text }]);
    setTimeout(() => this.dismiss(id), TOAST_MS);
  }

  dismiss(id: number): void {
    this.items.update((items) => items.filter((toast) => toast.id !== id));
  }
}

/**
 * The outlet: place it once in the shell. Its live region (`polite`) exists before the first toast, so each new toast
 * is announced when it appears. The icon repeats the kind that the color suggests.
 */
@Component({
  selector: 'ob-toast',
  imports: [ObIcon],
  template: `
    <div class="region" aria-live="polite" role="status">
      @for (toast of service.toasts(); track toast.id) {
        <div class="toast" [class]="toast.kind">
          <ob-icon [name]="toast.kind === 'error' ? 'error' : toast.kind === 'success' ? 'check' : 'bell'" />
          <span>{{ toast.text }}</span>
        </div>
      }
    </div>
  `,
  styleUrl: './toast.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ObToast {
  protected readonly service = inject(ToastService);
}
