"""Stagehand-style snapshot and act-by-ID (a PR 2 prototype, loaded into every call)."""

_SNAP_JS = r"""
(() => {
  const out = []; let n = 0;
  const clear = d => { d.querySelectorAll('[data-gid]').forEach(e => e.removeAttribute('data-gid')); d.querySelectorAll('iframe').forEach(f => { try { clear(f.contentDocument) } catch (x) {} }); d.querySelectorAll('*').forEach(e => e.shadowRoot && clear(e.shadowRoot)); };
  clear(document);
  const interactive = 'a[href],button,input,select,textarea,summary,[role=button],[role=option],[role=combobox],[role=checkbox],[role=link],[tabindex]:not([tabindex="-1"])';
  const visible = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
  const label = e => (e.getAttribute('aria-label') || (e.labels && e.labels[0] && e.labels[0].innerText) || e.placeholder || e.title || e.innerText || e.value || '').trim().replace(/\s+/g, ' ').slice(0, 60);
  const walk = (root, frame) => {
    for (const e of root.querySelectorAll('*')) {
      if (e.shadowRoot) walk(e.shadowRoot, frame);
      if (e.tagName === 'IFRAME') { try { walk(e.contentDocument, frame + 1); } catch (x) {} }
      const heading = /^H[1-3]$/.test(e.tagName);
      if (!(heading || e.matches(interactive)) || !visible(e)) continue;
      const id = frame + '-' + (++n); e.setAttribute('data-gid', id);
      const role = e.getAttribute('role') || (e.tagName === 'INPUT' ? (e.type || 'text') : e.tagName.toLowerCase());
      let line = '[' + id + '] ' + role + ': ' + label(e);
      if (e.tagName === 'SELECT') line += ' {' + [...e.options].map(o => (o.selected ? '*' : '') + o.text).slice(0, 12).join('|') + '}';
      else if ((e.tagName === 'INPUT' || e.tagName === 'TEXTAREA') && e.type !== 'password' && e.value) line += ' = ' + e.value.slice(0, 40);
      if (e.disabled) line += ' (disabled)';
      if (e.type === 'checkbox') line += e.checked ? ' [x]' : ' [ ]';
      out.push(line);
    }
  };
  walk(document, 0);
  return out;
})()
"""


def snapshot(limit=150, contains=None):
    """Interactive elements and headings, one line each, with [IDs] for act(); trimmed at the source."""
    lines = page.evaluate(_SNAP_JS)  # noqa: F821 — page is in the call's scope
    if contains:
        lines = [l for l in lines if contains.lower() in l.lower()]
    shown = lines[:limit]
    print(f"{page.url} — {page.title()} ({len(lines)} elements{', first ' + str(limit) if len(lines) > limit else ''})")  # noqa: F821
    print("\n".join(shown))


def _element(gid):
    frame, _ = gid.split("-", 1)
    if frame == "0":
        return page.locator(f"[data-gid='{gid}']")  # noqa: F821
    return page.frame_locator("iframe").nth(int(frame) - 1).locator(f"[data-gid='{gid}']")  # noqa: F821


def act(*steps):
    """Run steps like ("click", "0-12"), ("fill", "0-3", "text"), ("select", "0-5", "Tech"), ("check", "0-9")."""
    for op, gid, *value in steps:
        el = _element(gid)
        {"click": lambda: el.click(), "fill": lambda: el.fill(value[0]), "check": lambda: el.check(),
         "select": lambda: el.select_option(label=value[0]), "press": lambda: el.press(value[0])}[op]()
    page.wait_for_load_state()  # noqa: F821
