// The image request of the conversation screen: the form's limits, its validation, the body of the request and the
// text and fraction of the progress events. Pure functions, so the rules are tested without a component.
import type { TextKey } from '../../core/i18n/translator';

/** The aspects `POST /conversations/{id}/images` accepts; `auto` follows the framing the scene chooses. */
export const IMAGE_ASPECTS = ['auto', 'square', 'portrait', 'landscape', 'tall', 'wide'] as const;
export type ImageAspect = (typeof IMAGE_ASPECTS)[number];

/** The quality presets of the API: `alta` draws in two passes, `rapida` in one. */
export const IMAGE_QUALITIES = ['alta', 'rapida'] as const;
export type ImageQuality = (typeof IMAGE_QUALITIES)[number];

// These limits mirror lyrveil.services.images and lyrveil.images.workflows; the server checks them again.
export const MAX_IMAGE_REQUEST_CHARS = 1000;
export const MIN_STEPS = 10;
export const MAX_STEPS = 50;
export const MIN_CFG = 1;
export const MAX_CFG = 12;
export const MAX_SEED = 4294967295;

/** The outfit choice that lets the scene decide: the API's `null`. */
export const OUTFIT_FROM_SCENE = '';

/** What the image form holds. Numbers are kept as the text typed, so an empty field means "not set". */
export interface ImageForm {
  readonly request: string;
  readonly aspect: ImageAspect;
  /** `''` lets the scene choose, `usual` is the usual outfit, anything else is the key of a named outfit. */
  readonly outfit: string;
  readonly quality: ImageQuality;
  readonly seed: string;
  readonly steps: string;
  readonly cfg: string;
}

export const EMPTY_IMAGE_FORM: ImageForm = {
  request: '',
  aspect: 'auto',
  outfit: OUTFIT_FROM_SCENE,
  quality: 'alta',
  seed: '',
  steps: '',
  cfg: '',
};

/** The body of `POST /conversations/{id}/images`. */
export interface ImageRequestBody {
  readonly request: string;
  readonly aspect: ImageAspect;
  readonly outfit: string | null;
  readonly quality: ImageQuality;
  readonly seed: number | null;
  readonly steps: number | null;
  readonly cfg: number | null;
}

export type ImageFormField = 'request' | 'seed' | 'steps' | 'cfg';

/** The catalog text of each field that is not valid; a valid field has no entry. */
export type ImageFormErrors = Partial<Record<ImageFormField, TextKey>>;

const INTEGER = /^\d+$/;
const DECIMAL = /^\d+(\.\d+)?$/;

/** Returns the number in `text` when it is a plain integer, or null for an empty or malformed text. */
function integerOf(text: string): number | null {
  const trimmed = text.trim();
  return INTEGER.test(trimmed) ? Number(trimmed) : null;
}

function decimalOf(text: string): number | null {
  const trimmed = text.trim();
  return DECIMAL.test(trimmed) ? Number(trimmed) : null;
}

/**
 * Checks the form against the API's limits: a request of 1 to 1000 characters, a seed of 0 to 4294967295, steps of 10
 * to 50 and a CFG of 1 to 12. The three optional fields are valid when empty. An empty result means the form can be sent.
 */
export function validateImageForm(form: ImageForm): ImageFormErrors {
  const errors: ImageFormErrors = {};
  const request = form.request.trim();
  if (request === '') {
    errors.request = 'conv.image.error.request';
  } else if (request.length > MAX_IMAGE_REQUEST_CHARS) {
    errors.request = 'conv.image.error.requestLong';
  }
  if (form.seed.trim() !== '') {
    const seed = integerOf(form.seed);
    if (seed === null || seed > MAX_SEED) {
      errors.seed = 'conv.image.error.seed';
    }
  }
  if (form.steps.trim() !== '') {
    const steps = integerOf(form.steps);
    if (steps === null || steps < MIN_STEPS || steps > MAX_STEPS) {
      errors.steps = 'conv.image.error.steps';
    }
  }
  if (form.cfg.trim() !== '') {
    const cfg = decimalOf(form.cfg);
    if (cfg === null || cfg < MIN_CFG || cfg > MAX_CFG) {
      errors.cfg = 'conv.image.error.cfg';
    }
  }
  return errors;
}

/** Returns the request body of a form that `validateImageForm` accepted: empty optional fields become `null`. */
export function toImageBody(form: ImageForm): ImageRequestBody {
  return {
    request: form.request.trim(),
    aspect: form.aspect,
    outfit: form.outfit === OUTFIT_FROM_SCENE ? null : form.outfit,
    quality: form.quality,
    seed: integerOf(form.seed),
    steps: integerOf(form.steps),
    cfg: decimalOf(form.cfg),
  };
}

/** The phases of `image.progress`: the prompt stage, then ComfyUI's loading, first pass, second pass, and saving. */
export type ImagePhase = 'prompt' | 'loading' | 'sampling' | 'refining' | 'saving';

/** The latest `image.progress` of one image; `value` and `max` count the steps of a sampling or refining pass. */
export interface ImageProgress {
  readonly phase: ImagePhase;
  readonly value: number | null;
  readonly max: number | null;
}

export const PHASE_TEXT: Readonly<Record<ImagePhase, TextKey>> = {
  prompt: 'conv.image.phase.prompt',
  loading: 'conv.image.phase.loading',
  sampling: 'conv.image.phase.sampling',
  refining: 'conv.image.phase.refining',
  saving: 'conv.image.phase.saving',
};

function isPhase(value: unknown): value is ImagePhase {
  return typeof value === 'string' && Object.hasOwn(PHASE_TEXT, value);
}

function countOrNull(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
}

/** Returns the progress an `image.progress` payload carries, or null when its phase is not one the screen knows. */
export function toImageProgress(data: Readonly<Record<string, unknown>>): ImageProgress | null {
  const phase = data['phase'];
  if (!isPhase(phase)) {
    return null;
  }
  return { phase, value: countOrNull(data['value']), max: countOrNull(data['max']) };
}

/** The share of a pass that is done, from 0 to 1, or null while the phase has no count (a bar is not drawn then). */
export function progressFraction(progress: ImageProgress): number | null {
  if (progress.value === null || progress.max === null || progress.max <= 0) {
    return null;
  }
  return Math.min(1, progress.value / progress.max);
}
