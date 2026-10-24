'use strict';

function shortName(name, folder) {
  let n = name.replace(/\.[a-z0-9]{2,4}$/i, '');
  const series = folder.split('/').pop().toLowerCase();
  if (n.toLowerCase().startsWith(series + ' - ')) n = n.slice(series.length + 3);
  return n;
}

window.KKDetail = {
  open: function(library, folder, onChange) {
    this.library = library;
    this.folder = folder;
    this.onChange = onChange;
    
    document.getElementById('detail-title').textContent = folder.split('/').pop();
    const dlg = document.getElementById('detail');
    // start fresh every time: no comparison, rows or buttons left over from the previous series
    this.clear();
    if (!dlg.open) dlg.showModal();
    dlg.scrollTop = 0;
    if (!this.wired) {
      this.wired = true;
      document.getElementById('detail-close').addEventListener('click', () => dlg.close());
      dlg.addEventListener('close', () => this.clear());  // close button, Escape or a click outside
    }
    this.token = (this.token || 0) + 1;
    this.fetchReport(this.token);
  },

  clear: function() {
    if (window.KKCompare) window.KKCompare.reset(document.getElementById('compare'));
    ['detail-body', 'detail-actions'].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.textContent = '';
    });
  },

  fetchReport: async function(token) {
    try {
      const res = await fetch('/api/report?library=' + encodeURIComponent(this.library) + '&folder=' + encodeURIComponent(this.folder));
      if (!res.ok) throw new Error('Failed to fetch report');
      const data = await res.json();
      if (token !== undefined && token !== this.token) return;  // another series was opened meanwhile
      this.render(data);
    } catch (e) {
      console.error(e);
    }
  },

  formatBytes: function(bytes) {
    if (bytes === null || bytes === undefined) return '';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    if (!bytes || bytes < 1) return "0 B";
    const i = Math.min(4, Math.floor(Math.log(bytes) / Math.log(k)));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
  },

  render: function(data) {
    const sumEl = document.getElementById('detail-sum');
    const readyCount = data.files.filter(f => f.status === 'staged' || f.status === 'confirmed').length;
    const totals = data.totals;
    const originalBytes = totals.original_bytes || 0;
    const projectedBytes = totals.projected_bytes || 0;
    let percentSaved = 0;
    if (originalBytes > 0) {
      percentSaved = ((originalBytes - projectedBytes) / originalBytes) * 100;
    }
    const n = data.totals.files;
    const savedText = percentSaved > 0
      ? ' · ' + this.formatBytes(originalBytes) + ' → ' + this.formatBytes(projectedBytes) + ' (−' + Math.round(percentSaved) + '%)' : '';
    const readyPart = readyCount > 0 ? ' · ' + readyCount + ' ready to confirm' : '';
    sumEl.textContent = data.library + ' · ' + n + (n === 1 ? ' file' : ' files') + savedText + readyPart;

    const actionsEl = document.getElementById('detail-actions');
    actionsEl.innerHTML = '';

    // Staged files
    const stagedFiles = data.files.filter(f => f.status === 'staged');
    if (stagedFiles.length > 0) {
      const btn = document.createElement('button');
      btn.className = 'btn';
      btn.textContent = 'Confirm all ready (' + stagedFiles.length + ')';
      btn.onclick = () => this.bulkAction('confirm', stagedFiles);
      actionsEl.appendChild(btn);
    }

    // Confirmed files
    const confirmedFiles = data.files.filter(f => f.status === 'confirmed');
    if (confirmedFiles.length > 0) {
      const btn = document.createElement('button');
      btn.className = 'btn';
      btn.textContent = 'Undo all (' + confirmedFiles.length + ')';
      btn.onclick = () => this.bulkAction('undo', confirmedFiles);
      actionsEl.appendChild(btn);

      const finalizeBtn = document.createElement('button');
      finalizeBtn.className = 'btn';
      finalizeBtn.textContent = 'Finalize all (' + confirmedFiles.length + ')';
      finalizeBtn.onclick = () => {
        // Finalize deletes the kept originals, so what comes back is their size (the AV1 copies stay)
        const freed = confirmedFiles.reduce((acc, f) => acc + (f.original_bytes || 0), 0);
        const saved = confirmedFiles.reduce((acc, f) => acc + Math.max(0, (f.original_bytes || 0) - (f.new_bytes || 0)), 0);
        if (window.confirm('Delete the ' + confirmedFiles.length + ' kept originals? This frees ' + this.formatBytes(freed) +
            ' on the disk right now; compared with before re-encoding the series uses ' + this.formatBytes(saved) +
            ' less. This cannot be undone.')) {
          this.bulkAction('finalize', confirmedFiles);
        }
      };
      actionsEl.appendChild(finalizeBtn);
    }

    const bodyEl = document.getElementById('detail-body');
    bodyEl.innerHTML = '<table><thead><tr><th class="num">File</th><th>Status</th><th>Before</th><th>After</th><th>Saved</th><th>VMAF</th><th>Quality</th><th>Actions</th></tr></thead><tbody></tbody></table>';
    
    const tbody = bodyEl.querySelector('tbody');
    data.files.forEach(file => {
      const tr = document.createElement('tr');
      
      // File name
      const tdName = document.createElement('td');
      tdName.textContent = shortName(file.name || file.path.split('/').pop(), this.folder);
      tdName.title = file.name || '';
      tr.appendChild(tdName);

      // Status chip
      const statusChip = document.createElement('td');
      const statusLabel = this.getStatusLabel(file.status);
      statusChip.className = 'st-' + file.status;
      statusChip.textContent = statusLabel;
      const why = this.reasonText(file);
      if (why) {
        // visible on phones too (a tooltip is not)
        const small = document.createElement('div');
        small.className = 'st-reason';
        small.textContent = why;
        statusChip.appendChild(small);
      }
      tr.appendChild(statusChip);

      // Before
      const tdBefore = document.createElement('td');
      tdBefore.textContent = this.formatBytes(file.original_bytes);
      tr.appendChild(tdBefore);

      // After
      const tdAfter = document.createElement('td');
      let afterText = this.formatBytes(file.new_bytes);
      if (file.status === 'predicted') {
        afterText = '~' + afterText;
      }
      tdAfter.textContent = afterText;
      tr.appendChild(tdAfter);

      // Saved
      const tdSaved = document.createElement('td');
      tdSaved.textContent = file.saved_pct != null ? file.saved_pct + '%' : '-';
      tr.appendChild(tdSaved);

      // VMAF
      const tdVmaf = document.createElement('td');
      tdVmaf.textContent = file.vmaf != null ? Number(file.vmaf).toFixed(1) : '-';
      tr.appendChild(tdVmaf);

      // Quality
      const tdQuality = document.createElement('td');
      tdQuality.textContent = file.quality || '-';
      tr.appendChild(tdQuality);

      // Actions
      const tdActions = document.createElement('td');
      if (file.status === 'staged') {
        const compareBtn = document.createElement('button');
        compareBtn.className = 'btn btn-small';
        compareBtn.textContent = 'Compare';
        compareBtn.onclick = () => window.KKCompare.show(document.getElementById('compare'), file.path);
        tdActions.appendChild(compareBtn);

        const confirmBtn = document.createElement('button');
        confirmBtn.className = 'btn btn-small';
        confirmBtn.textContent = 'Confirm';
        confirmBtn.onclick = (ev) => this.action(file, 'confirm', ev);
        tdActions.appendChild(confirmBtn);

        const discardBtn = document.createElement('button');
        discardBtn.className = 'btn btn-small';
        discardBtn.textContent = 'Discard';
        discardBtn.onclick = (ev) => this.action(file, 'discard', ev);
        tdActions.appendChild(discardBtn);
      } else if (file.status === 'confirmed') {
        const compareBtn = document.createElement('button');
        compareBtn.className = 'btn btn-small';
        compareBtn.textContent = 'Compare';
        compareBtn.onclick = () => window.KKCompare.show(document.getElementById('compare'), file.path);
        tdActions.appendChild(compareBtn);

        const undoBtn = document.createElement('button');
        undoBtn.className = 'btn btn-small';
        undoBtn.textContent = 'Undo';
        undoBtn.onclick = (ev) => this.action(file, 'undo', ev);
        tdActions.appendChild(undoBtn);

        const finalizeBtn = document.createElement('button');
        finalizeBtn.className = 'btn btn-small';
        finalizeBtn.textContent = 'Finalize';
        finalizeBtn.onclick = (ev) => {
          const saved = Math.max(0, (file.original_bytes || 0) - (file.new_bytes || 0));
          if (window.confirm('Delete the original of ' + (file.name || '') + '? This frees ' + this.formatBytes(file.original_bytes) +
              ' on the disk right now; compared with before re-encoding it uses ' + this.formatBytes(saved) +
              ' less. This cannot be undone.')) {
            this.action(file, 'finalize', ev);
          }
        };
        tdActions.appendChild(finalizeBtn);
      } else if (file.status === 'encoded' && file.codec_after) {
        // done: the comparison frames are kept, so the result can still be looked at
        const compareBtn = document.createElement('button');
        compareBtn.className = 'btn btn-small';
        compareBtn.textContent = 'Compare';
        compareBtn.onclick = () => window.KKCompare.show(document.getElementById('compare'), file.path);
        tdActions.appendChild(compareBtn);
      }
      tr.appendChild(tdActions);
      tbody.appendChild(tr);
    });
  },

  // why a file was skipped or failed, in plain words
  reasonText: function(file) {
    const plain = {
      'already AV1': 'already AV1: nothing to gain',
      'HDR': 'HDR video: left alone because "Skip HDR videos" is on in Settings',
      'Dolby Vision profile 5': 'Dolby Vision profile 5 has no HDR10 picture underneath: re-encoding would get the colours wrong',
      'codec not selected': 'its codec (' + (file.codec_before || '?') + ') is not in the list of codecs to re-encode',
      'resolution too high': 'taller than the maximum height setting',
      'bitrate already low': 'already a small file for its resolution: AV1 would barely save anything',
      'unreadable': 'ffprobe could not read the file (damaged or still copying?)',
      'no smaller encode at target quality': 'no encode reached the target quality below the size limit; changing the target or size limit in Settings tries it again',
      'encode not smaller': 'the whole AV1 file (audio counted too) was above the size limit; changing the size limit in Settings tries it again'
    };
    if (file.status === 'skipped' && file.reason) return plain[file.reason] || file.reason;
    if (file.status === 'failed') {
      const err = (file.error || '').trim();
      if (!err) return 'failed without a message';
      if (err === 'stopped') return 'stopped: the quality search or encode crashed or the service restarted';
      // the first line says what happened (e.g. "ffmpeg killed (-9)..."), the rest is ffmpeg's own detail
      return err.split('\n').filter(Boolean)[0].slice(0, 200);
    }
    return '';
  },

  getStatusLabel: function(status) {
    const map = {
      'pending': 'Pending',
      'running': 'Encoding',
      'predicted': 'Estimate',
      'staged': 'Ready to confirm',
      'confirmed': 'Confirmed',
      'encoded': 'Done',
      'skipped': 'Skipped',
      'failed': 'Failed'
    };
    return map[status] || status;
  },

  action: async function(file, action, ev) {
    const btn = (ev || window.event).target;
    btn.disabled = true;
    btn.textContent = '...';
    
    try {
      const res = await fetch('/api/file', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: file.path, action: action })
      });
      
      if (res.status === 409) {
        const err = await res.text();
        alert(err);
        btn.disabled = false;
        btn.textContent = action.charAt(0).toUpperCase() + action.slice(1);
        return;
      }
      
      if (!res.ok) throw new Error('Failed');
      
      this.fetchReport();
    } catch (e) {
      console.error(e);
      btn.disabled = false;
      btn.textContent = action.charAt(0).toUpperCase() + action.slice(1);
    }
  },

  bulkAction: async function(action, files) {
    // one call does the whole series (the server walks every file in the needed state)
    try {
      const res = await fetch('/api/folder-action', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ library: this.library, folder: this.folder, action: action })
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || (data.failed && data.failed.length)) {
        alert((data.failed && data.failed.length ? data.failed.length + ' of ' + files.length + ' files failed: ' + data.failed[0].error : (data.error || 'Failed')));
      }
    } catch (e) {
      console.error(e);
    }
    this.fetchReport();
    if (this.onChange) this.onChange();
  }
};
