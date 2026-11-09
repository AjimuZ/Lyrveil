import { foldText } from './chat-find';

describe('foldText', () => {
  it('ignores case and accents so a plain query finds an accented word', () => {
    expect(foldText('Perché', 'it-IT')).toBe(foldText('PERCHE', 'it-IT'));
    expect(foldText('città', 'it-IT')).toContain('citta');
  });

  it('keeps everything else, so different words stay different', () => {
    expect(foldText('ciao mondo', 'it-IT')).toBe('ciao mondo');
    expect(foldText('ciao', 'it-IT')).not.toBe(foldText('cia', 'it-IT'));
  });
});
