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
      const response = await fetch(`/api/v1/files/directories?${query}`);
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
        const response = await fetch(`/api/v1/files?${query}`, {
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
