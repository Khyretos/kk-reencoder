// app/static/chart.js
'use strict';

window.KKChart = {
  render: function (el, libraries) {
    // Sort libraries by original_bytes descending to ensure consistent max width
    const sortedLibraries = [...libraries].sort((a, b) => b.original_bytes - a.original_bytes);
    
    if (sortedLibraries.length === 0) {
      el.innerHTML = '';
      return;
    }

    const maxOriginalBytes = sortedLibraries[0].original_bytes;
    const rowsContainer = document.createElement('div');
    rowsContainer.className = 'kk-chart-rows';

    // Helper to format bytes with 1024 steps (GB, TB, one decimal)
    const formatBytes = (bytes) => {
      if (bytes >= 1e12) return (bytes / 1e12).toFixed(1) + ' TB';
      if (bytes >= 1e9) return (bytes / 1e9).toFixed(1) + ' GB';
      if (bytes >= 1e6) return (bytes / 1e6).toFixed(0) + ' MB';
      return Math.round(bytes / 1e3) + ' KB';
    };

    // Calculate percentage change for the label
    const totalOriginal = sortedLibraries.reduce((sum, lib) => sum + lib.original_bytes, 0);
    const totalProjected = sortedLibraries.reduce((sum, lib) => sum + lib.projected_bytes, 0);
    let percentChange = 0;
    if (totalOriginal > 0) {
      percentChange = ((totalProjected - totalOriginal) / totalOriginal) * 100;
    }
    const sign = percentChange >= 0 ? '+' : '';
    const labelSuffix = `${sign}${percentChange.toFixed(1)}%`;

    // Render rows
    sortedLibraries.forEach(lib => {
      const row = document.createElement('div');
      row.className = 'kk-chart-row';
      
      // Label column
      const label = document.createElement('span');
      label.className = 'kk-chart-label';
      const fileCount = lib.files || 0;
      const seriesCount = lib.folders || 0;
      label.textContent = '';
      const name = document.createElement('strong');
      name.textContent = lib.library;
      const meta = document.createElement('small');
      meta.textContent = `${seriesCount} ${seriesCount === 1 ? 'title' : 'titles'} · ${fileCount} ${fileCount === 1 ? 'file' : 'files'}`;
      label.append(name, document.createElement('br'), meta);
      row.appendChild(label);

      // Track container
      const track = document.createElement('div');
      track.className = 'kk-chart-track';
      
      // Widths calculation
      const origWidthPct = (lib.original_bytes / maxOriginalBytes) * 100;
      const nowWidthPct = (lib.current_bytes / maxOriginalBytes) * 100;
      const projWidthPct = (lib.projected_bytes / maxOriginalBytes) * 100;

      // Bars
      const barOrig = document.createElement('div');
      barOrig.className = 'kk-chart-bar kk-chart-bar-orig';
      barOrig.style.width = `${origWidthPct}%`;
      barOrig.title = `Original: ${formatBytes(lib.original_bytes)}`;

      const barNow = document.createElement('div');
      barNow.className = 'kk-chart-bar kk-chart-bar-now';
      barNow.style.width = `${nowWidthPct}%`;
      barNow.title = `Current: ${formatBytes(lib.current_bytes)}`;

      const barProj = document.createElement('div');
      barProj.className = 'kk-chart-bar kk-chart-bar-proj';
      barProj.style.width = `${projWidthPct}%`;
      barProj.title = `Projected: ${formatBytes(lib.projected_bytes)}`;

      track.appendChild(barOrig);
      track.appendChild(barNow);
      track.appendChild(barProj);
      row.appendChild(track);

      // Right side summary text
      const summary = document.createElement('span');
      summary.className = 'kk-chart-summary';
      const libPct = lib.original_bytes > 0 ? Math.round((1 - lib.projected_bytes / lib.original_bytes) * 100) : 0;
      summary.textContent = `${formatBytes(lib.original_bytes)} → ${formatBytes(lib.projected_bytes)} (−${libPct}%)`;
      row.appendChild(summary);

      rowsContainer.appendChild(row);
    });

    // Legend
    const legend = document.createElement('div');
    legend.className = 'kk-chart-legend';
    
    const createLegendItem = (text, className) => {
      const item = document.createElement('div');
      item.className = `kk-chart-legend-item ${className}`;
      const dot = document.createElement('span');
      dot.className = `kk-chart-dot ${className}`;
      dot.style.width = '8px';
      dot.style.height = '8px';
      dot.style.display = 'inline-block';
      dot.style.borderRadius = '50%';
      item.appendChild(dot);
      item.appendChild(document.createTextNode(text));
      return item;
    };

    legend.appendChild(createLegendItem('Original', 'kk-chart-legend-orig'));
    legend.appendChild(createLegendItem('Now', 'kk-chart-legend-now'));
    legend.appendChild(createLegendItem('Projected', 'kk-chart-legend-proj'));
    
    rowsContainer.appendChild(legend);
    el.replaceChildren(rowsContainer);  // redraw in place: a poll must not add another set of rows
  }
};
