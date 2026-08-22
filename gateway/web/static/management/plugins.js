const message = document.getElementById('message');
const pluginRoot = document.getElementById('plugins');
const builtinRoot = document.getElementById('builtin-plugins');
const builtinFilter = document.getElementById('builtin-filter');
const builtinDetails = document.getElementById('builtin-details');
const builtinCount = document.getElementById('builtin-count');
const jobsRoot = document.getElementById('jobs');
const target = document.getElementById('target');
const onlineForm = document.getElementById('online');
const onlinePackage = document.getElementById('online-package');
const discoverVersions = document.getElementById('discover-versions');
const onlineVersion = document.getElementById('online-version');
const importOnline = document.getElementById('import-online');
let users = [];
let builtinPlugins = [];
let discoveredPackage = '';
const builtinCollapseKey = 'dsm.plugins.builtins.expanded';
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
function el(tag, text, className) { const node = document.createElement(tag); if (text != null) node.textContent = text; if (className) node.className = className; return node; }
function scope() { return target.value === 'all' ? {type:'all', usernames:[], includeDisabledUsers:false} : {type:'users', usernames:[target.value], includeDisabledUsers:true}; }
async function action(pluginId, versionId, name) {
  if (!confirm(`确认${{install:'安装',uninstall:'卸载',enable:'启用',disable:'停用'}[name]}这个插件？`)) return;
  try {
    const result = await api('/api/v1/plugins/jobs', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({action:name, pluginId, versionId, scope:scope(), enabledAfterInstall:true, applyToFutureUsers:document.getElementById('future').checked})});
    message.textContent = `任务 ${result.job_id} 已进入队列，目标用户 ${result.target_count} 个`;
    setTimeout(load, 900);
  } catch (error) { message.textContent = `操作失败：${error.message}`; }
}
async function builtinAction(entryId, name) {
  if (!confirm(`确认${name === 'enable' ? '启用' : '停用'}官方插件 ${entryId}？`)) return;
  try {
    const result = await api('/api/v1/plugins/builtins/jobs', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({action:name, entryId, scope:scope(), applyToFutureUsers:document.getElementById('future').checked})});
    message.textContent = `任务 ${result.job_id} 已进入队列，目标用户 ${result.target_count} 个`;
    setTimeout(load, 900);
  } catch (error) { message.textContent = `操作失败：${error.message}`; }
}
function renderBuiltinPlugins(plugins) {
  builtinRoot.replaceChildren();
  const query = builtinFilter.value.trim().toLocaleLowerCase();
  const filtered = query ? plugins.filter(plugin => `${plugin.entryId} ${plugin.moduleName}`.toLocaleLowerCase().includes(query)) : plugins;
  if (!plugins.length) { builtinRoot.append(el('p','未发现官方内置插件。','muted')); return; }
  if (!filtered.length) { builtinRoot.append(el('p','没有匹配的官方插件。','muted')); return; }
  const table = el('table'); const head = el('thead'); head.innerHTML = '<tr><th>插件</th><th class="hide-small">模块</th><th>已启用用户</th><th>操作</th></tr>'; table.append(head);
  const body = el('tbody');
  for (const plugin of filtered) {
    const row = el('tr'); const name = el('td'); name.append(el('strong', plugin.entryId)); name.append(el('span', plugin.defaultEnabled ? ' 默认启用' : ' 默认停用', 'builtin-default')); row.append(name);
    row.append(el('td', plugin.moduleName, 'hide-small module-name')); row.append(el('td', `${plugin.enabledUsers} / ${users.length}`));
    const controls = el('td'); const box = el('div', null, 'actions');
    for (const [actionName,label] of [['enable','启用'],['disable','停用']]) { const button=el('button',label); button.type='button'; button.onclick=()=>builtinAction(plugin.entryId,actionName); box.append(button); }
    controls.append(box); row.append(controls); body.append(row);
  }
  table.append(body); builtinRoot.append(table);
}
function renderPlugins(plugins) {
  pluginRoot.replaceChildren();
  if (!plugins.length) { pluginRoot.append(el('p','还没有共享插件，请先联网安装或离线导入。','muted')); return; }
  const table = el('table'); const head = el('thead'); head.innerHTML = '<tr><th>npm 包</th><th>版本</th><th class="hide-small">已分配 / 已启用</th><th>操作</th></tr>'; table.append(head);
  const body = el('tbody');
  for (const plugin of plugins) {
    const row = el('tr'); row.append(el('td', plugin.packageName));
    const versionCell = el('td'); const select = el('select');
    for (const version of plugin.versions) { const option = el('option', `${version.version} · ${version.source_type === 'offline' ? '离线' : 'registry'}`); option.value = version.id; select.append(option); }
    versionCell.append(select); row.append(versionCell); row.append(el('td', `${plugin.assignedUsers} / ${plugin.enabledUsers}`, 'hide-small'));
    const controls = el('td'); const box = el('div', null, 'actions');
    for (const [name,label] of [['install','安装'],['enable','启用'],['disable','停用'],['uninstall','卸载']]) { const button=el('button',label,name==='uninstall'?'danger':''); button.type='button'; button.onclick=()=>action(plugin.id, Number(select.value), name); box.append(button); }
    controls.append(box); row.append(controls); body.append(row);
  }
  table.append(body); pluginRoot.append(table);
}
function renderJobs(jobs) {
  jobsRoot.replaceChildren(); const table=el('table'); const head=el('thead'); head.innerHTML='<tr><th>时间</th><th>任务</th><th>状态</th><th class="hide-small">错误</th></tr>'; table.append(head); const body=el('tbody');
  const labels={online_import:'联网导入',offline_import:'离线导入',assign:'用户策略'};
  for (const job of jobs) { const row=el('tr'); row.append(el('td',new Date(job.created_at).toLocaleString())); const kind=job.kind==='assign'&&job.plugin_id==null&&job.package_name?'内置策略':(labels[job.kind]||job.kind); row.append(el('td',`${kind}${job.action?' / '+job.action:''}${kind==='内置策略'?' / '+job.package_name:''}`)); const state=el('td'); state.append(el('span',job.status,`badge ${job.status==='completed'?'ok':job.status.includes('failed')?'bad':''}`)); row.append(state); row.append(el('td',job.error||'—','hide-small')); body.append(row); }
  table.append(body); jobsRoot.append(table);
}
async function load() {
  try {
    const [pluginData,userData,jobData]=await Promise.all([api('/api/v1/plugins'),api('/api/v1/users'),api('/api/v1/plugins/jobs')]);
    users=userData.users; const current=target.value; target.replaceChildren(new Option('所有启用用户','all')); for(const user of users) target.append(new Option(`${user.username}${user.disabled?'（已停用）':''}`,user.username)); if([...target.options].some(x=>x.value===current)) target.value=current;
    builtinPlugins=pluginData.builtinPlugins || []; builtinCount.textContent=`（${builtinPlugins.length}）`; renderBuiltinPlugins(builtinPlugins); renderPlugins(pluginData.plugins); renderJobs(jobData.jobs); message.textContent=`${builtinPlugins.length} 个官方插件，${pluginData.plugins.length} 个共享插件，${users.length} 个用户`;
  } catch(error) { message.textContent=`加载失败：${error.message}`; }
}
function resetDiscoveredVersions() {
  discoveredPackage=''; onlineVersion.replaceChildren(new Option('请先发现版本','')); onlineVersion.disabled=true; importOnline.disabled=true;
}
onlinePackage.oninput=()=>{ if(onlinePackage.value.trim()!==discoveredPackage) resetDiscoveredVersions(); };
onlineForm.onsubmit=async event=>{
  event.preventDefault(); const packageName=onlinePackage.value.trim(); if(!packageName)return;
  onlinePackage.disabled=true; discoverVersions.disabled=true; onlineVersion.disabled=true; importOnline.disabled=true; message.textContent=`正在查询 ${packageName} 的版本…`;
  try {
    const params=new URLSearchParams({packageName});
    const result=await api(`/api/v1/plugins/registry/versions?${params}`);
    if(!Array.isArray(result.versions)||!result.versions.length)throw new Error('Registry 未返回可用版本');
    discoveredPackage=result.packageName; onlinePackage.value=result.packageName; onlineVersion.replaceChildren();
    for(const version of [...result.versions].reverse()){ const label=version===result.latest?`${version}（latest）`:version; onlineVersion.append(new Option(label,version,false,version===result.latest)); }
    onlineVersion.disabled=false; importOnline.disabled=false; message.textContent=`已发现 ${result.versions.length} 个版本，当前 latest 为 ${result.latest}`;
  } catch(error) { resetDiscoveredVersions(); message.textContent=`版本发现失败：${error.message}`; }
  finally { onlinePackage.disabled=false; discoverVersions.disabled=false; }
};
importOnline.onclick=async()=>{
  const version=onlineVersion.value; if(!discoveredPackage||!version)return;
  onlinePackage.disabled=true; discoverVersions.disabled=true; onlineVersion.disabled=true; importOnline.disabled=true;
  try {
    const result=await api('/api/v1/plugins/imports/registry',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({packageName:discoveredPackage,version})});
    message.textContent=`${discoveredPackage}@${version} 导入任务 ${result.job_id} 已创建`; setTimeout(load,900);
  } catch(error){ message.textContent=`导入失败：${error.message}`; }
  finally { onlinePackage.disabled=false; discoverVersions.disabled=false; onlineVersion.disabled=false; importOnline.disabled=false; }
};
document.getElementById('offline').onsubmit=async event=>{ event.preventDefault(); const file=new FormData(event.currentTarget).get('file'); if(!(file instanceof File)||!file.size)return; try { const result=await api('/api/v1/plugins/imports/archive',{method:'POST',headers:{'content-type':'application/octet-stream'},body:file}); message.textContent=`离线导入任务 ${result.job_id} 已创建`; setTimeout(load,900); } catch(error){ message.textContent=`导入失败：${error.message}`; } };
document.getElementById('refresh').onclick=load; load(); setInterval(load,10000);
builtinFilter.oninput=()=>renderBuiltinPlugins(builtinPlugins);
document.getElementById('logout').onclick=async event=>{ event.currentTarget.disabled=true; try { const response=await fetch('/api/v1/auth/sessions/current',{method:'DELETE'}); if(!response.ok)throw new Error(`HTTP ${response.status}`); location.replace('/login'); } catch(error) { event.currentTarget.disabled=false; message.textContent=`退出失败：${error.message||error}`; } };
