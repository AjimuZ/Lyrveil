// Behavior of the display primitives: icon, window, pill, gauge, avatar, bubble, skeleton, activity, empty state.
import { Component, Type } from '@angular/core';
import { ComponentFixture, TestBed } from '@angular/core/testing';

import { ObActivity } from './activity';
import { ObAvatar, initialsOf } from './avatar';
import { ObBubble, parseBubble } from './bubble';
import { ObButton } from './button';
import { ObEmptyState } from './empty-state';
import { GAUGE_SEGMENTS, ObGauge, filledSegments } from './gauge';
import { ObIcon, SPRITE_URL } from './icon';
import { ObPill } from './pill';
import { ObSkeleton } from './skeleton';
import { find, findAll, textOf } from './test-dom';
import { ObWindow } from './window';

function render<T>(type: Type<T>, inputs: Record<string, unknown>): ComponentFixture<T> {
  const fixture = TestBed.createComponent(type);
  for (const [name, value] of Object.entries(inputs)) {
    fixture.componentRef.setInput(name, value);
  }
  fixture.detectChanges();
  return fixture;
}

const root = (fixture: ComponentFixture<unknown>): HTMLElement => fixture.nativeElement as HTMLElement;

/** The width attribute of the svg of an icon rendered with these inputs. */
const iconWidth = (inputs: Record<string, unknown>): string | null =>
  find(root(render(ObIcon, inputs)), 'svg').getAttribute('width');

describe('ob-icon', () => {
  it('points at its symbol of the sprite and is decorative without a label', () => {
    const svg = find(root(render(ObIcon, { name: 'chat' })), 'svg');
    expect(find(svg, 'use').getAttribute('href')).toBe(`${SPRITE_URL}#chat`);
    expect(svg.getAttribute('width')).toBe('16');
    expect(svg.getAttribute('height')).toBe('16');
    expect(svg.getAttribute('aria-hidden')).toBe('true');
    expect(svg.getAttribute('role')).toBeNull();
  });

  it('is an image with an accessible name when it has a label', () => {
    const svg = find(root(render(ObIcon, { name: 'trash', label: 'Elimina' })), 'svg');
    expect(svg.getAttribute('role')).toBe('img');
    expect(svg.getAttribute('aria-label')).toBe('Elimina');
    expect(svg.getAttribute('aria-hidden')).toBeNull();
  });

  it('draws empty-state icons at 24 px and scales in whole steps only', () => {
    expect(iconWidth({ name: 'empty-chat' })).toBe('24');
    expect(iconWidth({ name: 'empty-chat', scale: 2 })).toBe('48');
    expect(iconWidth({ name: 'chat', scale: 0.2 })).toBe('16');
    expect(iconWidth({ name: 'chat', scale: 2.4 })).toBe('32');
  });
});

describe('ob-window', () => {
  it('has corner marks only when it is primary', () => {
    const card = render(ObWindow, {});
    expect(findAll(root(card), '.mark').length).toBe(0);
    expect(root(card).classList.contains('primary')).toBe(false);

    const primary = render(ObWindow, { primary: true });
    const marks = findAll(root(primary), '.mark');
    expect(marks.length).toBe(4);
    expect(marks.every((mark) => mark.getAttribute('aria-hidden') === 'true')).toBe(true);
    expect(root(primary).classList.contains('primary')).toBe(true);
  });
});

@Component({
  selector: 'lyr-pill-host',
  imports: [ObPill],
  template: `<ob-pill tone="success">Pronto</ob-pill>`,
})
class PillHost {}

describe('ob-pill', () => {
  it('shows its word with a square that is hidden from assistive technology, and takes the tone as a class', () => {
    const fixture = TestBed.createComponent(PillHost);
    fixture.detectChanges();
    const pill = find(root(fixture), 'ob-pill');
    expect(textOf(pill).trim()).toBe('Pronto');
    expect(find(pill, '.square').getAttribute('aria-hidden')).toBe('true');
    expect(pill.classList.contains('success')).toBe(true);
  });
});

describe('ob-gauge', () => {
  it('fills the nearest whole number of segments of the real proportion, clamped to the track', () => {
    expect(filledSegments(0, 100)).toBe(0);
    expect(filledSegments(50, 100)).toBe(5);
    expect(filledSegments(12, 12)).toBe(GAUGE_SEGMENTS);
    expect(filledSegments(30, 12)).toBe(GAUGE_SEGMENTS);
    expect(filledSegments(6.2, 12)).toBe(5);
  });

  it('is unknown, never zero, for a missing or impossible value', () => {
    expect(filledSegments(null, 100)).toBeNull();
    expect(filledSegments(Number.NaN, 100)).toBeNull();
    expect(filledSegments(-1, 100)).toBeNull();
    expect(filledSegments(5, 0)).toBeNull();
  });

  it('is an image named by its label, with lit segments for a value and a striped track for none', () => {
    const known = render(ObGauge, { value: 5, max: 10, label: 'VRAM: 5 di 10 GB' });
    const track = find(root(known), '.track');
    expect(track.getAttribute('role')).toBe('img');
    expect(track.getAttribute('aria-label')).toBe('VRAM: 5 di 10 GB');
    expect(findAll(track, '.segment.on').length).toBe(5);
    expect(track.classList.contains('unknown')).toBe(false);

    const unknown = render(ObGauge, { value: null, label: 'VRAM: non disponibile' });
    const striped = find(root(unknown), '.track');
    expect(striped.classList.contains('unknown')).toBe(true);
    expect(findAll(striped, '.segment.on').length).toBe(0);
  });
});

describe('ob-avatar', () => {
  it('takes up to two initials from the name', () => {
    expect(initialsOf('Nerea Falchi')).toBe('NF');
    expect(initialsOf('  nerea  maria falchi ')).toBe('NM');
    expect(initialsOf('èlia')).toBe('È');
    expect(initialsOf('   ')).toBe('');
  });

  it('shows initials without a picture and the picture when there is one', () => {
    const initials = render(ObAvatar, { name: 'Nerea Falchi' });
    expect(textOf(find(root(initials), '.initials'))).toBe('NF');
    expect(findAll(root(initials), 'img').length).toBe(0);
    expect(root(initials).getAttribute('aria-hidden')).toBe('true');
    expect(root(initials).getAttribute('data-size')).toBe('40');

    const picture = render(ObAvatar, { name: 'Nerea Falchi', src: '/media/nerea.png', size: 64, alt: 'Nerea' });
    expect(find(root(picture), 'img').getAttribute('src')).toBe('/media/nerea.png');
    expect(root(picture).getAttribute('role')).toBe('img');
    expect(root(picture).getAttribute('aria-label')).toBe('Nerea');
    expect(root(picture).getAttribute('data-size')).toBe('64');
  });

  it('falls back to initials when the picture fails, and tries again for a new picture', () => {
    const fixture = render(ObAvatar, { name: 'Ilario Bassi', src: '/media/broken.png' });
    find(root(fixture), 'img').dispatchEvent(new Event('error'));
    fixture.detectChanges();
    expect(findAll(root(fixture), 'img').length).toBe(0);
    expect(textOf(find(root(fixture), '.initials'))).toBe('IB');

    fixture.componentRef.setInput('src', '/media/other.png');
    fixture.detectChanges();
    expect(find(root(fixture), 'img').getAttribute('src')).toBe('/media/other.png');
  });
});

describe('ob-bubble', () => {
  it('splits lines and turns *emphasis* into italic runs', () => {
    expect(parseBubble('ok, *ammetto* tutto\n\nfine')).toEqual([
      [
        { text: 'ok, ', italic: false },
        { text: 'ammetto', italic: true },
        { text: ' tutto', italic: false },
      ],
      [],
      [{ text: 'fine', italic: false }],
    ]);
    expect(parseBubble('a\r\nb').length).toBe(2);
  });

  it('leaves asterisks that do not wrap a word as written', () => {
    for (const text of ['2 * 3 * 4', 'a*b*c', '**forte**', '*aperto', '* *']) {
      expect(parseBubble(text), text).toEqual([[{ text, italic: false }]]);
    }
  });

  it('renders text, never markup, and colors the user bubble apart', () => {
    const element = root(render(ObBubble, { author: 'user', text: '<b>ciao</b> *piano*\nsecondo' }));
    expect(findAll(element, 'b').length).toBe(0);
    expect(findAll(element, 'p.line').length).toBe(2);
    expect(textOf(find(element, 'em'))).toBe('piano');
    expect(textOf(element)).toContain('<b>ciao</b>');
    expect(element.classList.contains('user')).toBe(true);
    expect(root(render(ObBubble, { author: 'character', text: 'x' })).classList.contains('user')).toBe(false);
  });
});

describe('ob-skeleton', () => {
  it('shows static blocks and says what is loading', () => {
    const fixture = render(ObSkeleton, { label: 'Carico le conversazioni' });
    expect(findAll(root(fixture), '.block').length).toBe(3);
    expect(find(root(fixture), '.blocks').getAttribute('aria-hidden')).toBe('true');
    expect(textOf(find(root(fixture), '[role="status"]'))).toBe('Carico le conversazioni');

    fixture.componentRef.setInput('lines', 5);
    fixture.detectChanges();
    expect(findAll(root(fixture), '.block').length).toBe(5);
  });
});

describe('ob-activity', () => {
  it('is a progress bar with no value, so it never implies a percentage', () => {
    const fixture = render(ObActivity, { label: 'Sto preparando il modello' });
    const bar = find(root(fixture), '[role="progressbar"]');
    expect(bar.getAttribute('aria-label')).toBe('Sto preparando il modello');
    expect(bar.hasAttribute('aria-valuenow')).toBe(false);
    expect(textOf(find(root(fixture), '.label'))).toBe('Sto preparando il modello');
  });
});

@Component({
  selector: 'lyr-empty-host',
  imports: [ObEmptyState, ObButton],
  template: `
    <ob-empty-state icon="empty-chat" message="Nessuna conversazione.">
      <ob-button variant="primary">Crea un personaggio</ob-button>
    </ob-empty-state>
  `,
})
class EmptyHost {}

describe('ob-empty-state', () => {
  it('shows its icon at twice the grid size, one sentence and the projected action', () => {
    const fixture = TestBed.createComponent(EmptyHost);
    fixture.detectChanges();
    const element = root(fixture);
    expect(find(element, 'svg').getAttribute('width')).toBe('48');
    expect(find(element, 'svg').getAttribute('aria-hidden')).toBe('true');
    expect(textOf(find(element, '.message'))).toBe('Nessuna conversazione.');
    expect(textOf(find(element, 'ob-button button')).trim()).toBe('Crea un personaggio');
  });
});
