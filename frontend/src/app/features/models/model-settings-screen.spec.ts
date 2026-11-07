import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { provideApi } from '../../core/api/providers';
import { ModelSettingsScreen } from './model-settings-screen';
import { ModelProfile, ModelSettings } from './model-settings.model';

function profile(id: string, overrides: Partial<ModelProfile> = {}): ModelProfile {
  return {
    id,
    display_name: `Profilo ${id}`,
    file: `${id}.gguf`,
    bytes: 6975879296,
    license: 'Apache-2.0',
    license_url: 'https://example.test/license',
    min_tier: 'reduced',
    custom: false,
    state: 'verified',
    download_command: null,
    template: 'gemma4',
    selectable: true,
    quality_evidence: null,
    ...overrides,
  };
}

function settings(active: string): ModelSettings {
  return {
    tier: 'reduced',
    active: { text: active, image: 'img' },
    text: [
      profile('piccolo'),
      profile('altro'),
      profile('grande', {
        min_tier: 'target',
        state: 'missing',
        selectable: false,
        download_command: '.\\scripts\\lyrveil.cmd models -Model grande',
      }),
    ],
    image: [profile('img', { file: 'img.safetensors' })],
    local_files: [{ kind: 'text', file: 'mio.gguf', bytes: 5 }],
    launch: null,
  };
}

describe('ModelSettingsScreen', () => {
  let backend: HttpTestingController;
  let fixture: ComponentFixture<ModelSettingsScreen>;
  let root: HTMLElement;

  async function settle(): Promise<void> {
    for (let round = 0; round < 3; round++) {
      await fixture.whenStable();
      await new Promise((resolve) => setTimeout(resolve));
      fixture.detectChanges();
    }
  }

  const card = (name: string): HTMLElement => {
    const found = [...root.querySelectorAll<HTMLElement>('li.card')].find((item) => item.textContent?.includes(name));
    if (found === undefined) {
      throw new Error(`no card for ${name}`);
    }
    return found;
  };

  const button = (scope: Element, name: string): HTMLButtonElement => {
    const found = [...scope.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent?.trim() === name);
    if (found === undefined) {
      throw new Error(`no button named ${name}`);
    }
    return found;
  };

  beforeEach(async () => {
    TestBed.configureTestingModule({ providers: [provideApi(), provideHttpClientTesting()] });
    backend = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(ModelSettingsScreen);
    root = fixture.nativeElement as HTMLElement;
    fixture.detectChanges();
    backend.expectOne('/api/v1/settings/models').flush(settings('piccolo'));
    await settle();
  });

  afterEach(() => {
    fixture.destroy();
    backend.verify();
  });

  it('lists each profile with its size, licence, tier and state, and the download command of a missing one', () => {
    const active = card('Profilo piccolo');
    expect(active.textContent).toContain('In uso');
    expect(active.textContent).toContain('Verificato');
    expect(active.textContent).toContain('6,5 GB');
    expect(active.textContent).toContain('Licenza Apache-2.0');
    expect(active.textContent).toContain('Livello minimo: Ridotto');

    const missing = card('Profilo grande');
    expect(missing.textContent).toContain('Da scaricare');
    expect(missing.textContent).toContain('.\\scripts\\lyrveil.cmd models -Model grande');
    expect(missing.textContent).toContain('Serve un computer di livello Completo');
    expect(button(missing, 'Usa questo modello').disabled).toBe(true);
    expect(root.querySelector('.apply')).toBeNull();
  });

  it('chooses a profile, then shows Applica ora, which queues the reload', async () => {
    button(card('Profilo altro'), 'Usa questo modello').click();
    const patch = backend.expectOne('/api/v1/settings/models');
    expect(patch.request.method).toBe('PATCH');
    expect(patch.request.body).toEqual({ llm_profile_id: 'altro' });
    patch.flush(settings('altro'));
    await settle();

    expect(card('Profilo altro').textContent).toContain('In uso');
    button(root, 'Applica ora').click();
    await settle();
    const apply = backend.expectOne('/api/v1/settings/models/apply');
    expect(apply.request.method).toBe('POST');
    apply.flush({ id: 'j1', kind: 'maintenance', status: 'queued', queue_position: 1 });
    await settle();
    backend.expectOne('/api/v1/jobs/j1').flush({ id: 'j1', status: 'queued', error_code: null, error_detail: null });
    await settle();

    expect(root.textContent).toContain('In coda: il modello si ricarica dopo la risposta in corso.');
  });

  it('registers an unregistered file of the text folder with the chosen prompt format', async () => {
    const form = root.querySelector('form') as HTMLFormElement;
    (form.querySelector('#models-local-template') as HTMLSelectElement).value = 'chatml';
    form.querySelector('#models-local-template')?.dispatchEvent(new Event('change'));
    button(form, 'Registra file').click();
    await settle();

    const request = backend.expectOne('/api/v1/settings/models/custom');
    expect(request.request.body).toEqual({ kind: 'text', file: 'mio.gguf', template: 'chatml' });
    request.flush({ id: 'j2', kind: 'maintenance', status: 'queued', queue_position: 1 });
    await settle();
    backend.expectOne('/api/v1/jobs/j2').flush({ id: 'j2', status: 'running', error_code: null, error_detail: null });
    await settle();
    expect(root.textContent).toContain('Sto controllando il file…');
  });
});
