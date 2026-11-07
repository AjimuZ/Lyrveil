// Avatar tools shared by the Creator and the profile: upload with preview, crop, variant choice and primary avatar.
import { ChangeDetectionStrategy, Component, DestroyRef, computed, effect, inject, input, signal, untracked } from '@angular/core';

import { ApiError, toApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { CharacterAsset, CropRect, squareCrop } from './asset.model';
import { CharactersStore } from './characters-store';

/**
 * Shows the avatars of `characterId` as variants, marks the primary one and offers: choosing a file and seeing it
 * before it is sent, cropping a variant to a square (saved as a new variant, the original stays) and making a variant
 * primary. The server is the source of truth: every action is followed by reading the list again. A refused upload or
 * crop shows the catalog text of its error code, never raw server text. Deleting a variant is not offered here.
 */
@Component({
  selector: 'lyr-avatar-tools',
  imports: [TranslatePipe],
  templateUrl: './avatar-tools.html',
  styleUrl: './avatar-tools.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class AvatarTools {
  private readonly store = inject(CharactersStore);
  private latestLoad = 0;

  readonly characterId = input.required<string>();

  protected readonly avatars = signal<readonly CharacterAsset[]>([]);
  protected readonly busy = signal(false);
  protected readonly error = signal<ApiError | null>(null);

  /** The file chosen and not yet sent, with the object URL of its preview. */
  protected readonly pending = signal<{ readonly file: File; readonly previewUrl: string } | null>(null);

  /** The variant being cropped and the three controls that place the square, in percent. */
  protected readonly cropping = signal<CharacterAsset | null>(null);
  protected readonly cropSize = signal(100);
  protected readonly cropX = signal(50);
  protected readonly cropY = signal(50);

  protected readonly cropRect = computed<CropRect | null>(() => {
    const asset = this.cropping();
    return asset === null ? null : squareCrop(asset.width, asset.height, this.cropSize(), this.cropX(), this.cropY());
  });

  constructor() {
    // External sync: the list follows the character the screen shows.
    effect(() => {
      const id = this.characterId();
      untracked(() => void this.load(id));
    });
    inject(DestroyRef).onDestroy(() => this.discard());
  }

  protected choose(event: Event): void {
    const input = event.target as HTMLInputElement;
    const file = input.files?.item(0) ?? null;
    this.discard();
    this.error.set(null);
    if (file !== null) {
      this.pending.set({ file, previewUrl: URL.createObjectURL(file) });
    }
    // The same file can be chosen again after a refusal.
    input.value = '';
  }

  protected cancelUpload(): void {
    this.discard();
  }

  protected async upload(): Promise<void> {
    const chosen = this.pending();
    if (chosen === null) {
      return;
    }
    await this.run(async () => {
      await this.store.uploadAvatar(this.characterId(), chosen.file);
      this.discard();
      await this.reload();
    });
  }

  protected startCrop(asset: CharacterAsset): void {
    this.error.set(null);
    this.cropSize.set(100);
    this.cropX.set(50);
    this.cropY.set(50);
    this.cropping.set(asset);
  }

  protected cancelCrop(): void {
    this.cropping.set(null);
  }

  protected async applyCrop(): Promise<void> {
    const asset = this.cropping();
    const rect = this.cropRect();
    if (asset === null || rect === null) {
      return;
    }
    await this.run(async () => {
      await this.store.cropAvatar(this.characterId(), asset.id, rect);
      this.cropping.set(null);
      await this.reload();
    });
  }

  protected async makePrimary(asset: CharacterAsset): Promise<void> {
    await this.run(async () => {
      await this.store.setPrimaryAvatar(this.characterId(), asset.id);
      await this.reload();
    });
  }

  protected numberOf(event: Event): number {
    return Number((event.target as HTMLInputElement).value);
  }

  /** Forgets the chosen file and releases its preview. */
  private discard(): void {
    const chosen = this.pending();
    if (chosen !== null) {
      URL.revokeObjectURL(chosen.previewUrl);
      this.pending.set(null);
    }
  }

  private async run(work: () => Promise<void>): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      await work();
    } catch (failure) {
      this.error.set(toApiError(failure));
    } finally {
      this.busy.set(false);
    }
  }

  private async reload(): Promise<void> {
    this.avatars.set(await this.store.avatars(this.characterId()));
  }

  private async load(id: string): Promise<void> {
    const ticket = ++this.latestLoad;
    this.avatars.set([]);
    this.cropping.set(null);
    try {
      const found = await this.store.avatars(id);
      if (ticket === this.latestLoad) {
        this.avatars.set(found);
      }
    } catch (failure) {
      if (ticket === this.latestLoad) {
        this.error.set(toApiError(failure));
      }
    }
  }
}
