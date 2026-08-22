const message = document.getElementById('message');
const pluginRoot = document.getElementById('plugins');
const builtinRoot = document.getElementById('builtin-plugins');
const builtinFilter = document.getElementById('builtin-filter');
const builtinDetails = document.getElementById('builtin-details');
const builtinCount = document.getElementById('builtin-count');
const jobsRoot = document.getElementById('jobs');
const onlineForm = document.getElementById('online');
const onlinePackage = document.getElementById('online-package');
const discoverVersions = document.getElementById('discover-versions');
const onlineVersion = document.getElementById('online-version');
const importOnline = document.getElementById('import-online');
const assignmentDialog = document.getElementById('assignment-dialog');
const assignmentForm = document.getElementById('assignment-form');
const assignmentTitle = document.getElementById('assignment-title');
const assignmentDescription = document.getElementById('assignment-description');
const assignmentFilter = document.getElementById('assignment-filter');
const assignmentUsers = document.getElementById('assignment-users');
const assignmentFuture = document.getElementById('assignment-future');
const assignmentSubmit = document.getElementById('assignment-submit');
const selectedCount = document.getElementById('selected-count');

const actionLabels = {install:'安装',uninstall:'卸载',enable:'启用',disable:'停用'};
const builtinCollapseKey = 'dsm.plugins.builtins.expanded';
const selectedVersions = new Map();
let users = [];
let builtinPlugins = [];
let discoveredPackage = '';
let pendingAction = null;
let selectedUsers = new Set();

try { builtinDetails.open = localStorage.getItem(builtinCollapseKey) === 'true'; } catch (_) {}
builtinDetails.addEventListener('toggle', () => {
  try { localStorage.setItem(builtinCollapseKey, String(builtinDetails.open)); } catch (_) {}
});

async function api(url, options) {
  const response = await fetch(url, options);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${response.status}`);
  return body;
}

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function sharedUserStatus(plugin, username) {
  const state = (plugin.users || []).find(item => item.username === username);
  if (!state) return {kind:'unassigned', label:'未安装', detail:''};
  if (state.state === 'failed') return {kind:'failed', label:'异常', detail:state.error || state.version};
  if (state.state !== 'ready') return {kind:'pending', label:'处理中', detail:state.version};
  return {kind:state.enabled ? 'enabled' : 'disabled', label:state.enabled ? '已启用' : '已停用', detail:state.version};
}

function builtinUserStatus(plugin, username) {
  const state = (plugin.users || []).find(item => item.username === username);
  return state && state.enabled
    ? {kind:'enabled', label:'已启用', detail:''}
    : {kind:'disabled', label:'已停用', detail:''};
}

function currentUserStatus(username) {
  if (!pendingAction) return {kind:'unassigned', label:'未知', detail:''};
  return pendingAction.kind === 'builtin'
    ? builtinUserStatus(pendingAction.plugin, username)
    : sharedUserStatus(pendingAction.plugin, username);
}

function updateSelectionSummary() {
  selectedCount.textContent = `已选择 ${selectedUsers.size} 个用户`;
  assignmentSubmit.disabled = selectedUsers.size === 0;
}

function renderUserPicker() {
  assignmentUsers.replaceChildren();
  const query = assignmentFilter.value.trim().toLocaleLowerCase();
  const filtered = query ? users.filter(user => user.username.toLocaleLowerCase().includes(query)) : users;
  if (!filtered.length) {
    assignmentUsers.append(el('p', '没有匹配的用户。', 'muted empty-picker'));
    updateSelectionSummary();
    return;
  }
  for (const user of filtered) {
    const status = currentUserStatus(user.username);
    const row = el('label', null, 'user-option');
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.value = user.username;
    checkbox.checked = selectedUsers.has(user.username);
    checkbox.onchange = () => {
      if (checkbox.checked) selectedUsers.add(user.username); else selectedUsers.delete(user.username);
      updateSelectionSummary();
    };
    const identity = el('span', null, 'user-identity');
    identity.append(el('strong', user.username));
    identity.append(el('small', user.disabled ? '用户账号已停用' : '用户账号已启用', user.disabled ? 'account-off' : 'account-on'));
    const pluginState = el('span', status.label, `picker-status status-${status.kind}`);
    if (status.detail) pluginState.title = status.detail;
    row.append(checkbox, identity, pluginState);
    assignmentUsers.append(row);
  }
  updateSelectionSummary();
}

function openAssignmentDialog(kind, plugin, action, versionId) {
  pendingAction = {kind, plugin, action, versionId};
  selectedUsers = new Set();
  assignmentFilter.value = '';
  assignmentFuture.checked = false;
  const pluginName = kind === 'builtin' ? plugin.entryId : plugin.packageName;
  assignmentTitle.textContent = `${actionLabels[action]} ${pluginName}`;
  assignmentDescription.textContent = '查看当前状态并选择一个或多个目标用户。';
  assignmentSubmit.textContent = `批量${actionLabels[action]}`;
  renderUserPicker();
  assignmentDialog.showModal();
}

function selectUsers(predicate) {
  selectedUsers = new Set(users.filter(predicate).map(user => user.username));
  renderUserPicker();
}

async function submitAssignment() {
  if (!pendingAction || !selectedUsers.size) return;
  const {kind, plugin, action, versionId} = pendingAction;
  const endpoint = kind === 'builtin' ? '/api/v1/plugins/builtins/jobs' : '/api/v1/plugins/jobs';
  const payload = kind === 'builtin'
    ? {action, entryId:plugin.entryId, scope:{type:'users', usernames:[...selectedUsers], includeDisabledUsers:true}, applyToFutureUsers:assignmentFuture.checked}
    : {action, pluginId:plugin.id, versionId, scope:{type:'users', usernames:[...selectedUsers], includeDisabledUsers:true}, enabledAfterInstall:true, applyToFutureUsers:assignmentFuture.checked};
  assignmentSubmit.disabled = true;
  try {
    const result = await api(endpoint, {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify(payload)});
    assignmentDialog.close();
    message.textContent = `任务 ${result.job_id} 已进入队列，目标用户 ${result.target_count} 个`;
    pendingAction = null;
    setTimeout(load, 900);
  } catch (error) {
    message.textContent = `操作失败：${error.message}`;
    assignmentSubmit.disabled = false;
  }
}

function actionButtons(definitions) {
  const box = el('div', null, 'actions');
  for (const definition of definitions) {
    const button = el('button', definition.label, definition.danger ? 'danger' : '');
    button.type = 'button';
    button.onclick = definition.onclick;
    box.append(button);
  }
  return box;
}

function renderBuiltinPlugins(plugins) {
  builtinRoot.replaceChildren();
  const query = builtinFilter.value.trim().toLocaleLowerCase();
  const filtered = query ? plugins.filter(plugin => `${plugin.entryId} ${plugin.moduleName}`.toLocaleLowerCase().includes(query)) : plugins;
  if (!plugins.length) { builtinRoot.append(el('p', '未发现官方内置插件。', 'muted')); return; }
  if (!filtered.length) { builtinRoot.append(el('p', '没有匹配的官方插件。', 'muted')); return; }
  const table = el('table');
  const head = el('thead'); head.innerHTML = '<tr><th>插件</th><th class="hide-small">模块</th><th>用户状态</th><th>操作</th></tr>'; table.append(head);
  const body = el('tbody');
  for (const plugin of filtered) {
    const row = el('tr');
    const name = el('td'); name.append(el('strong', plugin.entryId)); name.append(el('span', plugin.defaultEnabled ? '镜像默认启用' : '镜像默认停用', 'builtin-default')); row.append(name);
    row.append(el('td', plugin.moduleName, 'hide-small module-name'));
    row.append(el('td', `${plugin.enabledUsers} 启用 / ${Math.max(users.length - plugin.enabledUsers, 0)} 停用`, 'summary-cell'));
    const controls = el('td'); controls.append(actionButtons([
      {label:'启用', onclick:()=>openAssignmentDialog('builtin', plugin, 'enable')},
      {label:'停用', onclick:()=>openAssignmentDialog('builtin', plugin, 'disable')},
    ])); row.append(controls); body.append(row);
  }
  table.append(body); builtinRoot.append(table);
}

function renderPlugins(plugins) {
  pluginRoot.replaceChildren();
  if (!plugins.length) { pluginRoot.append(el('p', '还没有共享插件，请先联网安装或离线导入。', 'muted')); return; }
  const table = el('table');
  const head = el('thead'); head.innerHTML = '<tr><th>npm 包</th><th>操作版本</th><th class="hide-small">用户状态</th><th>操作</th></tr>'; table.append(head);
  const body = el('tbody');
  for (const plugin of plugins) {
    const row = el('tr'); row.append(el('td', plugin.packageName));
    const versionCell = el('td');
    const select = el('select'); select.setAttribute('aria-label', `${plugin.packageName} 操作版本`);
    for (const version of plugin.versions) {
      const option = el('option', `${version.version} · ${version.source_type === 'offline' ? '离线' : 'registry'}`);
      option.value = version.id; select.append(option);
    }
    const selected = selectedVersions.get(plugin.id);
    if (selected && [...select.options].some(option => option.value === selected)) select.value = selected;
    select.onchange = () => selectedVersions.set(plugin.id, select.value);
    versionCell.append(select); row.append(versionCell);
    row.append(el('td', `${plugin.assignedUsers} 安装 / ${plugin.enabledUsers} 启用`, 'hide-small summary-cell'));
    const controls = el('td'); controls.append(actionButtons([
      {label:'安装', onclick:()=>openAssignmentDialog('shared', plugin, 'install', Number(select.value))},
      {label:'启用', onclick:()=>openAssignmentDialog('shared', plugin, 'enable', Number(select.value))},
      {label:'停用', onclick:()=>openAssignmentDialog('shared', plugin, 'disable', Number(select.value))},
      {label:'卸载', danger:true, onclick:()=>openAssignmentDialog('shared', plugin, 'uninstall', Number(select.value))},
    ])); row.append(controls); body.append(row);
  }
  table.append(body); pluginRoot.append(table);
}

function renderJobs(jobs) {
  jobsRoot.replaceChildren();
  const table = el('table'); const head = el('thead'); head.innerHTML = '<tr><th>时间</th><th>任务</th><th>状态</th><th class="hide-small">错误</th></tr>'; table.append(head); const body = el('tbody');
  const labels = {online_import:'联网导入', offline_import:'离线导入', assign:'用户策略'};
  for (const job of jobs) {
    const row = el('tr'); row.append(el('td', new Date(job.created_at).toLocaleString()));
    const kind = job.kind === 'assign' && job.plugin_id == null && job.package_name ? '内置策略' : (labels[job.kind] || job.kind);
    row.append(el('td', `${kind}${job.action ? ' / ' + job.action : ''}${kind === '内置策略' ? ' / ' + job.package_name : ''}`));
    const state = el('td'); state.append(el('span', job.status, `badge ${job.status === 'completed' ? 'ok' : job.status.includes('failed') ? 'bad' : ''}`)); row.append(state);
    row.append(el('td', job.error || '—', 'hide-small')); body.append(row);
  }
  table.append(body); jobsRoot.append(table);
}

async function load() {
  try {
    const [pluginData, userData, jobData] = await Promise.all([api('/api/v1/plugins'), api('/api/v1/users'), api('/api/v1/plugins/jobs')]);
    users = userData.users;
    builtinPlugins = pluginData.builtinPlugins || [];
    builtinCount.textContent = `（${builtinPlugins.length}）`;
    renderBuiltinPlugins(builtinPlugins);
    renderPlugins(pluginData.plugins);
    renderJobs(jobData.jobs);
    if (assignmentDialog.open) renderUserPicker();
    message.textContent = `${builtinPlugins.length} 个官方插件，${pluginData.plugins.length} 个共享插件，${users.length} 个用户`;
  } catch (error) { message.textContent = `加载失败：${error.message}`; }
}

function resetDiscoveredVersions() {
  discoveredPackage = '';
  onlineVersion.replaceChildren(new Option('请先发现版本', ''));
  onlineVersion.disabled = true;
  importOnline.disabled = true;
}

onlinePackage.oninput = () => { if (onlinePackage.value.trim() !== discoveredPackage) resetDiscoveredVersions(); };
onlineForm.onsubmit = async event => {
  event.preventDefault();
  const packageName = onlinePackage.value.trim();
  if (!packageName) return;
  onlinePackage.disabled = true; discoverVersions.disabled = true; onlineVersion.disabled = true; importOnline.disabled = true;
  message.textContent = `正在查询 ${packageName} 的版本…`;
  try {
    const result = await api(`/api/v1/plugins/registry/versions?${new URLSearchParams({packageName})}`);
    if (!Array.isArray(result.versions) || !result.versions.length) throw new Error('Registry 未返回可用版本');
    discoveredPackage = result.packageName; onlinePackage.value = result.packageName; onlineVersion.replaceChildren();
    for (const version of [...result.versions].reverse()) onlineVersion.append(new Option(version === result.latest ? `${version}（latest）` : version, version, false, version === result.latest));
    onlineVersion.disabled = false; importOnline.disabled = false;
    message.textContent = `已发现 ${result.versions.length} 个版本，当前 latest 为 ${result.latest}`;
  } catch (error) { resetDiscoveredVersions(); message.textContent = `版本发现失败：${error.message}`; }
  finally { onlinePackage.disabled = false; discoverVersions.disabled = false; }
};

importOnline.onclick = async () => {
  const version = onlineVersion.value;
  if (!discoveredPackage || !version) return;
  onlinePackage.disabled = true; discoverVersions.disabled = true; onlineVersion.disabled = true; importOnline.disabled = true;
  try {
    const result = await api('/api/v1/plugins/imports/registry', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({packageName:discoveredPackage, version})});
    message.textContent = `${discoveredPackage}@${version} 导入任务 ${result.job_id} 已创建`; setTimeout(load, 900);
  } catch (error) { message.textContent = `导入失败：${error.message}`; }
  finally { onlinePackage.disabled = false; discoverVersions.disabled = false; onlineVersion.disabled = false; importOnline.disabled = false; }
};

document.getElementById('offline').onsubmit = async event => {
  event.preventDefault();
  const file = new FormData(event.currentTarget).get('file');
  if (!(file instanceof File) || !file.size) return;
  try {
    const result = await api('/api/v1/plugins/imports/archive', {method:'POST', headers:{'content-type':'application/octet-stream'}, body:file});
    message.textContent = `离线导入任务 ${result.job_id} 已创建`; setTimeout(load, 900);
  } catch (error) { message.textContent = `导入失败：${error.message}`; }
};

assignmentFilter.oninput = renderUserPicker;
document.getElementById('select-filtered').onclick = () => {
  const query = assignmentFilter.value.trim().toLocaleLowerCase();
  for (const user of users) if (!query || user.username.toLocaleLowerCase().includes(query)) selectedUsers.add(user.username);
  renderUserPicker();
};
document.getElementById('select-enabled').onclick = () => selectUsers(user => !user.disabled);
document.getElementById('clear-selected').onclick = () => selectUsers(() => false);
document.getElementById('assignment-close').onclick = () => assignmentDialog.close();
document.getElementById('assignment-cancel').onclick = () => assignmentDialog.close();
assignmentForm.onsubmit = event => { event.preventDefault(); submitAssignment(); };
document.getElementById('refresh').onclick = load;
builtinFilter.oninput = () => renderBuiltinPlugins(builtinPlugins);
document.getElementById('logout').onclick = async event => {
  event.currentTarget.disabled = true;
  try {
    const response = await fetch('/api/v1/auth/sessions/current', {method:'DELETE'});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    location.replace('/login');
  } catch (error) { event.currentTarget.disabled = false; message.textContent = `退出失败：${error.message || error}`; }
};

load();
setInterval(load, 10000);
