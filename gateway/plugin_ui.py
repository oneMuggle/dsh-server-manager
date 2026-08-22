from __future__ import annotations


def plugin_admin_page() -> str:
    return r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>插件管理 · DeepSeek Harness</title>
  <style>
    :root { color-scheme: dark; font-family: system-ui, sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; background: #10131a; color: #f4f6fb; }
    main { width: min(1180px, calc(100vw - 36px)); margin: 36px auto; }
    header, .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
    header { justify-content: space-between; margin-bottom: 22px; }
    h1, h2 { margin: 0; } h2 { font-size: 18px; margin-bottom: 14px; }
    nav { display: flex; gap: 9px; }
    a, button, input, select { border: 1px solid #465066; border-radius: 8px; padding: 9px 12px; background: #181d27; color: inherit; font: inherit; }
    a { text-decoration: none; } button { cursor: pointer; } button.primary { background: #315efb; border-color: #315efb; }
    button:disabled, select:disabled { cursor: not-allowed; opacity: .55; }
    button.danger { color: #ffbaba; border-color: #733b3b; }
    section { margin: 16px 0; padding: 18px; border: 1px solid #303746; border-radius: 14px; background: #181d27; }
    form { display: flex; gap: 9px; flex-wrap: wrap; } form input[type=text] { min-width: 230px; }
    .grow { flex: 1; } .muted { color: #aeb9d2; font-size: 13px; }
    #message { min-height: 22px; color: #b9c8f6; }
    table { width: 100%; border-collapse: collapse; margin-top: 10px; }
    th, td { text-align: left; padding: 11px 9px; border-top: 1px solid #303746; vertical-align: middle; }
    th { color: #aeb9d2; font-size: 12px; }
    .actions { display: flex; gap: 6px; flex-wrap: wrap; }
    .badge { padding: 3px 7px; border-radius: 999px; background: #27416e; font-size: 12px; }
    .ok { background: #174c37; } .bad { background: #5a2929; }
    @media (max-width: 760px) { .hide-small { display:none; } main { width: calc(100vw - 20px); } }
  </style>
</head>
<body><main>
  <header><div><h1>共享插件管理</h1><div id="message">正在加载…</div></div>
    <nav><a href="/admin">用户管理</a><a href="/">返回 DSH</a><button id="refresh" class="primary">刷新</button><button id="logout">退出登录</button></nav>
  </header>
  <section><h2>导入插件</h2><div class="row">
    <form id="online" class="grow"><input id="online-package" name="packageName" required placeholder="npm 包名，例如 @scope/plugin"><button id="discover-versions" type="submit">发现版本</button><select id="online-version" aria-label="插件版本" disabled><option>请先发现版本</option></select><button id="import-online" class="primary" type="button" disabled>导入所选版本</button></form>
    <form id="offline"><input name="file" type="file" accept=".tgz,application/gzip" required><button>离线导入</button></form>
  </div><p class="muted">联网导入会先从 Registry 读取全部版本，选择后才下载该精确版本。插件实体只保存一份；安装脚本不会执行。</p></section>
  <section><h2>插件与用户策略</h2><div class="row">
    <label>目标 <select id="target"><option value="all">所有启用用户</option></select></label>
    <label><input id="future" type="checkbox"> 同步应用到后续新用户</label>
  </div><div id="plugins"></div></section>
  <section><h2>最近任务</h2><div id="jobs"></div></section>
</main>
<script>
const message = document.getElementById('message');
const pluginRoot = document.getElementById('plugins');
const jobsRoot = document.getElementById('jobs');
const target = document.getElementById('target');
const onlineForm = document.getElementById('online');
const onlinePackage = document.getElementById('online-package');
const discoverVersions = document.getElementById('discover-versions');
const onlineVersion = document.getElementById('online-version');
const importOnline = document.getElementById('import-online');
let users = [];
let discoveredPackage = '';
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
    const result = await api('/api/gateway/v1/admin/plugin-jobs', {method:'POST', headers:{'content-type':'application/json'}, body:JSON.stringify({action:name, pluginId, versionId, scope:scope(), enabledAfterInstall:true, applyToFutureUsers:document.getElementById('future').checked})});
    message.textContent = `任务 ${result.job_id} 已进入队列，目标用户 ${result.target_count} 个`;
    setTimeout(load, 900);
  } catch (error) { message.textContent = `操作失败：${error.message}`; }
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
  for (const job of jobs) { const row=el('tr'); row.append(el('td',new Date(job.created_at).toLocaleString())); row.append(el('td',`${labels[job.kind]||job.kind}${job.action?' / '+job.action:''}`)); const state=el('td'); state.append(el('span',job.status,`badge ${job.status==='completed'?'ok':job.status.includes('failed')?'bad':''}`)); row.append(state); row.append(el('td',job.error||'—','hide-small')); body.append(row); }
  table.append(body); jobsRoot.append(table);
}
async function load() {
  try {
    const [pluginData,userData,jobData]=await Promise.all([api('/api/gateway/v1/admin/plugins'),api('/api/gateway/v1/admin/users'),api('/api/gateway/v1/admin/plugin-jobs')]);
    users=userData.users; const current=target.value; target.replaceChildren(new Option('所有启用用户','all')); for(const user of users) target.append(new Option(`${user.username}${user.disabled?'（已停用）':''}`,user.username)); if([...target.options].some(x=>x.value===current)) target.value=current;
    renderPlugins(pluginData.plugins); renderJobs(jobData.jobs); message.textContent=`${pluginData.plugins.length} 个共享插件，${users.length} 个用户`;
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
    const result=await api(`/api/gateway/v1/admin/plugins/registry-versions?${params}`);
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
    const result=await api('/api/gateway/v1/admin/plugins/online',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({packageName:discoveredPackage,version})});
    message.textContent=`${discoveredPackage}@${version} 导入任务 ${result.job_id} 已创建`; setTimeout(load,900);
  } catch(error){ message.textContent=`导入失败：${error.message}`; }
  finally { onlinePackage.disabled=false; discoverVersions.disabled=false; onlineVersion.disabled=false; importOnline.disabled=false; }
};
document.getElementById('offline').onsubmit=async event=>{ event.preventDefault(); const file=new FormData(event.currentTarget).get('file'); if(!(file instanceof File)||!file.size)return; try { const result=await api('/api/gateway/v1/admin/plugins/offline',{method:'POST',headers:{'content-type':'application/octet-stream'},body:file}); message.textContent=`离线导入任务 ${result.job_id} 已创建`; setTimeout(load,900); } catch(error){ message.textContent=`导入失败：${error.message}`; } };
document.getElementById('refresh').onclick=load; load(); setInterval(load,10000);
document.getElementById('logout').onclick=async event=>{ event.currentTarget.disabled=true; try { const response=await fetch('/api/gateway/v1/auth/logout',{method:'POST'}); if(!response.ok)throw new Error(`HTTP ${response.status}`); location.replace('/login'); } catch(error) { event.currentTarget.disabled=false; message.textContent=`退出失败：${error.message||error}`; } };
</script></body></html>"""
