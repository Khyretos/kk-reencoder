'use strict';

window.KKDetail = {
  open: function(library, folder, onChange) {
    this.library = library;
    this.folder = folder;
    this.onChange = onChange;
    
    document.getElementById('detail-title').textContent = folder.split('/').pop();
    const dlg = document.getElementById('detail');
    if (!dlg.open) dlg.showModal();
    if (!this.wired) {
      this.wired = true;
      document.getElementById('detail-close').addEventListener('click', () => dlg.close());
    }
    this.fetchReport();
  },

  fetchReport: async function() {
    try {
      const res = await fetch('/api/report?library=' + encodeURIComponent(this.library) + '&folder=' + encodeURIComponent(this.folder));
      if (!res.ok) throw new Error('Failed to fetch report');
      const data = await res.json();
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
        const totalBytes = confirmedFiles.reduce((acc, f) => acc + f.new_bytes, 0);
        if (window.confirm('Delete the ' + confirmedFiles.length + ' kept originals? This frees ' + this.formatBytes(totalBytes) + ' and cannot be undone.')) {
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
      tdName.textContent = file.name || file.path.split('/').pop();
      tr.appendChild(tdName);

      // Status chip
      const statusChip = document.createElement('td');
      const statusLabel = this.getStatusLabel(file.status);
      statusChip.className = 'st-' + file.status;
      statusChip.title = file.reason || '';
      statusChip.textContent = statusLabel;
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
        confirmBtn.onclick = () => this.action(file, 'confirm');
        tdActions.appendChild(confirmBtn);

        const discardBtn = document.createElement('button');
        discardBtn.className = 'btn btn-small';
        discardBtn.textContent = 'Discard';
        discardBtn.onclick = () => this.action(file, 'discard');
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
        undoBtn.onclick = () => this.action(file, 'undo');
        tdActions.appendChild(undoBtn);

        const finalizeBtn = document.createElement('button');
        finalizeBtn.className = 'btn btn-small';
        finalizeBtn.textContent = 'Finalize';
        finalizeBtn.onclick = () => {
          if (window.confirm('Delete original?')) {
            this.action(file, 'finalize');
          }
        };
        tdActions.appendChild(finalizeBtn);
      }
      tr.appendChild(tdActions);
      tbody.appendChild(tr);
    });
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

  action: async function(file, action) {
    const btn = event.target;
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
    if (action === 'finalize') {
      if (!window.confirm('Delete originals?')) return;
    }
    
    for (const file of files) {
      try {
        const res = await fetch('/api/folder-action', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ 
            library: this.library, 
            folder: this.folder, 
            action: action 
          })
        });
        
        if (!res.ok) {
          const data = await res.json();
          if (data.failed && data.failed.includes(file.path)) continue;
          throw new Error('Failed');
        }
      } catch (e) {
        console.error(e);
        break;
      }
    }
    
    this.fetchReport();
  }
};
