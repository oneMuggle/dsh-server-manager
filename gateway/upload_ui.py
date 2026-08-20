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
    box-sizing: border-box; width: min(480px, 100%); padding: 24px;
    border: 1px solid #3d4659; border-radius: 16px; color: #f5f7fb;
    background: #181d27; box-shadow: 0 24px 80px #0008;
  }
  #dshgw-upload-panel h2 { margin: 0 0 20px; font-size: 20px; }
  #dshgw-upload-panel label { display: grid; gap: 8px; margin: 14px 0; font-size: 14px; }
  #dshgw-upload-panel input {
    box-sizing: border-box; width: 100%; padding: 11px; border: 1px solid #465066;
    border-radius: 8px; color: inherit; background: #10131a; font: inherit;
  }
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
    <h2 id="dshgw-upload-title">上传到工作区</h2>
    <label>目标目录（相对于 workspace）
      <input id="dshgw-upload-directory" value="." maxlength="512" spellcheck="false">
    </label>
    <label>选择文件（单文件最大 100 MB）
      <input id="dshgw-upload-files" type="file" multiple>
    </label>
    <div id="dshgw-upload-actions">
      <button id="dshgw-upload-close" type="button">取消</button>
      <button id="dshgw-upload-submit" class="primary" type="button">开始上传</button>
    </div>
    <div id="dshgw-upload-status" role="status"></div>
  </section>
</div>
<script>
(() => {
  const button = document.getElementById('dshgw-upload-button');
  const overlay = document.getElementById('dshgw-upload-overlay');
  const close = document.getElementById('dshgw-upload-close');
  const submit = document.getElementById('dshgw-upload-submit');
  const directory = document.getElementById('dshgw-upload-directory');
  const files = document.getElementById('dshgw-upload-files');
  const status = document.getElementById('dshgw-upload-status');
  const shut = () => overlay.classList.remove('open');
  button.addEventListener('click', () => { status.textContent = ''; overlay.classList.add('open'); });
  close.addEventListener('click', shut);
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
        const query = new URLSearchParams({ directory: directory.value || '.', name: file.name });
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
