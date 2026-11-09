// Screen 6 of docs/frontend.md: the image gallery with filters, paging and a lightbox.
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  OnInit,
  computed,
  inject,
  signal,
  viewChild,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, Translator } from '../../core/i18n/translator';
import { isTypingTarget } from '../../core/shell/shortcuts';
import { ObEmptyState } from '../../shared/ui/empty-state';
import { ObSkeleton } from '../../shared/ui/skeleton';
import { Character } from '../chats/chats.model';
import { GalleryStore } from './gallery-store';
import { GalleryImage, IMAGE_PURPOSES, ImagePurpose } from './gallery.model';

const PURPOSE_TEXT: Readonly<Record<ImagePurpose, TextKey>> = {
  chat: 'gallery.purpose.chat',
  avatar: 'gallery.purpose.avatar',
  profile: 'gallery.purpose.profile',
  scene: 'gallery.purpose.scene',
  variant: 'gallery.purpose.variant',
};

/**
 * The gallery. A grid of thumbnails (`/media/images/{id}/thumbnail`, so the list loads with ComfyUI off and never pulls
 * an original) that the browser fetches lazily, filtered by character and purpose, with **Carica altre** for the next
 * page. A thumbnail opens the lightbox: the original with its metadata (request, prompts, aspect, seed, steps, CFG,
 * profile, preset and workflow), a link to the source chat, **Rigenera** (a new variant in the source chat, with the
 * seed kept on request) and **Elimina** with an inline confirmation. ← and → (and two buttons) move to the previous or
 * next loaded image without closing the lightbox.
 */
@Component({
  selector: 'lyr-gallery-screen',
  imports: [RouterLink, TranslatePipe, ObEmptyState, ObSkeleton],
  templateUrl: './gallery-screen.html',
  styleUrl: './gallery-screen.scss',
  host: { '(document:keydown)': 'lightboxKey($event)' },
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class GalleryScreen implements OnInit {
  protected readonly store = inject(GalleryStore);
  private readonly translator = inject(Translator);
  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('lightbox');

  protected readonly purposes = IMAGE_PURPOSES.map((value) => ({ value, label: PURPOSE_TEXT[value] }));
  protected readonly characters = signal<readonly Character[]>([]);
  /** The image in the lightbox, or null while it is closed. */
  protected readonly selected = signal<GalleryImage | null>(null);
  protected readonly confirmingDelete = signal(false);
  protected readonly keepSeed = signal(false);
  protected readonly busy = signal(false);
  /** Set after **Rigenera** was accepted: the new image arrives in the source chat. */
  protected readonly regenerated = signal(false);

  ngOnInit(): void {
    this.store.clearActionError();
    void this.store.load();
    void this.store.listCharacters().then(
      (found) => this.characters.set(found),
      () => this.characters.set([]),
    );
  }

  protected filterCharacter(event: Event): void {
    void this.store.load({ ...this.store.filters(), characterId: this.valueOf(event) });
  }

  protected filterPurpose(event: Event): void {
    void this.store.load({ ...this.store.filters(), purpose: this.valueOf(event) as ImagePurpose | '' });
  }

  protected open(image: GalleryImage): void {
    this.store.clearActionError();
    this.confirmingDelete.set(false);
    this.keepSeed.set(false);
    this.regenerated.set(false);
    this.selected.set(image);
    this.dialog().nativeElement.showModal();
  }

  protected close(): void {
    this.dialog().nativeElement.close();
  }

  /** Where the lightbox image sits among the loaded thumbnails: its 1-based place and their count, or null. */
  protected readonly place = computed(() => {
    const image = this.selected();
    const index = image === null ? -1 : this.store.items().findIndex((item) => item.id === image.id);
    return index === -1 ? null : { index: index + 1, total: this.store.items().length };
  });

  /** Shows the image `delta` places away (-1 before, 1 after) in the same dialog. Does nothing at either end. */
  protected step(delta: -1 | 1): void {
    const place = this.place();
    const next = place === null ? undefined : this.store.items()[place.index - 1 + delta];
    if (next === undefined) {
      return;
    }
    this.store.clearActionError();
    this.confirmingDelete.set(false);
    this.keepSeed.set(false);
    this.regenerated.set(false);
    this.selected.set(next);
  }

  /**
   * ← and → move between images (docs/frontend.md, Keyboard), except where the arrows belong to a control. Only while
   * the lightbox is open: the modal dialog makes the rest of the page inert, so every such key comes from inside it.
   */
  protected lightboxKey(event: KeyboardEvent): void {
    if (this.selected() === null || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey || isTypingTarget(event.target)) {
      return;
    }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
      event.preventDefault();
      this.step(event.key === 'ArrowLeft' ? -1 : 1);
    }
  }

  protected positionText(): string {
    const place = this.place();
    return place === null ? '' : this.translator.t('gallery.lightbox.position', place);
  }

  /** Runs when the dialog closes, by the button or by Escape. */
  protected closed(): void {
    this.selected.set(null);
  }

  protected async confirmRemove(image: GalleryImage): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      if (await this.store.remove(image)) {
        this.close();
      }
    } finally {
      this.busy.set(false);
      this.confirmingDelete.set(false);
    }
  }

  protected async regenerate(image: GalleryImage): Promise<void> {
    if (this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      this.regenerated.set(await this.store.regenerate(image, this.keepSeed()));
    } finally {
      this.busy.set(false);
    }
  }

  protected characterName(image: GalleryImage): string {
    return this.characters().find((character) => character.id === image.character_id)?.full_name ?? '';
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLSelectElement).value;
  }

  protected checked(event: Event): boolean {
    return (event.target as HTMLInputElement).checked;
  }

  protected purposeText(purpose: ImagePurpose): TextKey {
    return PURPOSE_TEXT[purpose];
  }
}
