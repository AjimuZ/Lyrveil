// Screen 9 of docs/frontend.md: the model profiles with their local state, the choice of the text profile,
// **Applica ora**, and the registration of a custom file.
import { ChangeDetectionStrategy, Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';

import { TranslatePipe } from '../../core/i18n/translate-pipe';
import { TextKey, Translator } from '../../core/i18n/translator';
import { FollowedJob, ModelSettingsStore } from './model-settings-store';
import {
  CUSTOM_TEMPLATES,
  CustomTemplate,
  FileState,
  LocalFile,
  ModelKind,
  ModelProfile,
  Tier,
} from './model-settings.model';

const STATE_TEXT = {
  verified: 'models.state.verified',
  size_mismatch: 'models.state.sizeMismatch',
  missing: 'models.state.missing',
} as const satisfies Record<FileState, TextKey>;

const TIER_TEXT = {
  target: 'models.tier.target',
  reduced: 'models.tier.reduced',
} as const satisfies Record<Tier, TextKey>;

const TEMPLATE_TEXT = {
  gemma4: 'models.template.gemma4',
  mistral_v3_tekken: 'models.template.mistral',
  chatml: 'models.template.chatml',
} as const satisfies Record<CustomTemplate, TextKey>;

/** One of the two lists on screen. */
interface Section {
  readonly kind: ModelKind;
  readonly title: TextKey;
  readonly profiles: readonly ModelProfile[];
  readonly activeId: string;
}

const GIGABYTE = 1024 ** 3;

/**
 * The model settings. **Modello di testo** and **Modello per immagini** list every profile of the catalog and every
 * registered custom file with its size, licence, tier and local state; a missing or wrong-size file shows its exact
 * download command with a copy button. A text profile above the machine's tier is listed but cannot be chosen. Choosing
 * a text profile writes the launch file and shows **Applica ora**, which reloads the model once the running reply
 * ends; the job is followed until it ends. **Aggiungi file locale** registers a file already in a model folder as a
 * custom profile, marked **non verificato**. The image profile and the quality preset are fixed by the backend, so the
 * screen offers no control for them.
 */
@Component({
  selector: 'lyr-model-settings-screen',
  imports: [TranslatePipe],
  templateUrl: './model-settings-screen.html',
  styleUrl: './model-settings-screen.scss',
  changeDetection: ChangeDetectionStrategy.OnPush,
})
export class ModelSettingsScreen implements OnInit, OnDestroy {
  protected readonly store = inject(ModelSettingsStore);
  private readonly translator = inject(Translator);
  private readonly sizes = new Intl.NumberFormat('it-IT', { maximumFractionDigits: 1 });

  protected readonly templates = CUSTOM_TEMPLATES.map((value) => ({ value, label: TEMPLATE_TEXT[value] }));
  protected readonly busy = signal(false);
  /** The id of the profile whose download command was just copied, or null. */
  protected readonly copied = signal<string | null>(null);
  /** The id of the custom profile whose removal waits for a confirmation, or null. */
  protected readonly confirmingRemove = signal<string | null>(null);

  protected readonly newKind = signal<ModelKind>('text');
  protected readonly newFile = signal('');
  protected readonly newTemplate = signal<CustomTemplate>('gemma4');

  protected readonly sections = computed<readonly Section[]>(() => {
    const settings = this.store.settings();
    if (settings === null) {
      return [];
    }
    return [
      { kind: 'text', title: 'models.text.title', profiles: settings.text, activeId: settings.active.text },
      { kind: 'image', title: 'models.image.title', profiles: settings.image, activeId: settings.active.image },
    ];
  });

  /** The unregistered files of the folder of the chosen kind. */
  protected readonly candidates = computed<readonly LocalFile[]>(() =>
    (this.store.settings()?.local_files ?? []).filter((item) => item.kind === this.newKind()),
  );

  /** The file the form will register: the chosen one while it is still listed, otherwise the first. */
  protected readonly chosenFile = computed(() => {
    const files = this.candidates();
    return files.find((item) => item.file === this.newFile())?.file ?? files[0]?.file ?? '';
  });

  ngOnInit(): void {
    this.store.clearActionError();
    void this.store.load();
  }

  ngOnDestroy(): void {
    this.store.stopFollowing();
  }

  protected stateText(profile: ModelProfile): TextKey {
    return STATE_TEXT[profile.state];
  }

  protected tierText(tier: Tier): TextKey {
    return TIER_TEXT[tier];
  }

  protected templateText(profile: ModelProfile): TextKey | null {
    const known = CUSTOM_TEMPLATES.find((value) => value === profile.template);
    return known === undefined ? null : TEMPLATE_TEXT[known];
  }

  /** Returns the size in gigabytes (1024³ bytes) with one decimal, in the interface locale. */
  protected size(bytes: number): string {
    return this.sizes.format(bytes / GIGABYTE);
  }

  /** Whether the machine's tier is below the profile's: it is listed but cannot be chosen. */
  protected aboveTier(profile: ModelProfile): boolean {
    return this.store.settings()?.tier === 'reduced' && profile.min_tier === 'target';
  }

  protected canChoose(kind: ModelKind, profile: ModelProfile, activeId: string): boolean {
    return kind === 'text' && profile.selectable === true && profile.id !== activeId && !this.busy();
  }

  protected async choose(profile: ModelProfile): Promise<void> {
    this.busy.set(true);
    try {
      await this.store.select(profile.id);
    } finally {
      this.busy.set(false);
    }
  }

  protected async apply(): Promise<void> {
    this.busy.set(true);
    try {
      await this.store.applyNow();
    } finally {
      this.busy.set(false);
    }
  }

  protected async copy(profile: ModelProfile): Promise<void> {
    if (profile.download_command === null) {
      return;
    }
    try {
      await navigator.clipboard.writeText(profile.download_command);
      this.copied.set(profile.id);
    } catch {
      // The command stays on screen for a manual copy; the button simply does not confirm.
      this.copied.set(null);
    }
  }

  protected async remove(profile: ModelProfile): Promise<void> {
    this.confirmingRemove.set(null);
    await this.store.unregister(profile.id);
  }

  protected async register(event: Event): Promise<void> {
    event.preventDefault();
    const file = this.chosenFile();
    if (file === '' || this.busy()) {
      return;
    }
    this.busy.set(true);
    try {
      await this.store.register(this.newKind(), file, this.newKind() === 'text' ? this.newTemplate() : null);
    } finally {
      this.busy.set(false);
    }
  }

  protected pickKind(event: Event): void {
    this.newKind.set(this.valueOf(event) === 'image' ? 'image' : 'text');
    this.newFile.set('');
  }

  protected pickTemplate(event: Event): void {
    const known = CUSTOM_TEMPLATES.find((value) => value === this.valueOf(event));
    if (known !== undefined) {
      this.newTemplate.set(known);
    }
  }

  protected valueOf(event: Event): string {
    return (event.target as HTMLInputElement | HTMLSelectElement).value;
  }

  /** The line that tells where a followed job stands. */
  protected jobText(job: FollowedJob): string {
    const queued: Record<FollowedJob['purpose'], TextKey> = {
      reload: 'models.reload.queued',
      register: 'models.register.queued',
    };
    const running: Record<FollowedJob['purpose'], TextKey> = {
      reload: 'models.reload.running',
      register: 'models.register.running',
    };
    const done: Record<FollowedJob['purpose'], TextKey> = {
      reload: 'models.reload.done',
      register: 'models.register.done',
    };
    const failed: Record<FollowedJob['purpose'], TextKey> = {
      reload: 'models.reload.failed',
      register: 'models.register.failed',
    };
    switch (job.status) {
      case 'queued':
        return this.translator.t(queued[job.purpose]);
      case 'running':
        return this.translator.t(running[job.purpose]);
      case 'completed':
        return this.translator.t(done[job.purpose]);
      default:
        return job.errorDetail ?? this.translator.t(failed[job.purpose]);
    }
  }

  protected jobFailed(job: FollowedJob): boolean {
    return job.status === 'failed' || job.status === 'cancelled';
  }
}
