import {
  EMPTY_IMAGE_FORM,
  ImageForm,
  MAX_IMAGE_REQUEST_CHARS,
  PHASE_TEXT,
  progressFraction,
  toImageBody,
  toImageProgress,
  validateImageForm,
} from './image-request';

const form = (overrides: Partial<ImageForm> = {}): ImageForm => ({ ...EMPTY_IMAGE_FORM, request: 'una foto al porto', ...overrides });

describe('validateImageForm', () => {
  it('accepts a request alone, and the optional fields at their limits', () => {
    expect(validateImageForm(form())).toEqual({});
    expect(validateImageForm(form({ seed: '0', steps: '10', cfg: '1' }))).toEqual({});
    expect(validateImageForm(form({ seed: '4294967295', steps: '50', cfg: '12' }))).toEqual({});
    expect(validateImageForm(form({ cfg: '6.5' }))).toEqual({});
  });

  it('refuses a blank request and one over 1000 characters', () => {
    expect(validateImageForm(form({ request: '   ' })).request).toBe('conv.image.error.request');
    expect(validateImageForm(form({ request: 'a'.repeat(MAX_IMAGE_REQUEST_CHARS) }))).toEqual({});
    expect(validateImageForm(form({ request: 'a'.repeat(MAX_IMAGE_REQUEST_CHARS + 1) })).request).toBe('conv.image.error.requestLong');
  });

  it('refuses steps outside 10 to 50, a CFG outside 1 to 12 and a seed that is not a number in range', () => {
    for (const steps of ['9', '51', '20.5', 'venti', '-10']) {
      expect(validateImageForm(form({ steps })).steps, `steps ${steps}`).toBe('conv.image.error.steps');
    }
    for (const cfg of ['0.9', '12.1', '1e1', 'sei', '-3']) {
      expect(validateImageForm(form({ cfg })).cfg, `cfg ${cfg}`).toBe('conv.image.error.cfg');
    }
    for (const seed of ['4294967296', '-1', '1.5', 'x']) {
      expect(validateImageForm(form({ seed })).seed, `seed ${seed}`).toBe('conv.image.error.seed');
    }
  });

  it('reports every wrong field at once', () => {
    expect(Object.keys(validateImageForm(form({ request: '', seed: 'x', steps: '1', cfg: '99' }))).sort()).toEqual([
      'cfg',
      'request',
      'seed',
      'steps',
    ]);
  });
});

describe('toImageBody', () => {
  it('sends null for the fields left empty and for the outfit the scene decides', () => {
    expect(toImageBody(form({ request: '  una foto  ' }))).toEqual({
      request: 'una foto',
      aspect: 'auto',
      outfit: null,
      quality: 'alta',
      seed: null,
      steps: null,
      cfg: null,
    });
  });

  it('sends the typed numbers and the chosen outfit, aspect and quality', () => {
    expect(toImageBody(form({ aspect: 'wide', outfit: 'usual', quality: 'rapida', seed: ' 42 ', steps: '30', cfg: '7.5' }))).toEqual({
      request: 'una foto al porto',
      aspect: 'wide',
      outfit: 'usual',
      quality: 'rapida',
      seed: 42,
      steps: 30,
      cfg: 7.5,
    });
  });
});

describe('image progress', () => {
  it('reads the phases the backend publishes and ignores any other', () => {
    for (const phase of Object.keys(PHASE_TEXT)) {
      expect(toImageProgress({ phase, value: null, max: null })?.phase).toBe(phase);
    }
    expect(toImageProgress({ phase: 'waiting' })).toBeNull();
    expect(toImageProgress({})).toBeNull();
  });

  it('keeps the step counts only when they are non-negative numbers', () => {
    expect(toImageProgress({ phase: 'sampling', value: 14, max: 28 })).toEqual({ phase: 'sampling', value: 14, max: 28 });
    expect(toImageProgress({ phase: 'sampling', value: '14', max: -1 })).toEqual({ phase: 'sampling', value: null, max: null });
  });

  it('gives the share of the pass, clamped to 1, and none while there is no count', () => {
    expect(progressFraction({ phase: 'sampling', value: 14, max: 28 })).toBe(0.5);
    expect(progressFraction({ phase: 'refining', value: 30, max: 20 })).toBe(1);
    expect(progressFraction({ phase: 'refining', value: 0, max: 20 })).toBe(0);
    expect(progressFraction({ phase: 'loading', value: null, max: null })).toBeNull();
    expect(progressFraction({ phase: 'sampling', value: 1, max: 0 })).toBeNull();
  });
});
