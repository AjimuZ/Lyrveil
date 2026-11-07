// The image form of the conversation screen: request, aspect, outfit, quality and the advanced seed, steps and CFG.
import { ChangeDetectionStrategy, Component, ElementRef, computed, inject, signal, viewChild } from '@angular/core';

import { ApiError } from '../../core/api/api-error';
import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey } from '../../core/i18n/translator';
import { Outfit } from '../characters/card.model';
import { ConversationStore } from './conversation-store';
import {
  EMPTY_IMAGE_FORM,
  IMAGE_ASPECTS,
  IMAGE_QUALITIES,
  ImageAspect,
  ImageForm,
  ImageQuality,
  OUTFIT_FROM_SCENE,
  toImageBody,
  validateImageForm,
} from './image-request';

const ASPECT_TEXT: Readonly<Record<ImageAspect, TextKey>> = {
  auto: 'conv.image.aspect.auto',
  square: 'conv.image.aspect.square',
  portrait: 'conv.image.aspect.portrait',
  landscape: 'conv.image.aspect.landscape',
  tall: 'conv.image.aspect.tall',
  wide: 'conv.image.aspect.wide',
};

const QUALITY_TEXT: Readonly<Record<ImageQuality, TextKey>> = {
  alta: 'conv.image.quality.alta',
  rapida: 'conv.image.quality.rapida',
};

/**
 * A modal `<dialog>` that asks for an image in the open chat. `open` shows it with the composer's text as the first
 * request and loads the character's named outfits. A field outside the API's limits shows its reason once a send was
 * tried, and the send is not made; a request the server refuses keeps the dialog open with the reason. A sent request
 * closes it: the placeholder and its progress then appear in the message list.
 */
@Component({
  selector: 'lyr-image-dialog',
  imports: [TranslatePipe],
  templateUrl: './image-dialog.html',
  styleUrl: './image-dialog.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ImageDialog {
  private readonly store = inject(ConversationStore);
  private readonly dialog = viewChild.required<ElementRef<HTMLDialogElement>>('dialog');

  protected readonly aspects = IMAGE_ASPECTS.map((value) => ({ value, label: ASPECT_TEXT[value] }));
  protected readonly qualities = IMAGE_QUALITIES.map((value) => ({ value, label: QUALITY_TEXT[value] }));
  protected readonly sceneOutfit = OUTFIT_FROM_SCENE;

  protected readonly form = signal<ImageForm>(EMPTY_IMAGE_FORM);
  protected readonly outfits = signal<readonly Outfit[]>([]);
  protected readonly attempted = signal(false);
  protected readonly busy = signal(false);
  protected readonly error = signal<ApiError | null>(null);
  /** What is wrong with the form now; the template shows it only after a send was tried. */
  protected readonly problems = computed(() => validateImageForm(this.form()));

  /** Shows the dialog with `request` in the first field and a clean form, then loads the outfits. */
  async open(request = ''): Promise<void> {
    this.form.set({ ...EMPTY_IMAGE_FORM, request });
    this.attempted.set(false);
    this.error.set(null);
    this.busy.set(false);
    this.dialog().nativeElement.showModal();
    this.outfits.set(await this.store.outfits());
  }

  protected close(): void {
    this.dialog().nativeElement.close();
  }

  protected set<K extends keyof ImageForm>(field: K, value: ImageForm[K]): void {
    this.form.update((current) => ({ ...current, [field]: value }));
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement).value;
  }

  protected async send(event: Event): Promise<void> {
    event.preventDefault();
    this.attempted.set(true);
    if (this.busy() || Object.keys(this.problems()).length > 0) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    const failure = await this.store.requestImage(toImageBody(this.form()));
    this.busy.set(false);
    if (failure === null) {
      this.close();
    } else {
      this.error.set(failure);
    }
  }
}
