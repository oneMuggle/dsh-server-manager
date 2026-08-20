from __future__ import annotations

UPLOAD_WIDGET = """
<style>
  #dshgw-upload-button {
    position: fixed; right: 24px; bottom: 24px; z-index: 2147483000;
    border: 0; border-radius: 999px; padding: 12px 18px;
    color: #fff; background: #315efb; box-shadow: 0 8px 28px #0005;
    font: 600 14px/1 system-ui, sans-serif; cursor: pointer;
  }
  #dshgw-upload-overlay {
    display: none; position: fixed; inset: 0; z-index: 2147483001;
    place-items: center; padding: 24px; background: #0009;
    font-family: system-ui, sans-serif;
  }
  #dshgw-upload-overlay.open { display: grid; }
  #dshgw-upload-panel {
    box-sizing: border-box; width: min(680px, 100%); max-height: min(82vh, 760px); padding: 24px;
    border: 1px solid #3d4659; border-radius: 16px; color: #f5f7fb;
    background: #181d27; box-shadow: 0 24px 80px #0008; overflow: auto;
  }
  #dshgw-upload-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  #dshgw-upload-panel h2 { margin: 0; font-size: 20px; }
  #dshgw-upload-panel .subtitle { margin: 7px 0 18px; color: #aeb9d2; font-size: 13px; }
  #dshgw-upload-x { border: 0; padding: 4px 8px; color: #aeb9d2; background: transparent; font-size: 24px; cursor: pointer; }
  #dshgw-upload-panel label { display: grid; gap: 8px; margin: 14px 0; font-size: 14px; }
  #dshgw-upload-panel input {
    box-sizing: border-box; width: 100%; padding: 11px; border: 1px solid #465066;
    border-radius: 8px; color: inherit; background: #10131a; font: inherit;
  }
  #dshgw-upload-crumbs {
    display: flex; align-items: center; gap: 4px; min-height: 42px; padding: 8px 10px;
    border: 1px solid #3d4659; border-radius: 10px; overflow-x: auto; white-space: nowrap;
    background: #10131a;
  }
  #dshgw-upload-crumbs button { border: 0; padding: 5px 7px; color: #8eb0ff; background: transparent; font: inherit; cursor: pointer; }
  #dshgw-upload-crumbs span { color: #5f6a80; }
  #dshgw-upload-directories {
    min-height: 150px; max-height: 300px; margin-top: 10px; border: 1px solid #3d4659;
    border-radius: 10px; overflow-y: auto; background: #10131a;
  }
  .dshgw-upload-folder {
    display: flex; width: 100%; gap: 10px; padding: 12px 14px; border: 0;
    border-bottom: 1px solid #272e3b; color: #eef2ff; background: transparent;
    font: inherit; text-align: left; cursor: pointer;
  }
  .dshgw-upload-folder:last-child { border-bottom: 0; }
  .dshgw-upload-folder:hover { background: #202735; }
  .dshgw-upload-empty { margin: 0; padding: 28px 16px; color: #8893aa; text-align: center; }
  #dshgw-upload-target { margin: 12px 2px 0; color: #b9c8f6; font-size: 13px; }
  #dshgw-upload-actions { display: flex; gap: 10px; margin-top: 18px; }
  #dshgw-upload-actions button {
    flex: 1; padding: 11px; border: 1px solid #465066; border-radius: 8px;
    color: inherit; background: transparent; font: inherit; cursor: pointer;
  }
  #dshgw-upload-actions button.primary { border-color: #315efb; background: #315efb; color: white; }
  #dshgw-upload-status { min-height: 20px; margin: 14px 0 0; color: #b9c8f6; font-size: 13px; white-space: pre-wrap; }
</style>
<button id="dshgw-upload-button" type="button">上传文件</button>
<div id="dshgw-upload-overlay" role="dialog" aria-modal="true" aria-labelledby="dshgw-upload-title">
  <section id="dshgw-upload-panel">
    <div id="dshgw-upload-header"><div>
      <h2 id="dshgw-upload-title">上传文件</h2>
      <p class="subtitle">浏览工作区目录，并将本机文件上传到当前目录。</p>
    </div><button id="dshgw-upload-x" type="button" aria-label="关闭">×</button></div>
    <nav id="dshgw-upload-crumbs" aria-label="当前上传目录"></nav>
    <div id="dshgw-upload-directories"><p class="dshgw-upload-empty">正在加载目录…</p></div>
    <div id="dshgw-upload-target">当前目录：workspace</div>
    <label>选择文件（单文件最大 100 MB）
      <input id="dshgw-upload-files" type="file" multiple>
    </label>
    <div id="dshgw-upload-actions">
      <button id="dshgw-upload-close" type="button">取消</button>
      <button id="dshgw-upload-submit" class="primary" type="button">上传到当前目录</button>
    </div>
    <div id="dshgw-upload-status" role="status"></div>
  </section>
</div>
<script>
(() => {
  const button = document.getElementById('dshgw-upload-button');
  const overlay = document.getElementById('dshgw-upload-overlay');
  const close = document.getElementById('dshgw-upload-close');
  const closeX = document.getElementById('dshgw-upload-x');
  const submit = document.getElementById('dshgw-upload-submit');
  const crumbs = document.getElementById('dshgw-upload-crumbs');
  const directories = document.getElementById('dshgw-upload-directories');
  const target = document.getElementById('dshgw-upload-target');
  const files = document.getElementById('dshgw-upload-files');
  const status = document.getElementById('dshgw-upload-status');
  let currentDirectory = '.';
  const shut = () => overlay.classList.remove('open');
  const childPath = (base, name) => base === '.' ? name : `${base}/${name}`;
  const renderCrumbs = () => {
    crumbs.replaceChildren();
    const add = (label, path) => {
      const item = document.createElement('button');
      item.type = 'button'; item.textContent = label;
      item.addEventListener('click', () => loadDirectory(path));
      crumbs.appendChild(item);
    };
    add('workspace', '.');
    let accumulated = '';
    for (const part of currentDirectory === '.' ? [] : currentDirectory.split('/')) {
      const separator = document.createElement('span'); separator.textContent = '/'; crumbs.appendChild(separator);
      accumulated = accumulated ? `${accumulated}/${part}` : part;
      add(part, accumulated);
    }
  };
  const loadDirectory = async path => {
    directories.innerHTML = '<p class="dshgw-upload-empty">正在加载目录…</p>';
    status.textContent = '';
    try {
      const query = new URLSearchParams({ directory: path });
      const response = await fetch(`/api/gateway/v1/files/directories?${query}`);
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
      currentDirectory = result.directory || '.';
      renderCrumbs();
      target.textContent = `当前目录：workspace${currentDirectory === '.' ? '' : `/${currentDirectory}`}`;
      directories.replaceChildren();
      if (currentDirectory !== '.') {
        const parent = currentDirectory.includes('/') ? currentDirectory.slice(0, currentDirectory.lastIndexOf('/')) : '.';
        const up = document.createElement('button'); up.type = 'button'; up.className = 'dshgw-upload-folder'; up.textContent = '↩ 返回上一级';
        up.addEventListener('click', () => loadDirectory(parent)); directories.appendChild(up);
      }
      for (const name of result.directories || []) {
        const row = document.createElement('button'); row.type = 'button'; row.className = 'dshgw-upload-folder'; row.textContent = `📁 ${name}`;
        row.addEventListener('click', () => loadDirectory(childPath(currentDirectory, name)));
        directories.appendChild(row);
      }
      if (!directories.children.length) {
        const empty = document.createElement('p'); empty.className = 'dshgw-upload-empty'; empty.textContent = '当前目录没有子目录'; directories.appendChild(empty);
      }
      if (result.truncated) status.textContent = '目录较多，仅显示前 2000 项。';
    } catch (error) {
      directories.innerHTML = '<p class="dshgw-upload-empty">目录加载失败</p>';
      status.textContent = `目录加载失败：${error && error.message ? error.message : error}`;
    }
  };
  button.addEventListener('click', () => { status.textContent = ''; overlay.classList.add('open'); loadDirectory(currentDirectory); });
  close.addEventListener('click', shut);
  closeX.addEventListener('click', shut);
  overlay.addEventListener('click', event => { if (event.target === overlay) shut(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') shut(); });
  submit.addEventListener('click', async () => {
    const selected = Array.from(files.files || []);
    if (!selected.length) { status.textContent = '请先选择文件。'; return; }
    submit.disabled = true;
    let completed = 0;
    try {
      for (const file of selected) {
        status.textContent = `正在上传 ${completed + 1}/${selected.length}：${file.name}`;
        const query = new URLSearchParams({ directory: currentDirectory, name: file.name });
        const response = await fetch(`/api/gateway/v1/files/upload?${query}`, {
          method: 'POST',
          headers: { 'content-type': 'application/octet-stream' },
          body: file,
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || `HTTP ${response.status}`);
        completed += 1;
      }
      status.textContent = `上传完成：${completed} 个文件。`;
      files.value = '';
    } catch (error) {
      status.textContent = `上传失败：${error && error.message ? error.message : error}`;
    } finally {
      submit.disabled = false;
    }
  });
})();
</script>
""".encode("utf-8")

ADMIN_WIDGET = """
<style>
  #dshgw-admin-button {
    position: fixed; right: 24px; bottom: 78px; z-index: 2147483000;
    border: 1px solid #465066; border-radius: 999px; padding: 11px 17px;
    color: #f5f7fb; background: #181d27; box-shadow: 0 8px 28px #0005;
    font: 600 14px/1 system-ui, sans-serif; text-decoration: none;
  }
</style>
<a id="dshgw-admin-button" href="/admin">管理后台</a>
""".encode("utf-8")
