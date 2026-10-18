// app/static/compare.js
'use strict';

window.KKCompare = {
  show: function (el, path) {
    // Remove hidden attribute and clear existing content
    el.removeAttribute('hidden');
    el.innerHTML = '';

    const cmp = document.createElement('div');
    cmp.className = 'cmp';
    cmp.style.position = 'relative';
    cmp.style.width = '100%';
    cmp.style.overflow = 'hidden';

    // Base layer (re-encoded)
    const imgB = document.createElement('img');
    imgB.className = 'cmp-b';
    imgB.style.position = 'absolute';
    imgB.style.top = '0';
    imgB.style.left = '0';
    imgB.style.width = '100%';
    imgB.style.height = '100%';
    imgB.style.objectFit = 'cover';
    cmp.appendChild(imgB);

    // Top layer (original) - clipped
    const imgA = document.createElement('img');
    imgA.className = 'cmp-a';
    imgA.style.position = 'absolute';
    imgA.style.top = '0';
    imgA.style.left = '0';
    imgA.style.width = '100%';
    imgA.style.height = '100%';
    imgA.style.objectFit = 'cover';
    imgA.style.clipPath = 'inset(0 50% 0 0)'; // Default P=50 -> inset(0 50% 0 0)
    cmp.appendChild(imgA);

    // Handle
    const handle = document.createElement('div');
    handle.className = 'cmp-handle';
    handle.style.position = 'absolute';
    handle.style.top = '0';
    handle.style.bottom = '0';
    handle.style.left = '50%';
    handle.style.zIndex = '10';
    cmp.appendChild(handle);

    // Labels
    const labelLeft = document.createElement('span');
    labelLeft.className = 'cmp-label left';
    labelLeft.textContent = 'Original';
    labelLeft.style.position = 'absolute';
    labelLeft.style.top = '10px';
    labelLeft.style.left = '10px';
    labelLeft.style.color = '#fff';
    labelLeft.style.textShadow = '0 1px 2px rgba(0,0,0,0.8)';
    labelLeft.style.zIndex = '5';
    cmp.appendChild(labelLeft);

    const labelRight = document.createElement('span');
    labelRight.className = 'cmp-label right';
    labelRight.textContent = 'Re-encode';
    labelRight.style.position = 'absolute';
    labelRight.style.top = '10px';
    labelRight.style.right = '10px';
    labelRight.style.color = '#fff';
    labelRight.style.textShadow = '0 1px 2px rgba(0,0,0,0.8)';
    labelRight.style.zIndex = '5';
    cmp.appendChild(labelRight);

    // Range input
    const range = document.createElement('input');
    range.type = 'range';
    range.min = '0';
    range.max = '100';
    range.value = '50';
    range.className = 'cmp-range';
    range.style.position = 'absolute';
    range.style.top = '50%';
    range.style.transform = 'translateY(-50%)';
    range.style.width = '100%';
    range.style.margin = '0';
    range.style.cursor = 'pointer';
    range.setAttribute('aria-label', 'Slide between original and re-encode');
    range.addEventListener('input', function () {
      const p = parseInt(this.value, 10);
      handle.style.left = p + '%';
      imgA.style.clipPath = 'inset(0 ' + (100 - p) + '% 0 0)';
    });
    cmp.appendChild(range);

    el.appendChild(cmp);

    // Fetch data
    fetch('/api/frames?path=' + encodeURIComponent(path))
      .then(function (res) {
        if (!res.ok) throw new Error('Network response was not ok');
        return res.json();
      })
      .then(function (data) {
        if (!data.pairs || data.pairs.length === 0) {
          const p = document.createElement('p');
          p.className = 'muted';
          p.textContent = 'No comparison frames yet';
          el.appendChild(p);
          return;
        }

        // Create thumbs
        const thumbsRow = document.createElement('div');
        thumbsRow.className = 'cmp-thumbs';

        data.pairs.forEach(function (pair) {
          const btn = document.createElement('button');
          btn.style.display = 'inline-block';
          btn.style.margin = '2px';
          btn.style.padding = '2px 6px';
          btn.style.fontSize = '12px';
          btn.style.cursor = 'pointer';
          btn.style.border = '1px solid #ccc';
          btn.style.borderRadius = '4px';
          btn.style.background = '#f9f9f9';
          btn.style.transition = 'background 0.2s';

          const thumbImg = document.createElement('img');
          thumbImg.style.width = '48px';
          thumbImg.style.height = '36px';
          thumbImg.style.objectFit = 'cover';
          thumbImg.style.borderRadius = '2px';
          thumbImg.src = pair.b;

          const timeText = formatTime(pair.t);
          btn.appendChild(thumbImg);
          btn.appendChild(document.createTextNode(' ' + timeText));

          btn.addEventListener('click', function () {
            loadPair(pair, imgA, imgB, range, handle);
            updateActiveThumbs(data.pairs, this);
          });

          thumbsRow.appendChild(btn);
        });

        // Load first pair initially
        loadPair(data.pairs[0], imgA, imgB, range, handle);
        updateActiveThumbs(data.pairs, thumbsRow.firstChild);

        el.appendChild(thumbsRow);
      })
      .catch(function (err) {
        console.error('Failed to load comparison frames:', err);
        const p = document.createElement('p');
        p.className = 'muted';
        p.textContent = 'Failed to load comparison frames.';
        el.appendChild(p);
      });

    // Close button
    const closeBtn = document.createElement('button');
    closeBtn.className = 'cmp-close';
    closeBtn.textContent = 'Close comparison';
    closeBtn.style.position = 'absolute';
    closeBtn.style.top = '10px';
    closeBtn.style.right = '10px';
    closeBtn.style.padding = '6px 12px';
    closeBtn.style.background = '#ff4d4d';
    closeBtn.style.color = '#fff';
    closeBtn.style.border = 'none';
    closeBtn.style.borderRadius = '4px';
    closeBtn.style.cursor = 'pointer';
    closeBtn.style.zIndex = '20';
    closeBtn.addEventListener('click', function () {
      el.setAttribute('hidden', '');
    });
    el.appendChild(closeBtn);
  }
};

function formatTime(seconds) {
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return (m > 0 ? m + ':' : '') + (s < 10 ? '0' : '') + s;
}

function loadPair(pair, imgA, imgB, range, handle) {
  imgA.src = pair.a;
  imgB.src = pair.b;
  range.value = '50';
  handle.style.left = '50%';
  imgA.style.clipPath = 'inset(0 50% 0 0)';
}

function updateActiveThumbs(pairs, activeBtn) {
  const buttons = activeBtn.parentElement.querySelectorAll('button');
  const activeSrc = activeBtn.querySelector('img').src;
  
  buttons.forEach(function (btn, idx) {
    const isSame = pairs[idx] && pairs[idx].a === activeSrc;
    btn.setAttribute('aria-pressed', String(isSame));
  });
}
