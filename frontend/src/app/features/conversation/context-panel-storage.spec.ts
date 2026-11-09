import { loadPanelCollapsed, savePanelCollapsed } from './context-panel-storage';

describe('context panel storage', () => {
  afterEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('is open until the user collapses it, and remembers the choice', () => {
    expect(loadPanelCollapsed()).toBe(false);
    savePanelCollapsed(true);
    expect(loadPanelCollapsed()).toBe(true);
    savePanelCollapsed(false);
    expect(loadPanelCollapsed()).toBe(false);
  });

  it('tolerates a browser that refuses storage', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('denied');
    });
    expect(loadPanelCollapsed()).toBe(false);
    expect(() => savePanelCollapsed(true)).not.toThrow();
  });
});
