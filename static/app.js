// ── State ──
let structure = {};
let allCategories = [];
let activeCategory = null;
let activeGroup = null;
let errorSet = new Set();
let totalAnnotations = 0;
let visitedSet = new Set();
let currentBrowsePath = '';
let browseMode = 'dataset'; // 'dataset' or 'sample'

// ── Folder Browser ──
function openBrowseDialog(mode) {
  browseMode = mode || 'dataset';
  document.querySelector('#browseOverlay .dialog-header h2').textContent =
    browseMode === 'sample' ? 'Select Sample Folder' : 'Select Dataset Folder';
  document.getElementById('browseOverlay').classList.remove('hidden');
  browseTo('');
}

function closeBrowseDialog() {
  document.getElementById('browseOverlay').classList.add('hidden');
}

async function browseTo(path) {
  currentBrowsePath = path;
  const url = '/api/browse' + (path ? '?path=' + encodeURIComponent(path) : '');
  const res = await fetch(url);
  const data = await res.json();

  document.getElementById('browsePath').textContent = data.path || 'Drives';
  document.getElementById('btnBrowseUp').style.display = data.path ? '' : 'none';

  const list = document.getElementById('browseList');
  list.innerHTML = '';

  for (const dir of data.dirs) {
    const li = document.createElement('li');
    const fullPath = data.path ? data.path + '/' + dir : dir;
    li.innerHTML = '<span class="folder-icon">&#128193;</span> ' + dir;
    li.onclick = () => browseTo(fullPath);
    list.appendChild(li);
  }

  const status = document.getElementById('browseAnnotationStatus');
  const selectBtn = document.getElementById('btnBrowseSelect');
  if (browseMode === 'sample') {
    status.textContent = data.path ? 'Select this folder for samples' : '';
    selectBtn.disabled = !data.path;
  } else {
    if (data.has_annotation) {
      status.textContent = 'Annotation JSON found';
      selectBtn.disabled = false;
    } else {
      status.textContent = '';
      selectBtn.disabled = true;
    }
  }
}

function browseUp() {
  if (!currentBrowsePath) return;
  const parts = currentBrowsePath.replace(/\\/g, '/').split('/');
  parts.pop();
  let parent = parts.join('/');
  if (parent.match(/^[A-Z]:$/i)) parent += '/';
  if (!parent || parent === currentBrowsePath) {
    browseTo('');
  } else {
    browseTo(parent);
  }
}

function selectBrowseFolder() {
  const path = currentBrowsePath;
  closeBrowseDialog();

  if (browseMode === 'sample') {
    document.getElementById('sampleDirPath').value = path;
  } else {
    document.getElementById('folderPath').value = path;
    document.getElementById('btnLoad').disabled = false;
  }
}

// ── Dataset Loading ──
async function loadDataset() {
  const folder = document.getElementById('folderPath').value;
  if (!folder) return;

  document.getElementById('loadStatus').textContent = 'Loading...';
  document.getElementById('btnLoad').disabled = true;

  try {
    const res = await fetch('/api/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder, sample_dir: document.getElementById('sampleDirPath').value }),
    });
    const data = await res.json();

    if (!data.ok) {
      document.getElementById('loadStatus').textContent = 'Error: ' + (data.error || 'Unknown');
      document.getElementById('btnLoad').disabled = false;
      return;
    }

    structure = data.structure;
    errorSet.clear();
    visitedSet.clear();
    totalAnnotations = 0;
    for (const cat in structure) {
      totalAnnotations += structure[cat].total;
    }

    try {
      const catRes = await fetch('/api/categories');
      allCategories = await catRes.json();
    } catch (e) { allCategories = []; }

    document.getElementById('mainLayout').classList.remove('hidden');
    document.getElementById('loadStatus').textContent =
      totalAnnotations.toLocaleString() + ' annotations loaded';

    renderSidebar();
    const cats = Object.keys(structure);
    if (cats.length > 0) selectCategory(cats[0]);

    updateProgress();
  } catch (e) {
    document.getElementById('loadStatus').textContent = 'Error: ' + e.message;
  }
  document.getElementById('btnLoad').disabled = false;
}

// ── Apply Sample Dir ──
async function applySampleDir() {
  const sampleDir = document.getElementById('sampleDirPath').value;
  try {
    const res = await fetch('/api/sample_dir', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sample_dir: sampleDir }),
    });
    const data = await res.json();
    if (data.ok) {
      if (activeCategory === 'item' && activeGroup && activeGroup !== '(No Barcode)') {
        loadSamples(activeGroup);
      }
    } else {
      alert('Failed to set sample dir: ' + (data.error || 'Unknown'));
    }
  } catch (e) {
    alert('Failed to set sample dir: ' + e.message);
  }
}

// ── Sidebar ──
function renderSidebar() {
  const tree = document.getElementById('categoryTree');
  tree.innerHTML = '';

  for (const cat in structure) {
    const info = structure[cat];
    const li = document.createElement('li');
    li.dataset.category = cat;
    if (visitedSet.has('cat:' + cat)) li.classList.add('visited');
    li.innerHTML = cat + ' <span class="badge">' + info.total + '</span>';
    li.onclick = (e) => {
      e.stopPropagation();
      selectCategory(cat);
    };
    tree.appendChild(li);

    if (cat === 'item' && info.group_by === 'barcode') {
      for (const groupKey in info.groups) {
        const subLi = document.createElement('li');
        subLi.className = 'sub';
        if (visitedSet.has('group:' + groupKey)) subLi.classList.add('visited');
        subLi.dataset.category = cat;
        subLi.dataset.group = groupKey;
        const count = info.groups[groupKey].length;
        subLi.innerHTML = groupKey + ' <span class="badge">' + count + '</span>';
        subLi.onclick = (e) => {
          e.stopPropagation();
          selectCategory(cat, groupKey);
        };
        tree.appendChild(subLi);
      }
    }
  }
}

function selectCategory(cat, group) {
  activeCategory = cat;
  activeGroup = group || null;

  if (group) {
    visitedSet.add('group:' + group);
  } else {
    visitedSet.add('cat:' + cat);
  }

  document.querySelectorAll('#categoryTree li').forEach(li => {
    li.classList.remove('active');
    const liCat = li.dataset.category;
    const liGroup = li.dataset.group;
    if (liGroup && visitedSet.has('group:' + liGroup)) li.classList.add('visited');
    if (!liGroup && liCat && visitedSet.has('cat:' + liCat)) li.classList.add('visited');

    if (liCat === cat) {
      if (!group && !liGroup) li.classList.add('active');
      if (group && liGroup === group) li.classList.add('active');
    }
  });

  renderContent();

  // Show/hide sample panel
  const rightPanel = document.getElementById('contentRight');
  if (cat === 'item' && group && group !== '(No Barcode)') {
    rightPanel.classList.remove('hidden');
    loadSamples(group);
  } else {
    rightPanel.classList.add('hidden');
  }
}

// ── Sample Panel ──
async function loadSamples(barcode) {
  const header = document.getElementById('sampleHeader');
  const grid = document.getElementById('sampleGrid');

  header.textContent = 'Samples: ' + barcode;
  grid.innerHTML = '<div class="sample-empty">Loading...</div>';

  try {
    const res = await fetch('/api/samples/' + encodeURIComponent(barcode));
    const data = await res.json();

    grid.innerHTML = '';

    if (data.count === 0) {
      grid.innerHTML = '<div class="sample-empty">No samples available</div>';
      header.textContent = 'Samples: ' + barcode + ' (0)';
      return;
    }

    header.textContent = 'Samples: ' + barcode + ' (' + data.count + ')';

    for (const file of data.files) {
      const card = document.createElement('div');
      card.className = 'sample-card';

      const img = document.createElement('img');
      img.loading = 'lazy';
      img.src = '/api/samples/' + encodeURIComponent(barcode) + '/' + encodeURIComponent(file);
      img.alt = file;
      card.appendChild(img);

      const name = document.createElement('div');
      name.className = 'sample-name';
      name.textContent = file;
      card.appendChild(name);

      grid.appendChild(card);
    }
  } catch (e) {
    grid.innerHTML = '<div class="sample-empty">Failed to load samples</div>';
  }
}

// ── Content Rendering ──
const PAGE_SIZE = 50;
let renderedGroups = {};

function renderContent() {
  const container = document.getElementById('groupContainer');
  container.innerHTML = '';
  renderedGroups = {};

  if (!activeCategory || !structure[activeCategory]) return;

  const info = structure[activeCategory];
  const header = document.getElementById('contentHeader');
  header.textContent = activeCategory + ' (' + info.total + ' annotations)';

  const groups = info.groups;
  const groupKeys = activeGroup ? [activeGroup] : Object.keys(groups);

  for (const gk of groupKeys) {
    const items = groups[gk];
    if (!items || items.length === 0) continue;

    const filteredItems = items.filter(it => matchesItemFilter(it));
    if (filteredItems.length === 0) continue;

    const section = document.createElement('div');
    section.className = 'group-section';
    section.dataset.groupKey = gk;

    const gh = document.createElement('div');
    gh.className = 'group-header';
    let metaHtml = '';
    if (activeCategory === 'item') {
      const sids = [...new Set(filteredItems.map(i => i.shipment_id).filter(Boolean))];
      metaHtml = '<span class="group-meta">' + sids.length + ' shipments</span>';
    }
    gh.innerHTML = '<h3>' + gk + '</h3>' + metaHtml +
      '<span class="group-badge">' + filteredItems.length + ' items</span>';
    gh.onclick = () => {
      const body = section.querySelector('.group-body');
      body.classList.toggle('collapsed');
    };
    section.appendChild(gh);

    const gb = document.createElement('div');
    gb.className = 'group-body';

    const grid = document.createElement('div');
    grid.className = 'items-grid';
    gb.appendChild(grid);

    renderedGroups[gk] = 0;
    renderItemsPage(grid, filteredItems, gk);

    if (filteredItems.length > PAGE_SIZE) {
      const btn = document.createElement('button');
      btn.className = 'load-more-btn';
      btn.textContent = 'Load More (' + PAGE_SIZE + ')';
      btn.onclick = () => renderItemsPage(grid, filteredItems, gk, btn);
      gb.appendChild(btn);
    }

    section.appendChild(gb);
    container.appendChild(section);
  }
}

function renderItemsPage(grid, items, groupKey, loadMoreBtn) {
  const start = renderedGroups[groupKey] || 0;
  const end = Math.min(start + PAGE_SIZE, items.length);

  for (let i = start; i < end; i++) {
    grid.appendChild(createItemCard(items[i]));
  }

  renderedGroups[groupKey] = end;

  if (loadMoreBtn && end >= items.length) {
    loadMoreBtn.style.display = 'none';
  }
}

function createItemCard(item) {
  const card = document.createElement('div');
  card.className = 'item-card' + (errorSet.has(item.ann_id) ? ' error' : '');
  card.dataset.annId = item.ann_id;

  const img = document.createElement('img');
  img.loading = 'lazy';
  img.src = '/api/crop/' + item.ann_id;
  img.alt = item.category + ' ' + item.ann_id;
  card.appendChild(img);

  const meta = document.createElement('div');
  meta.className = 'item-meta';

  if (item.shipment_id) {
    const sid = document.createElement('div');
    sid.className = 'sid';
    sid.textContent = 'Ship: ' + item.shipment_id;
    meta.appendChild(sid);
  }

  const src = document.createElement('div');
  src.className = 'src';
  src.textContent = item.image_file;
  meta.appendChild(src);

  // Category dropdown
  if (allCategories.length > 0) {
    const catDiv = document.createElement('div');
    catDiv.className = 'category-edit';
    const catLabel = document.createElement('label');
    catLabel.textContent = 'Class:';
    const catSelect = document.createElement('select');
    catSelect.dataset.annId = item.ann_id;
    catSelect.dataset.original = item.category;
    for (const cat of allCategories) {
      const opt = document.createElement('option');
      opt.value = cat.name;
      opt.textContent = cat.name;
      if (cat.name === item.category) opt.selected = true;
      catSelect.appendChild(opt);
    }
    catSelect.addEventListener('change', (e) => handleCategoryEdit(e.target));
    catDiv.appendChild(catLabel);
    catDiv.appendChild(catSelect);
    meta.appendChild(catDiv);
  }

  // Barcode editor (items only)
  if (item.category === 'item') {
    const bcDiv = document.createElement('div');
    bcDiv.className = 'barcode-edit';
    const lbl = document.createElement('label');
    lbl.textContent = 'BC:';
    const inp = document.createElement('input');
    inp.type = 'text';
    inp.value = item.barcode || '';
    inp.dataset.annId = item.ann_id;
    inp.dataset.original = item.barcode || '';
    inp.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.target.blur(); }
    });
    inp.addEventListener('blur', (e) => handleBarcodeEdit(e.target));
    bcDiv.appendChild(lbl);
    bcDiv.appendChild(inp);
    meta.appendChild(bcDiv);
  }

  // Error checkbox
  const chk = document.createElement('div');
  chk.className = 'chk';
  const cb = document.createElement('input');
  cb.type = 'checkbox';
  cb.checked = errorSet.has(item.ann_id);
  cb.dataset.annId = item.ann_id;
  cb.onchange = () => toggleError(item.ann_id, cb.checked, card);
  const cbLabel = document.createElement('label');
  cbLabel.textContent = 'Error';
  cbLabel.onclick = () => { cb.checked = !cb.checked; cb.onchange(); };
  chk.appendChild(cb);
  chk.appendChild(cbLabel);
  meta.appendChild(chk);

  card.appendChild(meta);
  return card;
}

// ── Error Toggle ──
function toggleError(annId, isError, card) {
  if (isError) {
    errorSet.add(annId);
    card.classList.add('error');
  } else {
    errorSet.delete(annId);
    card.classList.remove('error');
  }
  updateProgress();
}

function updateProgress() {
  const count = errorSet.size;
  document.getElementById('errorCount').textContent = count.toLocaleString();
  document.getElementById('totalCount').textContent = totalAnnotations.toLocaleString();

  const pct = totalAnnotations > 0 ? (count / totalAnnotations * 100) : 0;
  document.getElementById('progressFill').style.width = pct + '%';

  document.getElementById('btnExport').disabled = count === 0;
}

// ── Category Edit ──
async function handleCategoryEdit(select) {
  const annId = parseInt(select.dataset.annId);
  const newVal = select.value;
  const oldVal = select.dataset.original;

  if (newVal === oldVal) return;

  try {
    const res = await fetch('/api/category/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ann_id: annId, new_category: newVal }),
    });
    const data = await res.json();

    if (data.ok) {
      select.dataset.original = newVal;
      const structRes = await fetch('/api/structure');
      structure = await structRes.json();
      totalAnnotations = 0;
      for (const cat in structure) {
        totalAnnotations += structure[cat].total;
      }
      renderSidebar();
      renderContent();
      updateProgress();
    } else {
      alert('Category update failed: ' + (data.error || 'Unknown'));
      select.value = oldVal;
    }
  } catch (e) {
    alert('Category update failed: ' + e.message);
    select.value = oldVal;
  }
}

// ── Barcode Edit ──
async function handleBarcodeEdit(input) {
  const annId = parseInt(input.dataset.annId);
  const newVal = input.value.trim();
  const oldVal = input.dataset.original;

  if (newVal === oldVal) return;

  try {
    const res = await fetch('/api/barcode/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ann_id: annId, new_barcode: newVal }),
    });
    const data = await res.json();

    if (data.ok) {
      input.dataset.original = newVal;
      input.classList.add('saved');
      setTimeout(() => input.classList.remove('saved'), 1500);

      const structRes = await fetch('/api/structure');
      structure = await structRes.json();
      renderSidebar();
      renderContent();
    } else {
      alert('Barcode update failed: ' + (data.error || 'Unknown'));
      input.value = oldVal;
    }
  } catch (e) {
    alert('Barcode update failed: ' + e.message);
    input.value = oldVal;
  }
}

// ── Export ──
async function exportXlsx() {
  if (errorSet.size === 0) {
    alert('No error items checked.');
    return;
  }

  const btn = document.getElementById('btnExport');
  btn.textContent = 'Exporting...';
  btn.disabled = true;

  try {
    const res = await fetch('/api/export', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ann_ids: [...errorSet] }),
    });

    if (!res.ok) {
      const err = await res.json();
      alert('Export failed: ' + (err.error || 'Unknown'));
      return;
    }

    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'error_items.xlsx';
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    alert('Export failed: ' + e.message);
  } finally {
    btn.textContent = 'Export XLSX';
    btn.disabled = errorSet.size === 0;
  }
}

// ── Filters ──
function matchesItemFilter(item) {
  const query = document.getElementById('searchInput').value.toLowerCase();
  const status = document.getElementById('statusFilter').value;

  if (query) {
    const match =
      item.image_file.toLowerCase().includes(query) ||
      (item.barcode && item.barcode.toLowerCase().includes(query)) ||
      (item.shipment_id && item.shipment_id.toLowerCase().includes(query)) ||
      (item.category && item.category.toLowerCase().includes(query)) ||
      String(item.ann_id).includes(query);
    if (!match) return false;
  }

  if (status === 'has_error') {
    return errorSet.has(item.ann_id);
  } else if (status === 'no_error') {
    return !errorSet.has(item.ann_id);
  }

  return true;
}

document.getElementById('searchInput').addEventListener('input', () => renderContent());
document.getElementById('statusFilter').addEventListener('change', () => renderContent());

// ── Keyboard Navigation ──
document.addEventListener('keydown', (e) => {
  if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
  const tag = document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'SELECT' || tag === 'TEXTAREA') return;

  e.preventDefault();
  const items = [...document.querySelectorAll('#categoryTree li')];
  if (items.length === 0) return;

  const activeIdx = items.findIndex(li => li.classList.contains('active'));
  let nextIdx;
  if (e.key === 'ArrowDown') {
    nextIdx = activeIdx < items.length - 1 ? activeIdx + 1 : 0;
  } else {
    nextIdx = activeIdx > 0 ? activeIdx - 1 : items.length - 1;
  }

  const target = items[nextIdx];
  selectCategory(target.dataset.category, target.dataset.group);
  target.scrollIntoView({ block: 'nearest' });
});
