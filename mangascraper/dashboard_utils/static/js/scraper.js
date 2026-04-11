// scraper.js — extracted from templates/scraper.html
// This file is loaded as an ES module from the scraper template.

let currentResults = [];
let previousResults = [];
let queueIds = new Set();
let extensionOutputFolders = {};
let extensionRecordsByName = {};
let _resultsById = {};
let filteredResultsRows = null;
let activeResultsPage = 1;
let otherResultsPage = 1;
const RESULTS_PAGE_SIZE = 25;
const selectedRows = {
  '#resultsTable': new Set(),
  '#otherResultsTable': new Set(),
  '#queueTable': new Set(),
};

function asText(v, fallback = '-') {
  if (v === null || v === undefined || v === '') return fallback;
  return String(v);
}

function dedupeRows(rows) {
  const seen = new Set();
  const unique = [];
  rows.forEach(row => {
    const gid = Number(row.id ?? row.gallery_id);
    if (!Number.isFinite(gid) || seen.has(gid)) return;
    seen.add(gid);
    unique.push(row);
  });
  return unique;
}

function sortRowsByIdDesc(rows) {
  return [...(rows || [])].sort((a, b) => {
    const aId = Number(a?.id ?? a?.gallery_id);
    const bId = Number(b?.id ?? b?.gallery_id);
    const aVal = Number.isFinite(aId) ? aId : -Infinity;
    const bVal = Number.isFinite(bId) ? bId : -Infinity;
    return bVal - aVal;
  });
}

function snapshotSelection(tableSelector) {
  const tracked = selectedRows[tableSelector] || new Set();
  document.querySelectorAll(`${tableSelector} tbody input[type="checkbox"]`).forEach(cb => {
    const gid = Number(cb.value);
    if (!Number.isFinite(gid)) return;
    if (cb.checked) tracked.add(gid);
    else tracked.delete(gid);
  });
  selectedRows[tableSelector] = tracked;
}

function selectedIds(tableSelector) {
  snapshotSelection(tableSelector);
  return Array.from(selectedRows[tableSelector] || []).filter(Number.isFinite);
}

function setSelectAll(selector, checked) {
  const tracked = selectedRows[selector] || new Set();
  document.querySelectorAll(`${selector} tbody input[type="checkbox"]`).forEach(cb => {
    const gid = Number(cb.value);
    cb.checked = checked;
    if (!Number.isFinite(gid)) return;
    if (checked) tracked.add(gid);
    else tracked.delete(gid);
  });
  selectedRows[selector] = tracked;
}

function renderRows(tableSelector, rows, options = {}) {
  const tbody = document.querySelector(`${tableSelector} tbody`);
  const tracked = selectedRows[tableSelector] || new Set();
  const showDetail = !!options.showDetail;
  tbody.innerHTML = '';
  rows.forEach(row => {
    const idValue = row.id ?? row.gallery_id;
    const artists = Array.isArray(row.artists) ? row.artists : [];
    const groups = Array.isArray(row.groups) ? row.groups : [];
    const langsArr = Array.isArray(row.languages) ? row.languages : [];
    const tr = document.createElement('tr');
    const detailCell = showDetail ? `<td><button class="detailBtn" data-id="${asText(idValue, '')}">Detail</button></td>` : '';
    tr.innerHTML = `
      <td><input type="checkbox" value="${asText(idValue, '')}" ${tracked.has(Number(idValue)) ? 'checked' : ''}></td>
      <td>${asText(idValue)}</td>
      <td>${asText(row.title, '(no title)')}</td>
      ${detailCell}
      <td>${asText([...artists, ...groups].join(', '), '-')}</td>
      <td>${asText(langsArr.join(', '), '-')}</td>
      <td>${asText(row.pages, '0')}</td>
    `;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      const gid = Number(cb.value);
      if (!Number.isFinite(gid)) return;
      if (cb.checked) tracked.add(gid);
      else tracked.delete(gid);
    });
  });

  if (showDetail) {
    tbody.querySelectorAll('.detailBtn').forEach(btn => {
      btn.addEventListener('click', () => {
        const row = _resultsById[Number(btn.dataset.id)];
        if (row) openGalleryDetail(row);
      });
    });
  }

  selectedRows[tableSelector] = tracked;
}

function renderDownloadQueueRows(rows) {
  const tbody = document.querySelector('#downloadQueueTable tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  (rows || []).forEach(row => {
    const tr = document.createElement('tr');
    const ids = Array.isArray(row.ids) ? row.ids.map(v => Number(v)).filter(Number.isFinite) : [];
    tr.innerHTML = `
      <td>${asText(row.download_no, '-')}</td>
      <td>${asText((row.status || '').toUpperCase(), '-')}</td>
      <td>${ids.length ? ids.join(', ') : '-'}</td>
    `;
    tbody.appendChild(tr);
  });
}

function currentSearchRows() {
  return dedupeRows(currentResults).filter(row => !queueIds.has(Number(row.id ?? row.gallery_id)));
}

function activeSearchRows() {
  if (!Array.isArray(filteredResultsRows)) {
    return currentSearchRows();
  }
  return dedupeRows(filteredResultsRows).filter(row => !queueIds.has(Number(row.id ?? row.gallery_id)));
}

function previousSearchRows() {
  const currentIds = new Set(dedupeRows(currentResults).map(row => Number(row.id ?? row.gallery_id)).filter(Number.isFinite));
  return dedupeRows(previousResults).filter(row => {
    const gid = Number(row.id ?? row.gallery_id);
    return Number.isFinite(gid) && !queueIds.has(gid) && !currentIds.has(gid);
  });
}

function renderSearchTables() {
  const activeRows = sortRowsByIdDesc(activeSearchRows());
  const otherRows = sortRowsByIdDesc(previousSearchRows());

  const activeTotalPages = Math.max(1, Math.ceil(activeRows.length / RESULTS_PAGE_SIZE));
  const otherTotalPages = Math.max(1, Math.ceil(otherRows.length / RESULTS_PAGE_SIZE));
  activeResultsPage = Math.min(Math.max(activeResultsPage, 1), activeTotalPages);
  otherResultsPage = Math.min(Math.max(otherResultsPage, 1), otherTotalPages);

  const activeStart = (activeResultsPage - 1) * RESULTS_PAGE_SIZE;
  const otherStart = (otherResultsPage - 1) * RESULTS_PAGE_SIZE;
  const activePageRows = activeRows.slice(activeStart, activeStart + RESULTS_PAGE_SIZE);
  const otherPageRows = otherRows.slice(otherStart, otherStart + RESULTS_PAGE_SIZE);

  _resultsById = {};
  [...activeRows, ...otherRows].forEach(row => {
    _resultsById[Number(row.id ?? row.gallery_id)] = row;
  });

  renderRows('#resultsTable', activePageRows, { showDetail: true });
  renderRows('#otherResultsTable', otherPageRows, { showDetail: true });

  const resultsPager = document.getElementById('resultsPager');
  const resultsPageInfo = document.getElementById('resultsPageInfo');
  const resultsPrevPageBtn = document.getElementById('resultsPrevPageBtn');
  const resultsNextPageBtn = document.getElementById('resultsNextPageBtn');
  if (resultsPager && resultsPageInfo && resultsPrevPageBtn && resultsNextPageBtn) {
    resultsPager.style.display = activeRows.length ? '' : 'none';
    resultsPageInfo.textContent = `Page ${activeResultsPage} of ${activeTotalPages} (${activeRows.length} total)`;
    resultsPrevPageBtn.disabled = activeResultsPage <= 1;
    resultsNextPageBtn.disabled = activeResultsPage >= activeTotalPages;
  }

  const otherResultsPanel = document.getElementById('otherResultsPanel');
  const otherResultsPager = document.getElementById('otherResultsPager');
  const otherResultsPageInfo = document.getElementById('otherResultsPageInfo');
  const otherResultsPrevPageBtn = document.getElementById('otherResultsPrevPageBtn');
  const otherResultsNextPageBtn = document.getElementById('otherResultsNextPageBtn');
  if (otherResultsPanel) {
    otherResultsPanel.style.display = otherRows.length ? '' : 'none';
  }
  if (otherResultsPager && otherResultsPageInfo && otherResultsPrevPageBtn && otherResultsNextPageBtn) {
    otherResultsPager.style.display = otherRows.length ? '' : 'none';
    otherResultsPageInfo.textContent = `Page ${otherResultsPage} of ${otherTotalPages} (${otherRows.length} total)`;
    otherResultsPrevPageBtn.disabled = otherResultsPage <= 1;
    otherResultsNextPageBtn.disabled = otherResultsPage >= otherTotalPages;
  }

  const addAllBtn = document.getElementById('addAllBtn');
  const addSelectedBtn = document.getElementById('addSelectedBtn');
  const hasRenderedResults = (activePageRows.length + otherPageRows.length) > 0;
  if (addAllBtn) addAllBtn.style.display = hasRenderedResults ? '' : 'none';
  if (addSelectedBtn) addSelectedBtn.style.display = hasRenderedResults ? '' : 'none';

  document.getElementById('filterPanel').style.display = activeRows.length || otherRows.length ? '' : 'none';
}

function renderHistoryRows(rows) {
  const tbody = document.querySelector('#historyTable tbody');
  tbody.innerHTML = '';

  rows.forEach(row => {
    const key = asText(row.cache_key, '');
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${asText(row.cache_type, '-')}</td>
      <td>${asText(row.cache_target || '(none)', '-')}</td>
      <td>${asText(row.ids_count, 0)}</td>
      <td>
        <button class="histSearchBtn" data-key="${key}">Search</button>
        <button class="histDeleteBtn" data-key="${key}">Remove</button>
      </td>
    `;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll('.histSearchBtn').forEach(btn => {
    btn.addEventListener('click', () => {
      const key = btn.dataset.key || '';
      if (!key) return;
      document.getElementById('queryType').value = 'cache_key';
      updateSearchFields();
      document.getElementById('queryValue').value = key;
      runSearch();
    });
  });

  tbody.querySelectorAll('.histDeleteBtn').forEach(btn => {
    btn.addEventListener('click', async () => {
      const key = btn.dataset.key || '';
      if (!key) return;
      await removeHistoryItem(key);
    });
  });
}

function renderResultsRows(rows) {
  currentResults = dedupeRows(rows);
  renderSearchTables();
}

function openGalleryDetail(row) {
  const join = arr => Array.isArray(arr) && arr.length ? arr.join(', ') : '-';
  document.getElementById('gdId').textContent = row.id ?? '-';
  document.getElementById('gdTitle').textContent = String(row.clean_title || row.title || `Gallery ${row.id}`);
  document.getElementById('gdArtists').textContent = join(row.artists);
  document.getElementById('gdGroups').textContent = join(row.groups);
  document.getElementById('gdLanguages').textContent = join(row.languages);
  document.getElementById('gdPages').textContent = row.pages ?? '-';
  try {
    document.getElementById('gdStatus').textContent = String(row.status || '-');
  } catch (e) {}
  document.getElementById('gdTags').textContent = join(row.tags);
  document.getElementById('gdCharacters').textContent = join(row.characters);
  document.getElementById('gdParodies').textContent = join(row.parodies);
  document.getElementById('galleryDetailModal').style.display = '';
}

function closeGalleryDetail() {
  document.getElementById('galleryDetailModal').style.display = 'none';
}

function setGlobalStatus(msg) {
  if (window.setGlobalStatusBadge) {
    window.setGlobalStatusBadge(msg, { state: 'busy', durationMs: 12000 });
    return;
  }
  const bar = document.getElementById('globalStatus');
  if (bar) bar.textContent = msg;
}

function clearGlobalStatus() {
  if (window.clearGlobalStatusBadge) {
    window.clearGlobalStatusBadge();
    return;
  }
  const bar = document.getElementById('globalStatus');
  if (bar) bar.textContent = '';
}

const SEARCH_FIELD_RULES = {
  row_queryValue: ['cache_key', 'search', 'artist', 'group', 'tag', 'character', 'parody'],
  row_idsValue:   ['ids'],
  row_sort:       ['homepage', 'search', 'artist', 'group', 'tag', 'character', 'parody'],
  row_startPage:  ['homepage', 'search', 'artist', 'group', 'tag', 'character', 'parody'],
  row_endPage:    ['homepage', 'search', 'artist', 'group', 'tag', 'character', 'parody'],
  row_startId:    ['id_range'],
  row_endId:      ['id_range'],
};

function updateSearchFields() {
  const type = document.getElementById('queryType').value;
  const queryValue = document.getElementById('queryValue');
  if (queryValue) {
    if (type === 'cache_key') {
      queryValue.placeholder = 'e.g. artist:enma';
    } else {
      queryValue.placeholder = 'e.g. english';
    }
  }
  for (const [rowId, visibleFor] of Object.entries(SEARCH_FIELD_RULES)) {
    const el = document.getElementById(rowId);
    if (el) el.style.display = visibleFor.includes(type) ? '' : 'none';
  }
}

document.getElementById('queryType').addEventListener('change', updateSearchFields);
updateSearchFields();

async function loadExtensions() {
  try {
    const res = await fetch('/api/scraper/extensions');
    const data = await res.json();
    const sel = document.getElementById('cfg_extension');
    const manageSel = document.getElementById('extManageSelect');
    extensionOutputFolders = {};
    extensionRecordsByName = {};
    while (sel.options.length > 1) sel.remove(1);
    manageSel.innerHTML = '<option value="">— extension management —</option>';
    (data.extensions || []).forEach(ext => {
      extensionRecordsByName[ext.name] = ext;
      const opt = document.createElement('option');
      opt.value = ext.name;
      const vStr = ext.version ? ` v${ext.version}` : '';
      const installStr = ext.installed ? ' (local - installed)' : ' (local - not installed)';
      opt.textContent = (ext.label || ext.name) + vStr + installStr;
      if (ext.update_available) opt.title = `Update available: v${ext.remote_version}`;
      sel.appendChild(opt);

      const localManageOpt = document.createElement('option');
      localManageOpt.value = ext.name;
      localManageOpt.textContent = `${ext.name} v${ext.version || '0'} (local - ${ext.installed ? 'installed' : 'not installed'})`;
      manageSel.appendChild(localManageOpt);

      if (ext.update_available) {
        const remoteManageOpt = document.createElement('option');
        remoteManageOpt.value = ext.name;
        remoteManageOpt.textContent = `${ext.name} v${ext.remote_version || ext.version || '0'} (remote - update available)`;
        manageSel.appendChild(remoteManageOpt);
      }

      if (ext.name && ext.default_output_folder) {
        extensionOutputFolders[ext.name] = ext.default_output_folder;
      }
    });
  } catch (e) {
    // Non-fatal: extension list stays as "none"
  }
}

function setupOutputFolderDropdown(currentFolder) {
  const sel = document.getElementById('cfg_output_folder_select');
  const custom = document.getElementById('cfg_output_folder_custom');
  const selectedExtension = document.getElementById('cfg_extension').value;
  const extensionDefaultFolder = extensionOutputFolders[selectedExtension] || '';

  const knownPaths = [];
  if (extensionDefaultFolder && !knownPaths.includes(extensionDefaultFolder)) {
    knownPaths.push(extensionDefaultFolder);
  }
  if (currentFolder && !knownPaths.includes(currentFolder)) {
    knownPaths.push(currentFolder);
  }

  while (sel.options.length > 1) sel.remove(1);
  knownPaths.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p;
    opt.textContent = p;
    sel.insertBefore(opt, sel.options[sel.options.length - 1]);
  });
  if (!sel.querySelector('option[value="__custom__"]')) {
    const customOpt = document.createElement('option');
    customOpt.value = '__custom__';
    customOpt.textContent = 'Custom...';
    sel.appendChild(customOpt);
  }

  if (currentFolder) {
    let matched = false;
    for (const opt of sel.options) {
      if (opt.value === currentFolder) { sel.value = currentFolder; matched = true; break; }
    }
    if (!matched) {
      const opt = document.createElement('option');
      opt.value = currentFolder;
      opt.textContent = currentFolder;
      sel.insertBefore(opt, sel.options[sel.options.length - 1]);
      sel.value = currentFolder;
    }
  }
  custom.style.display = 'none';
}

document.getElementById('cfg_output_folder_select').addEventListener('change', function () {
  const custom = document.getElementById('cfg_output_folder_custom');
  if (this.value === '__custom__') {
    custom.style.display = '';
    custom.focus();
  } else {
    custom.style.display = 'none';
    custom.value = '';
  }
});

function getOutputFolder() {
  const sel = document.getElementById('cfg_output_folder_select');
  if (sel.value === '__custom__') {
    return document.getElementById('cfg_output_folder_custom').value.trim();
  }
  return sel.value;
}

document.getElementById('cfg_extension').addEventListener('change', function () {
  const ext = this.value;
  const extDefaultFolder = extensionOutputFolders[ext] || '';
  setupOutputFolderDropdown(extDefaultFolder);
});

function selectedManagedExtensionName() {
  const fromManage = (document.getElementById('extManageSelect').value || '').trim();
  if (fromManage) return fromManage;
  return (document.getElementById('cfg_extension').value || '').trim();
}

async function manageExtension(action) {
  const msg = document.getElementById('extManageMsg');
  const name = selectedManagedExtensionName();
  if (!name) {
    msg.textContent = 'Select an extension first.';
    return;
  }

  const record = extensionRecordsByName[name] || {};
  if (action === 'update' && !record.update_available) {
    msg.textContent = `No update available for '${name}'.`;
    return;
  }

  msg.textContent = `${action[0].toUpperCase() + action.slice(1)} '${name}'...`;
  try {
    const res = await fetch(`/api/scraper/extensions/${action}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name })
    });
    const data = await res.json();
    msg.textContent = data.message || `${action} completed.`;
    await loadExtensions();
    await loadConfig();
  } catch (err) {
    msg.textContent = `${action} failed: ${err.message}`;
  }
}

function formatHudDuration(seconds) {
  const total = Number(seconds || 0);
  if (!Number.isFinite(total) || total <= 0) return '0s';
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = Math.floor(total % 60);
  if (hours > 0) return `${hours}h ${minutes}m ${secs}s`;
  if (minutes > 0) return `${minutes}m ${secs}s`;
  return `${secs}s`;
}

function formatHudBytesPerSecond(bytesPerSecond) {
  const val = Number(bytesPerSecond || 0);
  if (!Number.isFinite(val) || val <= 0) return '0 B/s';
  const units = ['B/s', 'KB/s', 'MB/s', 'GB/s'];
  let size = val;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex += 1;
  }
  return `${size >= 10 || unitIndex === 0 ? size.toFixed(0) : size.toFixed(1)} ${units[unitIndex]}`;
}

async function refreshStatus(force = false) {
  const data = await (window.getScraperStatus ? window.getScraperStatus(force) : (async () => {
    const res = await fetch('/api/scraper/status');
    return await res.json();
  })());
  if (!data) return;
  const counts = data.counts || {};
  const progress = data.progress || {};

  const dynamicStatus = window.__dynamicBarStatusOverride || String(data.status || 'unknown').toUpperCase();
  const runTotal = Number(counts.total ?? progress.total_galleries ?? data.queue_total ?? 0);
  const completedCount = Number(counts.completed ?? progress.completed ?? 0);
  const failedCount = Number(counts.failed ?? progress.failed ?? 0);
  const skippedCount = Number(counts.skipped ?? progress.skipped ?? 0);
  const pagesTotal = Number(progress.total_pages ?? 0);
  const pagesProcessed = Number(progress.pages_processed ?? 0);
  const pagesPerSecond = Number(progress.pages_per_second ?? 0);

  const parts = [
    `Status: ${dynamicStatus}`,
    `PID: ${asText(data.pid, 'N/A')}`,
    `Queued Galleries: ${asText(runTotal, 0)}`,
    `Completed: ${asText(completedCount, 0)}/${asText(runTotal, 0)}`,
    `Failed: ${asText(failedCount, 0)}/${asText(runTotal, 0)}`,
    `Skipped: ${asText(skippedCount, 0)}/${asText(runTotal, 0)}`,
  ];
  if (pagesTotal > 0) {
    parts.push(`Pages: ${asText(pagesProcessed, 0)}/${asText(pagesTotal, 0)} (${pagesPerSecond.toFixed(2)} Pages/s)`);
  }
  parts.push(`Speed: ${formatHudBytesPerSecond(progress.download_speed_bytes ?? 0)}`);
  parts.push(`Uptime: ${formatHudDuration(data.uptime_seconds ?? 0)}`);
  if (pagesTotal > 0) {
    parts.push(`ETA: ${formatHudDuration(progress.eta_seconds ?? 0)}`);
  }
  const pretty = parts.join(' | ');

  const dock = document.getElementById('dynamicBar');
  const dockContent = document.getElementById('dynamicBarContent');
  if (dock && dockContent) {
    dock.style.display = '';
    dockContent.textContent = pretty;
  }
}

async function loadConfig() {
  const res = await fetch('/api/scraper/config');
  const data = await res.json();
  const c = data.config || {};

  await loadExtensions();
  const extSel = document.getElementById('cfg_extension');
  extSel.value = c.extension || '';

  document.getElementById('cfg_mirrors').value = c.mirrors || '';
  document.getElementById('cfg_language').value = c.language || '';
  document.getElementById('cfg_title_type').value = c.title_type || 'pretty';
  document.getElementById('cfg_excluded_tags').value = c.excluded_tags || '';

  setupOutputFolderDropdown(c.output_folder || '');

  document.getElementById('cfg_format').value = c.format || 'directory';
  document.getElementById('cfg_threads_galleries').value = c.threads_galleries ?? '';
  document.getElementById('cfg_threads_images').value = c.threads_images ?? '';
  document.getElementById('cfg_max_retries').value = c.max_retries ?? '';
  document.getElementById('cfg_use_tor').checked = !!c.use_tor;
  document.getElementById('cfg_dry_run').checked = !!c.dry_run;
  document.getElementById('cfg_verify_ssl').checked = !!c.verify_ssl;
  document.getElementById('cfg_use_daemon_threads').checked = !!c.use_daemon_threads;
  document.getElementById('cfg_calm').checked = !!c.calm;
  document.getElementById('cfg_debug').checked = !!c.debug;

  document.getElementById('configMsg').textContent = 'Configuration loaded.';
}

async function saveConfig() {
  const payload = {
    extension: document.getElementById('cfg_extension').value,
    mirrors: document.getElementById('cfg_mirrors').value.trim(),
    language: document.getElementById('cfg_language').value.trim(),
    title_type: document.getElementById('cfg_title_type').value,
    excluded_tags: document.getElementById('cfg_excluded_tags').value.trim(),
    output_folder: getOutputFolder(),
    format: document.getElementById('cfg_format').value,
    threads_galleries: Number(document.getElementById('cfg_threads_galleries').value || 0),
    threads_images: Number(document.getElementById('cfg_threads_images').value || 0),
    max_retries: Number(document.getElementById('cfg_max_retries').value || 0),
    use_tor: document.getElementById('cfg_use_tor').checked,
    dry_run: document.getElementById('cfg_dry_run').checked,
    verify_ssl: document.getElementById('cfg_verify_ssl').checked,
    use_daemon_threads: document.getElementById('cfg_use_daemon_threads').checked,
    calm: document.getElementById('cfg_calm').checked,
    debug: document.getElementById('cfg_debug').checked
  };

  const res = await fetch('/api/scraper/config', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  });
  const data = await res.json();
  document.getElementById('configMsg').textContent = data.message || 'Configuration updated.';
}

async function runSearch() {
  const type = document.getElementById('queryType').value;
  let queryValue = '';
  let explicitIds = '';
  if (type === 'ids') {
    explicitIds = document.getElementById('idsValue').value.trim();
  } else {
    queryValue = document.getElementById('queryValue').value.trim();
  }

  const startPage = Number(document.getElementById('startPage').value || 1);
  const endPageRaw = document.getElementById('endPage').value.trim();
  const endPageNum = (endPageRaw === '' || endPageRaw.toLowerCase() === 'all') ? null : Number(endPageRaw);

  if (endPageNum !== null && endPageNum - startPage >= 20) {
    if (!confirm(`Fetching ${endPageNum - startPage + 1} pages may take a long time and risk rate limiting.\nContinue?`)) return;
  }

  const payload = {
    query_type: type,
    query_value: queryValue,
    ids: explicitIds,
    sort: document.getElementById('sortValue').value,
    start_page: startPage,
    end_page: endPageRaw,
    start_id: Number(document.getElementById('startId').value || 0),
    end_id: Number(document.getElementById('endId').value || 0),
    fetch_all_pages: false,
  };

  document.getElementById('searchMsg').textContent = '';
  previousResults = [];
  selectedRows['#otherResultsTable'].clear();
  filteredResultsRows = null;
  activeResultsPage = 1;
  otherResultsPage = 1;
  renderSearchTables();
  window.setDynamicBarStatusOverride('Searching...');
  await refreshStatus();

  try {
    const res = await fetch('/api/scraper/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    const ids = Array.isArray(data.ids) ? data.ids : [];
    const results = Array.isArray(data.results) ? data.results : [];
    const rowById = new Map(results.map(r => [Number(r.id), r]));
    const incomingResults = ids.map(id => rowById.get(Number(id)) || {
      id,
      title: `Gallery ${id}`,
      artists: [],
      groups: [],
      tags: [],
      characters: [],
      parodies: [],
      languages: [],
      pages: 0,
    });

    previousResults = [];
    currentResults = incomingResults;
    renderSearchTables();

    const s = data.summary || {};
    document.getElementById('summaryMsg').textContent = Object.keys(s).length
      ? `Summary: ${asText(s.total_galleries, 0)} galleries | ${asText(s.unique_artists, 0)} artists | ${asText(s.unique_tags, 0)} tags | pages ${asText(s.min_pages, 0)}-${asText(s.max_pages, 0)} avg ${Math.round(Number(s.avg_pages || 0))}`
      : 'Summary: none';

      document.getElementById('searchMsg').textContent = data.message || `Loaded ${currentSearchRows().length} current results.`;
  } catch (err) {
    document.getElementById('searchMsg').textContent = `Search failed: ${err.message}`;
  } finally {
    window.setDynamicBarStatusOverride('');
    await refreshStatus();
  }
}

async function refreshSearchHistory() {
  const msg = document.getElementById('historyMsg');
  try {
    msg.textContent = 'Loading search history...';
    const res = await fetch('/api/scraper/search/history');
    const data = await res.json();
    const rows = data.history || [];
    renderHistoryRows(rows);
    msg.textContent = `Showing ${rows.length} saved searches.`;
  } catch (err) {
    msg.textContent = `Failed to load search history: ${err.message}`;
  }
}

async function removeHistoryItem(cacheKey) {
  const ok = window.confirm(`Remove search history item '${cacheKey}'? This will delete its cache key from the database.`);
  if (!ok) return;
  await fetch('/api/scraper/search/history/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cache_key: cacheKey })
  });
  await refreshSearchHistory();
}

async function clearEntireCache() {
  const ok = window.confirm('Clear the entire cache? This removes all cache keys and cached metadata.');
  if (!ok) return;
  setGlobalStatus('Clearing cache...');
  try {
    await fetch('/api/scraper/cache/clear', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({})
    });
    await refreshSearchHistory();
  } finally {
    clearGlobalStatus();
  }
}

function _splitFilter(val) {
  return val.split(',').map(s => s.trim().toLowerCase()).filter(Boolean);
}

function applyFilters() {
  const artists = _splitFilter(document.getElementById('flt_artists').value);
  const groups = _splitFilter(document.getElementById('flt_groups').value);
  const tags = _splitFilter(document.getElementById('flt_tags').value);
  const excludeTags = _splitFilter(document.getElementById('flt_excludeTags').value);
  const languages = _splitFilter(document.getElementById('flt_language').value);
  const minPages = parseInt(document.getElementById('flt_minPages').value, 10) || 0;
  const maxPages = parseInt(document.getElementById('flt_maxPages').value, 10) || 0;

  let filtered = currentSearchRows();
  if (artists.length) filtered = filtered.filter(r => (r.artists || []).some(a => artists.includes(a.toLowerCase())));
  if (groups.length) filtered = filtered.filter(r => (r.groups || []).some(g => groups.includes(g.toLowerCase())));
  if (tags.length) filtered = filtered.filter(r => (r.tags || []).some(t => tags.includes(t.toLowerCase())));
  if (excludeTags.length) filtered = filtered.filter(r => !(r.tags || []).some(t => excludeTags.includes(t.toLowerCase())));
  if (languages.length) filtered = filtered.filter(r => (r.languages || []).some(l => languages.includes(l.toLowerCase())));
  if (minPages > 0) filtered = filtered.filter(r => (r.pages || 0) >= minPages);
  if (maxPages > 0) filtered = filtered.filter(r => (r.pages || 0) <= maxPages);

  filteredResultsRows = filtered;
  activeResultsPage = 1;
  renderSearchTables();
  document.getElementById('filterMsg').textContent = `Showing ${filtered.length} of ${currentSearchRows().length} current results.`;
}

function clearFilters() {
  ['flt_artists', 'flt_groups', 'flt_tags', 'flt_excludeTags', 'flt_language', 'flt_minPages', 'flt_maxPages'].forEach(id => {
    document.getElementById(id).value = '';
  });
  filteredResultsRows = null;
  activeResultsPage = 1;
  renderSearchTables();
  document.getElementById('filterMsg').textContent = '';
}

async function refreshQueue() {
  snapshotSelection('#queueTable');
  const res = await fetch('/api/scraper/queue');
  const data = await res.json();
  const rows = sortRowsByIdDesc(data.queue || []);
  const downloads = Array.isArray(data.downloads) ? data.downloads : [];
  const selectedIds = (data.ids || []).map(Number).filter(Number.isFinite);
  const downloadIds = downloads.flatMap(entry => {
    const rawIds = entry?.ids;
    if (Array.isArray(rawIds)) {
      return rawIds.map(Number).filter(Number.isFinite);
    }
    if (typeof rawIds === 'string') {
      const trimmed = rawIds.trim();
      if (!trimmed) return [];
      try {
        const parsed = JSON.parse(trimmed);
        if (Array.isArray(parsed)) {
          return parsed.map(Number).filter(Number.isFinite);
        }
      } catch {
      }
      return trimmed
        .replace(/^[\[]|[\]]$/g, '')
        .split(/[\s,]+/)
        .map(Number)
        .filter(Number.isFinite);
    }
    if (rawIds && typeof rawIds === 'object') {
      return Object.values(rawIds).map(Number).filter(Number.isFinite);
    }
    return [];
  });
  queueIds = new Set([...selectedIds, ...downloadIds]);
  renderDownloadQueueRows(downloads);
  renderRows('#queueTable', rows);
  renderSearchTables();
  document.getElementById('queueMsg').textContent = `${rows.length} Galleries Queued | ${downloads.length} Download Entries.`;
  await refreshStatus();
}

async function addIdsToQueue(ids) {
  const cleanIds = Array.from(new Set((ids || []).map(Number).filter(Number.isFinite)));
  if (!cleanIds.length) return;
  setGlobalStatus('Adding to queue...');
  try {
    await fetch('/api/scraper/queue/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ids: cleanIds })
    });
  } finally {
    clearGlobalStatus();
  }
  cleanIds.forEach(id => {
    selectedRows['#resultsTable'].delete(id);
    selectedRows['#otherResultsTable'].delete(id);
  });
  await refreshQueue();
}

document.getElementById('refreshAllBtn').onclick = async () => {
  await refreshStatus();
  await refreshQueue();
  await loadConfig();
};

document.getElementById('startBtn').onclick = async () => {
  setGlobalStatus('Starting scraper from selected queue...');
  const res = await fetch('/api/scraper/queue/start', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (data.message) {
    document.getElementById('queueMsg').textContent = data.message;
  }
  await refreshQueue();
  await refreshStatus();
  clearGlobalStatus();
};

document.getElementById('stopBtn').onclick = async () => {
  setGlobalStatus('Stopping scraper...');
  const res = await fetch('/api/scraper/stop', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  document.getElementById('searchMsg').textContent = data.message || 'Stop request sent.';
  await refreshStatus();
  clearGlobalStatus();
};

document.getElementById('loadConfigBtn').onclick = loadConfig;
document.getElementById('saveConfigBtn').onclick = saveConfig;
document.getElementById('extInstallBtn').onclick = async () => manageExtension('install');
document.getElementById('extUninstallBtn').onclick = async () => manageExtension('uninstall');
document.getElementById('extUpdateBtn').onclick = async () => manageExtension('update');
document.getElementById('searchBtn').onclick = runSearch;
document.getElementById('refreshHistoryBtn').onclick = refreshSearchHistory;
document.getElementById('clearCacheBtn').onclick = clearEntireCache;
document.getElementById('addAllBtn').onclick = async () => addIdsToQueue(currentSearchRows().map(r => r.id));
document.getElementById('addSelectedBtn').onclick = async () => addIdsToQueue([
  ...selectedIds('#resultsTable'),
  ...selectedIds('#otherResultsTable')
]);
document.getElementById('applyFilterBtn').onclick = applyFilters;
document.getElementById('clearFilterBtn').onclick = clearFilters;
document.getElementById('galleryDetailClose').onclick = closeGalleryDetail;
document.getElementById('refreshQueueBtn').onclick = refreshQueue;
document.getElementById('removeSelectedQueueBtn').onclick = async () => {
  const ids = selectedIds('#queueTable');
  if (!ids.length) {
    document.getElementById('queueMsg').textContent = 'No queue items selected.';
    return;
  }
  if (!window.confirm(`Remove ${ids.length} selected gallery${ids.length === 1 ? '' : 'ies'} from queue?`)) {
    return;
  }
  await fetch('/api/scraper/queue/remove', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids })
  });
  await refreshQueue();
};
document.getElementById('clearQueueBtn').onclick = async () => {
  if (!window.confirm('Clear the entire queue?')) {
    return;
  }
  await fetch('/api/scraper/queue/clear', { method: 'POST' });
  await refreshQueue();
};

document.getElementById('resultsSelectAll').addEventListener('change', (e) => setSelectAll('#resultsTable', e.target.checked));
document.getElementById('otherResultsSelectAll').addEventListener('change', (e) => setSelectAll('#otherResultsTable', e.target.checked));
document.getElementById('queueSelectAll').addEventListener('change', (e) => setSelectAll('#queueTable', e.target.checked));
document.getElementById('resultsPrevPageBtn').addEventListener('click', () => {
  activeResultsPage = Math.max(1, activeResultsPage - 1);
  renderSearchTables();
});
document.getElementById('resultsNextPageBtn').addEventListener('click', () => {
  activeResultsPage += 1;
  renderSearchTables();
});
document.getElementById('otherResultsPrevPageBtn').addEventListener('click', () => {
  otherResultsPage = Math.max(1, otherResultsPage - 1);
  renderSearchTables();
});
document.getElementById('otherResultsNextPageBtn').addEventListener('click', () => {
  otherResultsPage += 1;
  renderSearchTables();
});

document.getElementById('cfg_calm').addEventListener('change', (e) => {
  if (e.target.checked) {
    document.getElementById('cfg_debug').checked = false;
  }
});

document.getElementById('cfg_debug').addEventListener('change', (e) => {
  if (e.target.checked) {
    document.getElementById('cfg_calm').checked = false;
  }
});

loadConfig();
if (window.getScraperStatus) window.getScraperStatus();
refreshQueue();
refreshSearchHistory();
setInterval(refreshQueue, 15000);
setInterval(refreshSearchHistory, 30000);

// Export nothing; module initialises on load. Keep a couple helpers on window for external callers.
window.refreshStatus = refreshStatus;
window.refreshQueue = refreshQueue;
window.runSearch = runSearch;
