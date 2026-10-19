// app/static/compare.js: before/after viewer for one re-encoded file.
// The original sits on top and is cut off at the divider; drag anywhere on the picture (mouse or finger)
// to move the divider. Zoom (1x/2x/4x) magnifies the spot you tapped last, and "Flip" swaps between
// the whole original and the whole re-encode, which is the easiest way to spot a difference.
'use strict';

window.KKCompare = {
  gen: 0,
  // empty and hide the viewer; a frames request still on its way is dropped (see gen)
  reset: function (el) {
    this.gen++;
    if (!el) return;
    el.hidden = true;
    el.textContent = '';
  },
  show: async function (el, path) {
    const gen = ++this.gen;
    el.removeAttribute('hidden');
    el.textContent = '';

    let pairs = [];
    try {
      const r = await fetch('/api/frames?path=' + encodeURIComponent(path), { cache: 'no-store' });
      if (r.ok) pairs = (await r.json()).pairs || [];
    } catch (e) { /* shown as "no frames" below */ }
    if (gen !== this.gen) return;  // closed, or another file was picked meanwhile

    const bar = document.createElement('div');
    bar.className = 'cmp-bar';
    const close = document.createElement('button');
    close.className = 'btn cmp-close';
    close.textContent = 'Close comparison';
    close.addEventListener('click', () => { el.hidden = true; el.textContent = ''; });

    if (!pairs.length) {
      const p = document.createElement('p');
      p.className = 'muted';
      p.textContent = 'No comparison frames yet.';
      bar.appendChild(close);
      el.append(p, bar);
      return;
    }

    const cmp = document.createElement('div');
    cmp.className = 'cmp';
    const stage = document.createElement('div');   // both pictures; zoom scales this layer
    stage.className = 'cmp-stage';
    const imgB = document.createElement('img');
    imgB.className = 'cmp-b';
    imgB.alt = 'Re-encode';
    const imgA = document.createElement('img');
    imgA.className = 'cmp-a';
    imgA.alt = 'Original';
    stage.append(imgB, imgA);

    const handle = document.createElement('div');
    handle.className = 'cmp-handle';
    const knob = document.createElement('div');
    knob.className = 'cmp-knob';
    knob.textContent = '‹ ›';
    handle.appendChild(knob);

    const left = document.createElement('span');
    left.className = 'cmp-label left';
    left.textContent = 'Original';
    const right = document.createElement('span');
    right.className = 'cmp-label right';
    right.textContent = 'AV1 re-encode';

    // keyboard users move the divider with the arrow keys on this slider
    const range = document.createElement('input');
    range.type = 'range';
    range.min = '0';
    range.max = '100';
    range.value = '50';
    range.className = 'cmp-range';
    range.setAttribute('aria-label', 'Divider between original (left) and re-encode (right)');

    cmp.append(stage, handle, left, right, range);

    let pos = 50;
    let zoom = 1;
    let flipped = null;   // null = divider, 'a' = whole original, 'b' = whole re-encode
    function draw() {
      const p = flipped === 'a' ? 100 : flipped === 'b' ? 0 : pos;
      imgA.style.clipPath = `inset(0 ${100 - p}% 0 0)`;
      handle.style.left = p + '%';
      handle.hidden = flipped !== null;
      left.hidden = flipped === 'b';
      right.hidden = flipped === 'a';
      range.value = String(Math.round(pos));
      stage.style.transform = `scale(${zoom})`;
    }
    function moveTo(clientX, clientY) {
      const r = cmp.getBoundingClientRect();
      pos = Math.max(0, Math.min(100, (clientX - r.left) / r.width * 100));
      if (zoom > 1) {
        // magnify around the finger; the divider stays where the finger is
        stage.style.transformOrigin = `${pos}% ${Math.max(0, Math.min(100, (clientY - r.top) / r.height * 100))}%`;
      }
      flipped = null;
      draw();
    }
    let dragging = false;
    cmp.addEventListener('pointerdown', e => { dragging = true; cmp.setPointerCapture(e.pointerId); moveTo(e.clientX, e.clientY); });
    cmp.addEventListener('pointermove', e => { if (dragging) moveTo(e.clientX, e.clientY); });
    cmp.addEventListener('pointerup', () => { dragging = false; });
    cmp.addEventListener('pointercancel', () => { dragging = false; });
    range.addEventListener('input', () => { pos = Number(range.value); flipped = null; draw(); });

    // tools: zoom and flip
    const tools = document.createElement('div');
    tools.className = 'cmp-tools';
    const zoomBtns = [1, 2, 4].map(z => {
      const b = document.createElement('button');
      b.className = 'btn';
      b.textContent = z + '×';
      b.setAttribute('aria-pressed', String(z === 1));
      b.addEventListener('click', () => {
        zoom = z;
        zoomBtns.forEach(x => x.setAttribute('aria-pressed', String(x === b)));
        draw();
      });
      return b;
    });
    const flipA = document.createElement('button');
    flipA.className = 'btn';
    flipA.textContent = 'Show original';
    const flipB = document.createElement('button');
    flipB.className = 'btn';
    flipB.textContent = 'Show re-encode';
    // hold to compare: press shows one version, letting go goes back to the divider
    [[flipA, 'a'], [flipB, 'b']].forEach(([btn, which]) => {
      btn.addEventListener('pointerdown', () => { flipped = which; draw(); });
      ['pointerup', 'pointerleave', 'pointercancel'].forEach(ev => btn.addEventListener(ev, () => { flipped = null; draw(); }));
      btn.addEventListener('keydown', e => { if (e.key === ' ' || e.key === 'Enter') { flipped = which; draw(); } });
      btn.addEventListener('keyup', () => { flipped = null; draw(); });
    });
    const hint = document.createElement('span');
    hint.className = 'cmp-hint';
    hint.textContent = 'Drag on the picture to move the divider. Zoom in, then hold "Show original" / "Show re-encode" to flip.';
    tools.append(...zoomBtns, flipA, flipB);

    // frame picker
    const thumbs = document.createElement('div');
    thumbs.className = 'cmp-thumbs';
    function mmss(t) {
      const s = Math.round(t);
      return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
    }
    const buttons = pairs.map((pair, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      const img = document.createElement('img');
      img.src = pair.b;
      img.alt = '';
      img.loading = 'lazy';
      const t = document.createElement('span');
      t.textContent = mmss(pair.t);
      b.append(img, t);
      b.setAttribute('aria-label', `Frame at ${mmss(pair.t)}`);
      b.addEventListener('click', () => select(i));
      return b;
    });
    thumbs.append(...buttons);
    function select(i) {
      imgA.src = pairs[i].a;
      imgB.src = pairs[i].b;
      buttons.forEach((b, j) => b.setAttribute('aria-pressed', String(i === j)));
      const next = pairs[(i + 1) % pairs.length];  // preload the next pair
      new Image().src = next.a;
      new Image().src = next.b;
    }

    bar.append(hint, close);
    el.append(cmp, tools, thumbs, bar);
    select(0);
    draw();
    cmp.scrollIntoView({ block: 'nearest' });
  },
};
