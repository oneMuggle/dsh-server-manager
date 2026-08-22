from __future__ import annotations

UPLOAD_WIDGET = """
<style>
  #dshgw-upload-button {
    position: fixed; right: 24px; bottom: 24px; z-index: 2147483000;
    min-height: 36px; border: 0; border-radius: 18px; padding: 7px 16px;
    color: var(--dsw-alias-label-primary-foreground, #fff);
    background: var(--dsw-alias-button-primary-fill, #0f1115);
    box-shadow: var(--dsw-shadow-lv2, 0 8px 28px rgba(0, 0, 0, .18));
    font-family: inherit; font-size: 14px; line-height: 22px; font-weight: 500; cursor: pointer;
  }
  #dshgw-upload-button:hover { background: var(--dsw-alias-button-primary-hover, #43454a); }
  #dshgw-upload-button:focus-visible,
  #dshgw-upload-panel button:focus-visible,
  #dshgw-upload-panel input:focus-visible {
    outline: 2px solid var(--dsw-alias-brand-primary-new-colorprimary-new-color, #4176e6);
    outline-offset: 2px;
  }
  #dshgw-upload-overlay {
    display: none; position: fixed; inset: 0; z-index: 2147483001;
    place-items: center; padding: 24px;
    color: var(--dsw-alias-label-primary, #0f1115);
    background: var(--dsw-alias-bg-mask-1, rgba(0, 0, 0, .24));
    backdrop-filter: var(--dsw-mask-blur, blur(2px));
    font-family: inherit;
  }
  #dshgw-upload-overlay.open { display: grid; }
  #dshgw-upload-panel {
    box-sizing: border-box; width: min(680px, 100%); max-height: min(82vh, 760px); padding: 24px;
    border: 1px solid var(--dsw-alias-border-inverted, transparent); border-radius: 24px;
    color: var(--dsw-alias-label-primary, #0f1115);
    background: var(--dsw-alias-bg-layer-2, #fff);
    box-shadow: var(--dsw-shadow-lv3, 0 24px 80px rgba(0, 0, 0, .24)); overflow: auto;
  }
  #dshgw-upload-header { display: flex; align-items: flex-start; justify-content: space-between; gap: 16px; }
  #dshgw-upload-panel h2 { margin: 0; color: var(--dsw-alias-label-primary, #0f1115); font-size: 16px; line-height: 24px; font-weight: 500; }
  #dshgw-upload-panel .subtitle { margin: 6px 0 18px; color: var(--dsw-alias-label-secondary, #61666b); font-size: 14px; line-height: 22px; }
  #dshgw-upload-x {
    display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px;
    border: 0; border-radius: 8px; padding: 0; color: var(--dsw-alias-label-secondary, #61666b);
    background: transparent; font-family: inherit; font-size: 22px; line-height: 1; font-weight: 400; cursor: pointer;
  }
  #dshgw-upload-x:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(38, 49, 72, .06)); }
  #dshgw-upload-panel label { display: grid; gap: 8px; margin: 14px 0; color: var(--dsw-alias-label-primary, #0f1115); font-size: 14px; line-height: 22px; }
  #dshgw-upload-panel input {
    box-sizing: border-box; width: 100%; min-height: 40px; padding: 7px 8px;
    border: 1px solid var(--dsw-alias-border-l2, rgba(0, 0, 0, .1)); border-radius: 8px;
    color: var(--dsw-alias-label-primary, #0f1115);
    background: var(--dsw-alias-bg-layer-1, #fff); font: inherit;
  }
  #dshgw-upload-panel input:hover { border-color: var(--dsw-alias-border-l3, rgba(0, 0, 0, .12)); }
  #dshgw-upload-panel input::file-selector-button {
    min-height: 28px; margin-right: 10px; padding: 3px 12px;
    border: 1px solid var(--dsw-alias-border-l2, rgba(0, 0, 0, .1)); border-radius: 14px;
    color: var(--dsw-alias-label-primary, #0f1115); background: transparent;
    font: inherit; cursor: pointer;
  }
  #dshgw-upload-panel input::file-selector-button:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(38, 49, 72, .06)); }
  #dshgw-upload-crumbs {
    display: flex; align-items: center; gap: 4px; min-height: 42px; padding: 8px 10px;
    border: 1px solid var(--dsw-alias-border-l2, rgba(0, 0, 0, .1)); border-radius: 10px;
    overflow-x: auto; white-space: nowrap; background: var(--dsw-alias-bg-layer-1, #fff);
    scrollbar-color: var(--dsw-alias-scrollbar-bg-l1, #e5e5e5) transparent;
  }
  #dshgw-upload-crumbs button {
    border: 0; border-radius: 8px; padding: 5px 7px;
    color: var(--dsw-alias-label-primary, #0f1115); background: transparent;
    font: inherit; cursor: pointer;
  }
  #dshgw-upload-crumbs button:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(38, 49, 72, .06)); }
  #dshgw-upload-crumbs span { color: var(--dsw-alias-label-dimmed, #e1e5ee); }
  #dshgw-upload-directories {
    min-height: 150px; max-height: 300px; margin-top: 10px;
    border: 1px solid var(--dsw-alias-border-l2, rgba(0, 0, 0, .1)); border-radius: 10px;
    overflow-y: auto; background: var(--dsw-alias-bg-layer-1, #fff);
    scrollbar-color: var(--dsw-alias-scrollbar-bg-l1, #e5e5e5) transparent;
  }
  .dshgw-upload-folder {
    display: flex; width: 100%; gap: 10px; padding: 12px 14px; border: 0;
    border-bottom: 1px solid var(--dsw-alias-border-l1, rgba(0, 0, 0, .04));
    color: var(--dsw-alias-label-primary, #0f1115); background: transparent;
    font: inherit; text-align: left; cursor: pointer;
  }
  .dshgw-upload-folder:last-child { border-bottom: 0; }
  .dshgw-upload-folder:hover { background: var(--dsw-alias-interactive-bg-hover, rgba(38, 49, 72, .06)); }
  .dshgw-upload-folder:active { background: var(--dsw-alias-interactive-bg-active, rgba(38, 49, 72, .1)); }
  .dshgw-upload-empty { margin: 0; padding: 28px 16px; color: var(--dsw-alias-label-tertiary, #81858c); text-align: center; }
  #dshgw-upload-target { margin: 12px 2px 0; color: var(--dsw-alias-label-secondary, #61666b); font-size: 13px; line-height: 20px; }
  #dshgw-upload-actions { display: flex; gap: 10px; margin-top: 18px; }
  #dshgw-upload-actions button {
    flex: 1; min-height: 36px; padding: 7px 14px;
    border: 1px solid var(--dsw-alias-border-l2, rgba(0, 0, 0, .1)); border-radius: 18px;
    color: var(--dsw-alias-label-primary, #0f1115); background: transparent;
    font-family: inherit; font-size: 14px; line-height: 22px; font-weight: 400; cursor: pointer;
  }
  #dshgw-upload-actions button:hover:not(:disabled) { background: var(--dsw-alias-interactive-bg-hover, rgba(38, 49, 72, .06)); }
  #dshgw-upload-actions button.primary {
    border-color: transparent; color: var(--dsw-alias-label-primary-foreground, #fff);
    background: var(--dsw-alias-button-primary-fill, #0f1115);
  }
  #dshgw-upload-actions button.primary:hover:not(:disabled) { background: var(--dsw-alias-button-primary-hover, #43454a); }
  #dshgw-upload-actions button:disabled { cursor: not-allowed; opacity: .4; }
  #dshgw-upload-status {
    min-height: 20px; margin: 14px 0 0;
    color: var(--dsw-alias-label-secondary, #61666b);
    font-size: 13px; line-height: 20px; white-space: pre-wrap;
  }
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
    position: fixed; right: 24px; bottom: 132px; z-index: 2147483000;
    border: 1px solid #465066; border-radius: 999px; padding: 11px 17px;
    color: #f5f7fb; background: #181d27; box-shadow: 0 8px 28px #0005;
    font: 600 14px/1 system-ui, sans-serif; text-decoration: none;
  }
</style>
<a id="dshgw-admin-button" href="/admin">管理后台</a>
""".encode("utf-8")

LOGOUT_WIDGET = """
<style>
  #dshgw-logout-button {
    position: fixed; right: 24px; bottom: 78px; z-index: 2147483000;
    border: 1px solid var(--dsw-alias-border-l2, rgba(0, 0, 0, .1));
    border-radius: 999px; padding: 10px 17px;
    color: var(--dsw-alias-label-primary, #0f1115);
    background: var(--dsw-alias-bg-layer-2, #fff);
    box-shadow: var(--dsw-shadow-lv2, 0 8px 28px rgba(0, 0, 0, .18));
    font: 500 14px/1 system-ui, sans-serif; cursor: pointer;
  }
  #dshgw-logout-button:hover { background: var(--dsw-alias-interactive-bg-hover, #f2f3f5); }
  #dshgw-logout-button:disabled { cursor: wait; opacity: .55; }
</style>
<button id="dshgw-logout-button" type="button">退出登录</button>
<script>
(() => {
  const button = document.getElementById('dshgw-logout-button');
  button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      const response = await fetch('/api/gateway/v1/auth/logout', { method: 'POST' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      location.replace('/login');
    } catch (error) {
      button.disabled = false;
      alert(`退出失败：${error && error.message ? error.message : error}`);
    }
  });
})();
</script>
""".encode("utf-8")
