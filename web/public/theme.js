/* Theme picker for the website: the same six looks as the desktop app.
   Loaded as a plain script in <head> so the saved theme is applied before
   the page paints; the menu itself is added to the header once it exists. */
(function () {
  var THEMES = [
    { key: 'light', name: 'Normal Light', color: '#F5F7FA' },
    { key: 'brutal', name: 'New Brutalism', color: '#F6F8FB' },
    { key: 'editorial', name: 'Editorial Grid', color: '#FFFFFF' },
    { key: 'bento', name: 'Soft Bento', color: '#ECEFFB' },
    { key: 'iri', name: 'Iridescent', color: '#100E17' },
    { key: 'dark', name: 'Normal Dark', color: '#0F1729' },
  ];
  var KEY = 'presentia-theme';
  var DEFAULT = 'brutal';

  function find(key) {
    for (var i = 0; i < THEMES.length; i++) if (THEMES[i].key === key) return THEMES[i];
    return null;
  }
  function saved() {
    try { var v = localStorage.getItem(KEY); return find(v) ? v : DEFAULT; } catch (e) { return DEFAULT; }
  }
  function apply(key) {
    document.documentElement.setAttribute('data-theme', key);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', find(key).color);
  }

  var current = saved();
  apply(current);

  function build() {
    var header = document.querySelector('.site-header');
    if (!header || header.querySelector('.theme-menu')) return;

    var menu = document.createElement('details');
    menu.className = 'theme-menu';
    menu.innerHTML =
      '<summary class="theme-btn" aria-label="Choose a theme">' +
        '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' +
          '<path d="M12 22a10 10 0 1 1 10-10c0 2.8-2.2 4-4.5 4H15a2 2 0 0 0-1.5 3.3A1.6 1.6 0 0 1 12 22z"/>' +
          '<circle cx="7.5" cy="11.5" r="1.3"/><circle cx="10.5" cy="7" r="1.3"/><circle cx="15.5" cy="7.5" r="1.3"/>' +
        '</svg><span class="theme-btn-label">Theme</span>' +
      '</summary>' +
      '<div class="theme-pop">' +
        '<h2>Theme</h2><p>Pick how this page looks. Saved on this device.</p>' +
        '<div class="theme-grid" role="radiogroup" aria-label="Theme"></div>' +
      '</div>';

    var grid = menu.querySelector('.theme-grid');
    THEMES.forEach(function (t) {
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'theme-card';
      b.setAttribute('role', 'radio');
      b.setAttribute('data-key', t.key);
      b.setAttribute('aria-checked', String(t.key === current));
      b.innerHTML =
        '<span class="mini" data-mini="' + t.key + '" aria-hidden="true">' +
          '<span class="mini-top"></span>' +
          '<span class="mini-body"><span class="mini-title"></span>' +
            '<span class="mini-card"><span class="mini-line"></span><span class="mini-line"></span><span class="mini-btn"></span></span>' +
          '</span>' +
        '</span>' +
        '<span class="theme-card-name">' + t.name + '</span>' +
        '<span class="theme-card-check" aria-hidden="true">' +
          '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12l5 5 9-10"/></svg>' +
        '</span>';
      b.addEventListener('click', function () {
        current = t.key;
        apply(current);
        try { localStorage.setItem(KEY, current); } catch (e) { /* private mode */ }
        grid.querySelectorAll('.theme-card').forEach(function (c) {
          c.setAttribute('aria-checked', String(c.getAttribute('data-key') === current));
        });
      });
      grid.appendChild(b);
    });

    // Close on Escape or a click outside the menu.
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && menu.open) { menu.open = false; menu.querySelector('summary').focus(); }
    });
    document.addEventListener('click', function (e) {
      if (menu.open && !menu.contains(e.target)) menu.open = false;
    });

    var end = document.createElement('div');
    end.className = 'header-end';
    var nav = header.querySelector('.site-nav');
    if (nav) end.appendChild(nav);
    end.appendChild(menu);
    header.appendChild(end);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build);
  else build();
})();
