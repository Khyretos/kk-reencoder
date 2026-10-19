'use strict';
// Overview page: a big saved-space number, six Apache ECharts charts and the jobs running now.
// Data: GET /api/overview every 15 s, GET /api/status every 2 s. Colours come from the brand
// custom properties in style.css, read again when the colour scheme changes.
(function () {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const charts = {};
    const cpuHistory = [];
    let lastOverview = null;
    let lastStatus = null;
    let shownBig = 0;

    function fmtBytes(b) {
        if (!b || b < 1) return '0 B';
        const u = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.min(4, Math.floor(Math.log(b) / Math.log(1024)));
        const v = b / Math.pow(1024, i);
        return (i >= 4 ? v.toFixed(2) : v >= 100 ? v.toFixed(0) : v.toFixed(1)) + ' ' + u[i];
    }

    function palette() {
        const st = getComputedStyle(document.documentElement);
        const v = (name, fallback) => st.getPropertyValue(name).trim() || fallback;
        const light = window.matchMedia('(prefers-color-scheme: light)').matches;
        return {
            orange: v('--kk-orange', '#f3941f'),
            lilac: v('--kk-lilac', '#cca9ff'),
            violet: light ? '#5c398e' : '#7d55b8',
            neon: v('--kk-neon', '#bf4eff'),
            pink: v('--kk-pink', '#f15bb5'),
            green: light ? '#15803d' : '#4ade80',
            text: light ? '#1c1329' : '#f4eefc',
            muted: light ? '#5e5873' : '#a39cbc',
            grid: light ? 'rgba(92,57,142,.12)' : 'rgba(204,169,255,.10)',
            surface: light ? '#ffffff' : '#2c1f3f',
            track: light ? '#efe7fa' : '#1c1329',
        };
    }
    let C = palette();

    function base() {
        return {
            backgroundColor: 'transparent',
            animationDuration: reduced ? 0 : 900,
            animationEasing: 'cubicOut',
            textStyle: { color: C.text, fontFamily: 'Inter, system-ui, sans-serif' },
            tooltip: { backgroundColor: C.surface, borderColor: C.violet, textStyle: { color: C.text } },
            legend: { bottom: 0, textStyle: { color: C.muted }, icon: 'circle', itemWidth: 10, itemHeight: 10 },
        };
    }
    const byteAxis = () => ({
        type: 'value', splitNumber: 4,
        axisLabel: { color: C.muted, formatter: fmtBytes, hideOverlap: true },
        splitLine: { lineStyle: { color: C.grid } },
    });
    const catAxis = (data) => ({
        type: 'category', data,
        axisLabel: { color: C.muted, width: 110, overflow: 'truncate' },
        axisLine: { lineStyle: { color: C.grid } }, axisTick: { show: false },
    });

    function chart(id) {
        if (!charts[id]) {
            const el = document.querySelector('#' + id + ' .chartbox');
            if (!el) return null;
            charts[id] = echarts.init(el, null, { renderer: 'canvas' });
        }
        return charts[id];
    }

    function setAria(id, text) {
        const el = document.querySelector('#' + id + ' .chartbox');
        if (el) { el.setAttribute('role', 'img'); el.setAttribute('aria-label', text); }
    }

    // ---- hero ----
    function hero(t) {
        const saved = Math.max(0, t.saved_bytes || 0);
        const projected = Math.max(0, t.projected_saved_bytes || 0);
        const big = document.getElementById('saved-big');
        const sub = document.getElementById('saved-sub');
        const target = saved > 0 ? saved : projected;
        const pct = t.original_bytes ? Math.round(target / t.original_bytes * 100) : 0;
        if (saved > 0) {
            sub.textContent = `saved of ${fmtBytes(t.original_bytes)} · ${pct}% smaller`
                + (projected > saved ? ` · ${fmtBytes(projected - saved)} more when everything is done (estimate)` : '');
        } else {
            sub.textContent = `will be saved of ${fmtBytes(t.original_bytes)} (${pct}%) when everything is confirmed · estimate`;
        }
        countUp(big, target);
        const tiles = document.querySelectorAll('#hero-stats .tile');
        const values = [
            ['Files done', (t.encoded || 0) + (t.confirmed || 0)],
            ['Waiting for confirm', t.staged || 0],
            ['Still to do', t.pending || 0],
            ['Skipped', t.skipped || 0],
        ];
        tiles.forEach((tile, i) => {
            if (!values[i]) return;
            tile.querySelector('.tile-label').textContent = values[i][0];
            tile.querySelector('.tile-value').textContent = values[i][1].toLocaleString();
        });
    }

    function countUp(el, target) {
        if (reduced || Math.abs(target - shownBig) < 1) { el.textContent = fmtBytes(target); shownBig = target; return; }
        const from = shownBig, start = performance.now(), dur = 1200;
        function step(now) {
            const k = Math.min(1, (now - start) / dur);
            const v = from + (target - from) * (1 - Math.pow(1 - k, 3));
            el.textContent = fmtBytes(v);
            if (k < 1) requestAnimationFrame(step); else shownBig = target;
        }
        requestAnimationFrame(step);
    }

    // ---- charts ----
    function donut(t) {
        const c = chart('ch-donut'); if (!c) return;
        const saved = Math.max(0, t.saved_bytes || 0);
        const toSave = Math.max(0, (t.projected_saved_bytes || 0) - saved);
        const stays = Math.max(0, t.projected_bytes || 0);
        const extra = Math.max(0, t.extra_bytes || 0);
        const pct = t.original_bytes ? Math.round((t.projected_saved_bytes || 0) / t.original_bytes * 100) : 0;
        c.setOption(Object.assign(base(), {
            color: [C.green, C.orange, C.violet, C.pink],
            tooltip: Object.assign(base().tooltip, { trigger: 'item', valueFormatter: fmtBytes }),
            series: [{
                type: 'pie', radius: ['55%', '78%'], center: ['50%', '45%'], avoidLabelOverlap: true,
                itemStyle: { borderColor: C.track, borderWidth: 3, borderRadius: 6 },
                label: { show: true, position: 'center', formatter: () => `−${pct}%`, fontSize: 30, fontWeight: 800, color: C.text },
                labelLine: { show: false },
                data: [
                    { name: 'Saved', value: saved },
                    { name: 'To be saved', value: toSave },
                    { name: 'Stays (AV1 size)', value: stays },
                    { name: 'Copies waiting', value: extra },
                ],
            }],
        }), true);
        setAria('ch-donut', `Projected saving ${pct} percent; saved ${fmtBytes(saved)}, ${fmtBytes(toSave)} still to be saved.`);
    }

    function history(h) {
        const c = chart('ch-history'); if (!c) return;
        c.setOption(Object.assign(base(), {
            grid: { left: 64, right: 16, top: 20, bottom: 40 },
            tooltip: Object.assign(base().tooltip, { trigger: 'axis', valueFormatter: fmtBytes }),
            legend: { show: false },
            xAxis: { type: 'category', data: h.map(d => d.date), axisLabel: { color: C.muted, hideOverlap: true }, axisLine: { lineStyle: { color: C.grid } } },
            yAxis: byteAxis(),
            series: [{
                name: 'Saved', type: 'line', smooth: true, showSymbol: h.length < 2, symbolSize: 8,
                data: h.map(d => d.saved_bytes), lineStyle: { width: 3, color: C.orange }, itemStyle: { color: C.orange },
                areaStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: 'rgba(243,148,31,.45)' }, { offset: 1, color: 'rgba(243,148,31,0)' }]) },
            }],
        }), true);
        setAria('ch-history', h.length ? `Saved ${fmtBytes(h[h.length - 1].saved_bytes)} by ${h[h.length - 1].date}` : 'Nothing re-encoded yet');
    }

    function libraries(libs) {
        const c = chart('ch-libs'); if (!c) return;
        const names = libs.map(l => l.library);
        c.setOption(Object.assign(base(), {
            color: [C.violet, C.lilac, C.orange],
            grid: { left: 80, right: 16, top: 10, bottom: 50 },
            tooltip: Object.assign(base().tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: fmtBytes }),
            xAxis: byteAxis(), yAxis: catAxis(names),
            series: [
                { name: 'Before', type: 'bar', data: libs.map(l => l.original_bytes), barGap: '15%', itemStyle: { borderRadius: [0, 6, 6, 0] } },
                { name: 'Now', type: 'bar', data: libs.map(l => l.current_bytes + (l.extra_bytes || 0)), itemStyle: { borderRadius: [0, 6, 6, 0] } },
                { name: 'Projected', type: 'bar', data: libs.map(l => l.projected_bytes), itemStyle: { borderRadius: [0, 6, 6, 0] } },
            ],
        }), true);
        setAria('ch-libs', libs.map(l => `${l.library}: ${fmtBytes(l.original_bytes)} to ${fmtBytes(l.projected_bytes)}`).join('; '));
    }

    function top(list) {
        const c = chart('ch-top'); if (!c) return;
        const rows = list.filter(x => x.saved_bytes > 0).slice(0, 10).reverse();
        c.setOption(Object.assign(base(), {
            legend: { show: false },
            grid: { left: 120, right: 60, top: 10, bottom: 30 },
            tooltip: Object.assign(base().tooltip, { trigger: 'axis', axisPointer: { type: 'shadow' }, valueFormatter: fmtBytes }),
            xAxis: byteAxis(), yAxis: catAxis(rows.map(r => r.name)),
            series: [{
                name: 'Saved', type: 'bar', data: rows.map(r => r.saved_bytes), barMaxWidth: 26,
                itemStyle: { borderRadius: [0, 8, 8, 0], color: new echarts.graphic.LinearGradient(0, 0, 1, 0, [{ offset: 0, color: C.neon }, { offset: 1, color: C.orange }]) },
                label: { show: true, position: 'right', color: C.text, formatter: p => '−' + Math.round(rows[p.dataIndex].saved_pct) + '%' },
            }],
        }), true);
        setAria('ch-top', rows.slice().reverse().map(r => `${r.name}: ${fmtBytes(r.saved_bytes)}`).join('; ') || 'No savings yet');
    }

    function codecs(cd) {
        const c = chart('ch-codecs'); if (!c) return;
        const names = [...new Set([...Object.keys(cd.before || {}), ...Object.keys(cd.after || {})])].sort();
        const others = [C.violet, C.lilac, C.pink, C.neon, C.green];
        const colorOf = {};
        names.filter(n => n !== 'av1').forEach((n, i) => { colorOf[n] = others[i % others.length]; });
        colorOf.av1 = C.orange;
        const colors = names.map(n => colorOf[n]);
        const ring = (obj) => Object.keys(obj).sort().map(k => ({ name: k, value: obj[k], itemStyle: { color: colorOf[k] } }));
        c.setOption(Object.assign(base(), {
            color: colors,
            tooltip: Object.assign(base().tooltip, { trigger: 'item', valueFormatter: fmtBytes, formatter: p => `${p.seriesName}<br>${p.name}: ${fmtBytes(p.value)} (${p.percent}%)` }),
            legend: Object.assign(base().legend, { data: names }),
            series: [
                { name: 'Before', type: 'pie', radius: ['22%', '45%'], center: ['50%', '45%'], label: { show: false }, itemStyle: { borderColor: C.track, borderWidth: 2 }, data: ring(cd.before || {}) },
                { name: 'After', type: 'pie', radius: ['52%', '75%'], center: ['50%', '45%'], label: { show: false }, itemStyle: { borderColor: C.track, borderWidth: 2 }, data: ring(cd.after || {}) },
            ],
        }), true);
        setAria('ch-codecs', 'Inner ring codecs before, outer ring after');
    }

    function live(sys) {
        const c = chart('ch-live'); if (!c || !sys) return;
        const cpu = typeof sys.cpu_share === 'number' ? Math.round(sys.cpu_share) : 0;
        const mem = sys.mem_limit ? Math.round(sys.mem_bytes / sys.mem_limit * 100) : 0;
        cpuHistory.push(cpu); if (cpuHistory.length > 60) cpuHistory.shift();
        const gauge = (name, value, center, color) => ({
            name, type: 'gauge', center, radius: '44%', min: 0, max: 100, startAngle: 210, endAngle: -30,
            progress: { show: true, width: 12, roundCap: true, itemStyle: { color } },
            axisLine: { lineStyle: { width: 12, color: [[1, C.track]] } },
            axisTick: { show: false }, splitLine: { show: false }, axisLabel: { show: false }, pointer: { show: false },
            title: { offsetCenter: [0, '72%'], color: C.muted, fontSize: 12 },
            detail: { offsetCenter: [0, '5%'], formatter: '{value}%', color: C.text, fontSize: 24, fontWeight: 700 },
            data: [{ value, name }],
        });
        c.setOption(Object.assign(base(), {
            legend: { show: false },
            grid: { left: 30, right: 10, top: '66%', bottom: 8 },
            xAxis: { type: 'category', show: false, data: cpuHistory.map((_, i) => i) },
            yAxis: { type: 'value', min: 0, max: 100, show: false },
            series: [
                gauge('CPU', cpu, ['26%', '34%'], C.orange),
                gauge('Memory', mem, ['74%', '34%'], C.lilac),
                { type: 'line', data: cpuHistory.slice(), smooth: true, showSymbol: false, lineStyle: { color: C.orange, width: 2 },
                  areaStyle: { color: 'rgba(243,148,31,.18)' } },
            ],
        }), false);
        setAria('ch-live', `CPU ${cpu} percent of the allowed cores, memory ${mem} percent of the limit`);
    }

    function jobs(list) {
        const box = document.getElementById('now-list');
        if (!box) return;
        box.textContent = '';
        if (!list || !list.length) {
            const idle = document.createElement('div');
            idle.className = 'idle';
            idle.textContent = 'Idle';
            box.appendChild(idle);
            return;
        }
        list.forEach(j => {
            const card = document.createElement('div');
            card.className = 'jobcard';
            const head = document.createElement('div');
            head.className = 'job-head';
            const f = document.createElement('strong'); f.textContent = j.folder;
            const n = document.createElement('span'); n.className = 'job-file'; n.textContent = j.path.split('/').pop();
            const p = document.createElement('span'); p.className = 'job-pct'; p.textContent = Math.round(j.percent || 0) + '%';
            head.append(f, n, p);
            const bar = document.createElement('div'); bar.className = 'bar running';
            const fill = document.createElement('div'); fill.className = 'fill'; fill.style.width = Math.min(100, j.percent || 0) + '%';
            bar.appendChild(fill);
            const parts = [j.phase];
            if (j.fps > 0) parts.push(Math.round(j.fps) + ' fps');
            if (typeof j.eta_s === 'number' && j.eta_s > 0) parts.push(Math.ceil(j.eta_s / 60) + ' min left');
            if (typeof j.cpu_pct === 'number') parts.push((j.cpu_pct / 100).toFixed(1) + ' CPU cores');
            if (j.rss_bytes) parts.push(fmtBytes(j.rss_bytes));
            const info = document.createElement('div'); info.className = 'job-info'; info.textContent = parts.join(' · ');
            card.append(head, bar, info);
            box.appendChild(card);
        });
    }

    function drawOverview(o) {
        const steps = [() => hero(o.totals), () => donut(o.totals), () => history(o.history || []),
            () => libraries(o.libraries || []), () => top(o.top || []), () => codecs(o.codecs || {})];
        steps.forEach(fn => { try { fn(); } catch (e) { console.warn('chart update failed', e); } });
    }

    async function pollOverview() {
        try {
            const r = await fetch('/api/overview', { cache: 'no-store' });
            if (r.ok) { lastOverview = await r.json(); drawOverview(lastOverview); }
        } catch (e) { console.warn('overview fetch failed', e); }
    }

    async function pollStatus() {
        try {
            const r = await fetch('/api/status', { cache: 'no-store' });
            if (r.ok) {
                lastStatus = await r.json();
                try { live(lastStatus.sys); } catch (e) { console.warn('live chart failed', e); }
                jobs(lastStatus.jobs);
            }
        } catch (e) { console.warn('status fetch failed', e); }
    }

    function init() {
        // the first live point needs two samples; the second poll fills it
        pollOverview();
        pollStatus();
        setInterval(pollOverview, 15000);
        setInterval(pollStatus, 2000);
        window.addEventListener('resize', () => Object.values(charts).forEach(c => c.resize()));
        window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
            C = palette();
            if (lastOverview) drawOverview(lastOverview);
            if (lastStatus) live(lastStatus.sys);
        });
    }

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
    else init();
})();
