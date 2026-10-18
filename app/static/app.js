'use strict';

(function() {
    // --- State & Configuration ---

    const API_BASE = '';
    const POLL_STATUS_INTERVAL = 2000;
    const POLL_FOLDERS_INTERVAL = 10000;

    let state = {
        mode: 'stage', // 'report' | 'stage'
        paused: false,
        parallel: 1,
        lastPollTime: 0,
        foldersCache: {}, // key: "library/name", value: object from /api/folders
        librariesCache: [], // array from /api/libraries
        connState: 'Live',
        viewMode: 'grid' // 'grid' | 'list'
    };

    // DOM Elements
    const els = {
        mode: document.getElementById('mode'),
        pauseBtn: document.getElementById('pause'),
        parallelInput: document.getElementById('parallel'),
        conn: document.getElementById('conn'),
        notice: document.getElementById('notice'),
        tiles: [
            { label: 'Library size', val: null, sub: null },
            { label: 'Size now', val: null, sub: null },
            { label: 'Saved so far', val: null, sub: null },
            { label: 'Projected when done', val: null, sub: null }
        ],
        spaceFill: document.getElementById('space-fill'),
        spaceText: document.getElementById('space-text'),
        libchart: document.getElementById('libchart'),
        sysline: document.getElementById('sysline'),
        jobList: document.getElementById('job-list'),
        q: document.getElementById('q'),
        filter: document.getElementById('filter'),
        viewGrid: document.getElementById('view-grid'),
        viewList: document.getElementById('view-list'),
        folders: document.getElementById('folders'),
        detailTitle: document.getElementById('detail-title'),
        detailSum: document.getElementById('detail-sum'),
        detailActions: document.getElementById('detail-actions'),
        detailBody: document.getElementById('detail-body'),
        detailClose: document.getElementById('detail-close'),
        detailJson: document.getElementById('detail-json'),
        detail: document.getElementById('detail'),
        isGrid: state.viewMode === 'grid'
    };

    // Guard against missing elements
    const safeEls = {
        pauseBtn: els.pauseBtn || null,
        parallelInput: els.parallelInput || null,
        conn: els.conn || null,
        notice: els.notice || null,
        spaceFill: els.spaceFill || null,
        spaceText: els.spaceText || null,
        libchart: els.libchart || null,
        sysline: els.sysline || null,
        jobList: els.jobList || null,
        q: els.q || null,
        filter: els.filter || null,
        viewGrid: els.viewGrid || null,
        viewList: els.viewList || null,
        folders: els.folders || null,
        detailClose: els.detailClose || null,
        detailJson: els.detailJson || null,
        detail: els.detail || null
    };

    // --- Initialization ---

    function init() {
        loadViewPreference();
        setupEventListeners();
        startPolling();
        fetchFoldersAndLibraries();
    }

    function loadViewPreference() {
        try {
            const saved = localStorage.getItem('kk-reencoder-view');
            if (saved === 'list') {
                state.viewMode = 'list';
                toggleView('list');
            }
        } catch (e) {
            console.warn('Could not load view preference:', e);
        }
    }

    function setupEventListeners() {
        // Pause Toggle
        els.pauseBtn.addEventListener('click', () => {
            togglePause();
        });

        // Parallel Input
        els.parallelInput.addEventListener('change', (e) => {
            const val = parseInt(e.target.value, 10);
            if (val < 1 || val > 5) return;
            state.parallel = val;
            updateParallelUI();
            setParallel(true);
        });

        // View Switcher
        els.viewGrid.textContent = 'Grid';
        els.viewList.textContent = 'List';
        els.viewGrid.addEventListener('click', () => toggleView('grid'));
        els.viewList.addEventListener('click', () => toggleView('list'));

        // Search & Filter
        els.q.addEventListener('input', debounce(renderFolders, 300));
        els.filter.addEventListener('change', renderFolders);

        // Detail Dialog
        els.detailClose.addEventListener('click', () => closeDetail());
        els.detailJson.addEventListener('click', downloadReport);
        
        // Close dialog on backdrop click
        els.detail.addEventListener('click', (e) => {
            if (e.target === els.detail) closeDetail();
        });
        // Close on Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && !els.detail.hidden) closeDetail();
        });
    }

    function startPolling() {
        setInterval(() => {
            pollStatus();
        }, POLL_STATUS_INTERVAL);

        setInterval(() => {
            fetchFoldersAndLibraries();
        }, POLL_FOLDERS_INTERVAL);
    }

    // --- API & Data Fetching ---

    async function fetchJSON(endpoint, options = {}) {
        const url = API_BASE + endpoint;
        try {
            const res = await fetch(url, options);
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            return await res.json();
        } catch (err) {
            console.error('API Error:', err);
            return null;
        }
    }

    async function pollStatus() {
        const data = await fetchJSON('/api/status');
        if (!data) return;

        state.lastPollTime = Date.now();
        state.mode = data.mode;
        state.paused = data.paused;
        state.connState = 'Live';
        updateModeBadge();
        updateConnectionUI();

        updateTiles(data.totals);
        updateSpace(data);
        updateSysLine(data.sys);
        updateJobList(data.jobs);
        updateNotice(data.blocked);

        // Update parallel input display if not focused
        if (document.activeElement !== els.parallelInput) {
            els.parallelInput.value = data.parallel;
        }
    }

    async function fetchFoldersAndLibraries() {
        const [folders, libs] = await Promise.all([
            fetchJSON('/api/folders'),
            fetchJSON('/api/libraries')
        ]);

        if (folders) {
            // Cache by library/name
            state.foldersCache = {};
            folders.forEach(f => {
                const key = `${f.library}/${f.name}`;
                state.foldersCache[key] = f;
            });
        }
        if (libs) {
            state.librariesCache = libs;
            KKChart.render(els.libchart, libs);
        }

        renderFolders();
    }

    // --- UI Updates ---

    function updateConnectionUI() {
        els.conn.textContent = state.connState;
        els.conn.className = 'pill ' + (state.connState === 'Live' ? '' : 'warn');
    }

    function updateTiles(totals) {
        const fmtBytes = (b) => formatBytes(b || 0);
        const fmtPct = (p) => p ? Math.round(p) + '%' : '-';

        // Library size (original_bytes)
        els.tiles[0].val = fmtBytes(totals.original_bytes);
        els.tiles[0].sub = totals.files + ' files';

        // Size now (current_bytes + extra_bytes)
        const nowBytes = totals.current_bytes + totals.extra_bytes;
        els.tiles[1].val = fmtBytes(nowBytes);
        els.tiles[1].sub = totals.extra_bytes > 0 ? `+${formatBytes(totals.extra_bytes)} waiting for confirm` : '';

        // Saved so far (saved_bytes)
        els.tiles[2].val = fmtBytes(totals.saved_bytes);
        const savedPct = totals.original_bytes > 0 ? (totals.saved_bytes / totals.original_bytes * 100) : 0;
        els.tiles[2].sub = fmtPct(savedPct);

        // Projected (projected_bytes)
        els.tiles[3].val = fmtBytes(totals.projected_bytes);
        const projSaved = totals.projected_bytes > 0 && totals.original_bytes > 0 
            ? ((totals.original_bytes - totals.projected_bytes) / totals.original_bytes * 100) 
            : 0;
        els.tiles[3].sub = `saves ${Math.round(projSaved)}%`;
        
        if (state.mode === 'report') {
            els.tiles[3].sub += ' · estimate';
        }
        // write the values into the four .tile elements of #tiles
        document.querySelectorAll('#tiles .tile').forEach((tile, i) => {
            const t = els.tiles[i];
            if (!t) return;
            const v = tile.querySelector('.tile-value');
            const sub = tile.querySelector('.tile-sub');
            if (v) v.textContent = t.val;
            if (sub) sub.textContent = t.sub || '';
        });
    }

    function updateSpace(data) {
        const maxGB = data.max_staged_gb || 1; // Avoid div by zero
        const usedGB = (data.staged_bytes || 0) / (1024 * 1024 * 1024);
        const pct = Math.min(usedGB / maxGB, 1);
        
        els.spaceFill.style.width = (pct * 100) + '%';
        els.spaceText.textContent = `${formatBytes(data.staged_bytes)} of ${formatBytes(maxGB * 1024 * 1024 * 1024)} used by copies waiting for confirm or kept originals`;
    }

    function updateSysLine(sys) {
        if (!sys) {
            els.sysline.textContent = '';
            return;
        }
        const cpuStr = typeof sys.cpu_share === 'number' ? Math.round(sys.cpu_share) + '%' : '—';
        const memStr = sys.mem_bytes !== null && sys.mem_limit !== null 
            ? `${formatBytes(sys.mem_bytes)} of ${formatBytes(sys.mem_limit)}` 
            : '-';
        els.sysline.textContent = `Encoder container: CPU ${cpuStr} of ${Math.round(sys.cpus)} cores · memory ${memStr}`;
    }

    function updateJobList(jobs) {
        els.jobList.innerHTML = '';

        if (jobs.length === 0) {
            const idle = document.createElement('div');
            idle.className = 'idle';
            idle.textContent = 'Idle';
            els.jobList.appendChild(idle);
            return;
        }

        jobs.forEach(job => {
            const card = document.createElement('div');
            card.className = 'jobcard';
            const head = document.createElement('div');
            head.className = 'job-head';
            const folder = document.createElement('strong');
            folder.textContent = job.folder;
            const file = document.createElement('span');
            file.className = 'job-file';
            file.textContent = job.path.split('/').pop();
            const pct = document.createElement('span');
            pct.className = 'job-pct';
            pct.textContent = Math.round(job.percent || 0) + '%';
            head.append(folder, file, pct);

            const bar = document.createElement('div');
            bar.className = 'bar running';
            const fill = document.createElement('div');
            fill.className = 'fill';
            fill.style.width = Math.min(100, job.percent || 0) + '%';
            bar.appendChild(fill);

            const parts = [job.phase];
            if (job.fps > 0) parts.push(Math.round(job.fps) + ' fps');
            if (typeof job.eta_s === 'number' && job.eta_s > 0) parts.push(Math.ceil(job.eta_s / 60) + ' min left');
            if (typeof job.cpu_pct === 'number') parts.push('CPU ' + Math.round(job.cpu_pct) + '%');
            if (job.rss_bytes) parts.push(formatBytes(job.rss_bytes));
            const info = document.createElement('div');
            info.className = 'job-info';
            info.textContent = parts.join(' · ');

            card.append(head, bar, info);
            els.jobList.appendChild(card);
        });
    }

    function updateNotice(blocked) {
        if (blocked) {
            els.notice.textContent = blocked;
            els.notice.hidden = false;
        } else if (state.paused) {
            els.notice.textContent = 'Paused';
            els.notice.hidden = false;
        } else {
            els.notice.hidden = true;
        }
    }

    // --- Folder Rendering Logic ---

    // the line next to the status chip: what is left, or what was saved
    function setStatusText(text, state, folder) {
        text.className = 'status-text';
        const left = folder.pending + (folder.running || 0);
        const toGo = folder.files ? folder.original_bytes * left / folder.files : 0;
        if (state === 'partial') {
            text.textContent = `${left} of ${folder.files} left · ~${formatBytes(toGo)} to go`;
        } else if (state === 'done') {
            text.textContent = `saved ${formatBytes(folder.original_bytes - folder.projected_bytes)}`;
        } else if (state === 'notstarted') {
            text.textContent = `${folder.files} file${folder.files === 1 ? '' : 's'}`;
        } else {
            text.textContent = '';
        }
    }

    function renderFolders() {
        const query = els.q.value.toLowerCase();
        const filterVal = els.filter.value;
        const isGrid = els.isGrid;

        els.folders.innerHTML = '';

        // Get all unique keys from cache
        const keys = Object.keys(state.foldersCache);
        
        // Filter by search
        const filteredKeys = keys.filter(k => {
            const [lib, name] = k.split('/');
            return lib.toLowerCase().includes(query) || name.toLowerCase().includes(query);
        });

        // Filter by state
        const finalKeys = filteredKeys.filter(k => {
            const f = state.foldersCache[k];
            if (filterVal === 'all') return true;
            return getFolderStateClass(f) === filterVal;
        });

        finalKeys.forEach(key => {
            const f = state.foldersCache[key];
            const el = createFolderElement(f, key);
            els.folders.appendChild(el);
        });

        // Update global isGrid flag for event listeners
        els.isGrid = state.viewMode === 'grid';
    }

    function createFolderElement(folder, key) {
        const div = document.createElement('div');
        div.className = 'folder';
        div.dataset.key = key;

        // Header
        const header = document.createElement('div');
        header.className = 'folder-header';

        const nameSpan = document.createElement('span');
        nameSpan.className = 'folder-name';
        nameSpan.textContent = folder.name;
        header.appendChild(nameSpan);

        const chip = document.createElement('span');
        chip.className = 'chip';
        chip.textContent = folder.library;
        header.appendChild(chip);

        // Buttons
        const btnGroup = document.createElement('div');
        btnGroup.className = 'folder-actions';

        const prioBtn = document.createElement('button');
        prioBtn.className = 'btn btn-prio';
        prioBtn.textContent = 'Up next';
        // Enable if there are pending files and it's not the highest possible priority (usually 5).
        if (folder.pending > 0 && folder.priority < 5) {
            prioBtn.disabled = false;
            prioBtn.onclick = () => changePriority(folder.library, folder.name, folder.priority + 1);
        }

        const pauseBtn = document.createElement('button');
        pauseBtn.className = 'btn btn-pause';
        pauseBtn.textContent = folder.paused ? 'Resume' : 'Pause';
        pauseBtn.onclick = () => toggleFolderPause(folder.library, folder.name);

        btnGroup.appendChild(prioBtn);
        btnGroup.appendChild(pauseBtn);
        header.appendChild(btnGroup);

        // Body
        const body = document.createElement('div');
        body.className = els.isGrid ? 'folder-grid' : 'folder-list';

        // Status chip and text
        const statusRow = document.createElement('div');
        statusRow.className = 'status-row';
        const statusChip = document.createElement('span');
        statusChip.className = 'status-chip';
        const statusText = document.createElement('span');
        statusText.className = 'status-text';
        const stateClass = getFolderStateClass(folder);
        setStatusChip(statusChip, stateClass, folder);
        setStatusText(statusText, stateClass, folder);
        statusRow.append(statusChip, statusText);

        // Big saving number and before -> after
        const known = folder.encoded + folder.predicted + folder.staged + folder.confirmed > 0;
        const savedPct = known && folder.original_bytes > 0
            ? Math.round((1 - folder.projected_bytes / folder.original_bytes) * 100) : null;
        const statsRow = document.createElement('div');
        statsRow.className = 'stats-row';
        const saved = document.createElement('span');
        saved.className = 'stat-saved';
        saved.textContent = savedPct === null ? '—' : `−${savedPct}%`;
        const sizes = document.createElement('span');
        sizes.className = 'stat-val';
        sizes.textContent = known
            ? `${formatBytes(folder.original_bytes)} → ${formatBytes(folder.projected_bytes)}`
            : formatBytes(folder.original_bytes);
        statsRow.append(saved, sizes);

        // Files handled
        const handled = folder.encoded + folder.predicted + folder.staged + folder.confirmed + folder.skipped;
        const bar = document.createElement('div');
        bar.className = 'bar';
        const fill = document.createElement('div');
        fill.className = 'fill';
        fill.style.width = (folder.files ? Math.round(handled / folder.files * 100) : 0) + '%';
        bar.appendChild(fill);
        const count = document.createElement('div');
        count.className = 'proj-row';
        count.textContent = `${handled} of ${folder.files} file${folder.files === 1 ? '' : 's'} handled`
            + (folder.extra_bytes > 0 ? ` · now ${formatBytes(folder.current_bytes + folder.extra_bytes)} on disk` : '');

        body.append(statusRow, statsRow, bar, count);

        // Click to open detail
        div.addEventListener('click', (e) => {
            if (e.target.closest('.folder')) {
                KKDetail.open(folder.library, folder.name, refreshFolders);
            }
        });

        div.appendChild(header);
        div.appendChild(body);
        return div;
    }

    function getFolderStateClass(folder) {
        const { pending, running, staged, confirmed, encoded, predicted, skipped, files, current_bytes, original_bytes } = folder || {};
        const handled = encoded + predicted + staged + confirmed;
        const totalHandledOrPending = handled + pending;

        if (pending === 0 && running === 0 && staged === 0 && confirmed === 0 && encoded > 0 && predicted > 0) {
            return 'done';
        }
        if (staged > 0) {
            return 'ready';
        }
        if (handled > 0 && pending > 0) {
            return 'partial';
        }
        if (skipped === files && files > 0) {
            return 'skipped';
        }
        if (totalHandledOrPending === 0) {
            return 'notstarted';
        }
        // Check for empty bytes values which cause the gradient error
        if (current_bytes === null || current_bytes === undefined || original_bytes === null || original_bytes === undefined) {
            return 'partial';
        }
        return 'partial'; // fallback
    }

    function setStatusChip(chip, state, folder) {
        chip.className = 'status-chip ' + state;
        switch (state) {
            case 'done':
                chip.textContent = 'Done';
                break;
            case 'ready':
                const s = folder.staged || 0;
                chip.textContent = `Ready to confirm (${s})`;
                break;
            case 'partial':
                chip.textContent = 'Partly done';
                break;
            case 'notstarted':
                chip.textContent = 'Not started';
                break;
            case 'skipped':
                chip.textContent = 'Skipped';
                break;
        }
    }


    function setStatusChip(chip, state, folder) {
        chip.className = 'status-chip ' + state;
        switch (state) {
            case 'done':
                chip.textContent = 'Done';
                break;
            case 'ready':
                const s = folder.staged || 0;
                chip.textContent = `Ready to confirm (${s})`;
                break;
            case 'partial':
                chip.textContent = 'Partly done';
                break;
            case 'notstarted':
                chip.textContent = 'Not started';
                break;
            case 'skipped':
                chip.textContent = 'Skipped';
                break;
        }
    }



    function calculateProgressPercent(folder) {
        const { pending, running, staged, confirmed, encoded, predicted, skipped, files } = folder;
        const handled = encoded + predicted + staged + confirmed;
        const total = files;
        if (total === 0) return 0;
        // Include pending/running in progress calculation? Usually yes for "work done"
        // But spec says "handled / files". Handled usually means processed.
        // Let's count pending as "in progress" for the bar.
        const active = pending + running;
        const completed = handled + active;
        return Math.round((completed / total) * 100);
    }

    function getRunningDot(folder) {
        if (folder.running > 0) {
            const dot = document.createElement('span');
            dot.className = 'st-running';
            dot.textContent = '●';
            return dot;
        }
        return null;
    }

    // --- Actions ---

    async function togglePause() {
        const current = state.paused;
        await fetchJSON('/pause', { method: 'POST', body: JSON.stringify({ paused: !current }) });
        // Force a quick poll to update UI immediately
        await pollStatus();
    }

    async function setParallel(active) {
        const val = active ? state.parallel : 1;
        await fetchJSON('/parallel', { method: 'POST', body: JSON.stringify({ parallel: val }) });
    }

    async function changePriority(library, folder, newPriority) {
        await fetchJSON('/folder', {
            method: 'POST',
            body: JSON.stringify({ library, folder, priority: newPriority })
        });
        // Refresh to see updated priority/order
        fetchFoldersAndLibraries();
    }

    async function toggleFolderPause(library, folder) {
        await fetchJSON('/folder-action', {
            method: 'POST',
            body: JSON.stringify({ library, folder, action: folder.paused ? 'resume' : 'pause' })
        });
        fetchFoldersAndLibraries();
    }

    function toggleView(mode) {
        state.viewMode = mode;
        els.viewGrid.setAttribute('aria-pressed', mode === 'grid');
        els.viewList.setAttribute('aria-pressed', mode === 'list');
        localStorage.setItem('kk-reencoder-view', mode);
        renderFolders();
    }

    // Update mode badge text
    function updateModeBadge() {
        els.mode.textContent = state.mode === 'report' ? 'Report only' : 'Staging copies';
    }

    // Update library chart labels to use folders count
    function updateLibraryChartLabels() {
        const libs = state.librariesCache || [];
        const labels = libs.map(lib => {
            const folders = lib.folders || 0;
            const label = folders === 1 ? `${folders} series · 1 file` : `${folders} series · ${folders} files`;
            return label;
        });

    }

    // --- Utilities ---

    function formatBytes(bytes) {
        if (bytes === 0) return '0 B';
        if (bytes === null || bytes === undefined) return '0 B';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        if (!bytes || bytes < 1) return "0 B";
        const i = Math.min(4, Math.floor(Math.log(bytes) / Math.log(k)));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i];
    }

    function debounce(func, wait) {
        let timeout;
        return function executedFunction(...args) {
            const later = () => {
                clearTimeout(timeout);
                func(...args);
            };
            clearTimeout(timeout);
            timeout = setTimeout(later, wait);
        };
    }

    // --- Detail Dialog Helpers ---

    function downloadReport() {
        // This would ideally fetch the report endpoint with params
        // For now, just a placeholder or fetch if we had the params
        alert("Download feature requires specific library/folder params passed to detail.");
    }

    function closeDetail() {
        els.detail.hidden = true;
        els.detail.showModal = false;
    }

    // Expose refresh function for detail.js
    window.refreshFolders = fetchFoldersAndLibraries;

    // Start
    init();

})();
