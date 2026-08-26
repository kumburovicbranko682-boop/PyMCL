// 设置页
import { bridge } from '../bridge';
import { store } from '../store';
import { toast, showLoading, showError, applyTheme } from '../ui';
import { escapeHtml } from './common';

export async function renderSettingsPage(container: HTMLElement) {
  showLoading(container);
  try {
    const settings = await bridge.call<any>('get_settings');
    store.setSettings(settings);
    render(container);
  } catch (e: any) {
    showError(container, '加载设置失败: ' + (e.message || '未知错误'), () => renderSettingsPage(container));
  }
}

function render(container: HTMLElement) {
  const s = (store.settings || {}) as any;
  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:16px;max-width:800px">
      <div class="card">
        <div class="card-header">⚙️ 基本设置</div>
        <div class="settings-section">
          <div class="setting-row">
            <span class="setting-label">共享 libraries</span>
            <div class="setting-control">
              <label class="toggle"><input type="checkbox" id="setting-share-libraries" ${s.share_libraries ? 'checked' : ''}><span class="toggle-slider"></span></label>
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">共享 assets</span>
            <div class="setting-control">
              <label class="toggle"><input type="checkbox" id="setting-share-assets" ${s.share_assets ? 'checked' : ''}><span class="toggle-slider"></span></label>
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">下载线程数</span>
            <div class="setting-control">
              <input class="input" id="setting-download-threads" type="number" value="${s.download_threads || 8}" style="width:100px" min="1" max="32">
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">默认内存 (MB)</span>
            <div class="setting-control">
              <input class="input" id="setting-default-memory" type="number" value="${s.default_memory_mb || 4096}" style="width:120px" min="512" max="65536">
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">默认分辨率</span>
            <div class="setting-control">
              <input class="input" id="setting-width" type="number" value="${(s.default_resolution || [854, 480])[0]}" style="width:80px" min="640" max="7680">
              <span>×</span>
              <input class="input" id="setting-height" type="number" value="${(s.default_resolution || [854, 480])[1]}" style="width:80px" min="360" max="4320">
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">允许多开</span>
            <div class="setting-control">
              <label class="toggle"><input type="checkbox" id="setting-allow-multi" ${s.allow_multi_instance ? 'checked' : ''}><span class="toggle-slider"></span></label>
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">深色模式</span>
            <div class="setting-control">
              <label class="toggle"><input type="checkbox" id="setting-ui-dark" ${s.ui_dark ? 'checked' : ''}><span class="toggle-slider"></span></label>
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">下载飞入动画</span>
            <div class="setting-control">
              <label class="toggle"><input type="checkbox" id="setting-ui-fly" ${s.ui_fly_animation !== false ? 'checked' : ''}><span class="toggle-slider"></span></label>
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">飞入动画时长 (ms)</span>
            <div class="setting-control">
              <input class="input" id="setting-ui-fly-dur" type="number" value="${s.ui_fly_duration_ms || 620}" style="width:120px" min="200" max="1200">
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">下载源</span>
            <div class="setting-control">
              <select class="select" id="setting-download-source">
                <option value="auto" ${s.download_source === 'auto' ? 'selected' : ''}>自动（官方慢则 BMCLAPI）</option>
                <option value="official" ${s.download_source === 'official' ? 'selected' : ''}>仅官方</option>
                <option value="bmclapi" ${s.download_source === 'bmclapi' ? 'selected' : ''}>仅 BMCLAPI</option>
              </select>
            </div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">🔑 账号设置</div>
        <div class="settings-section">
          <div class="setting-row">
            <span class="setting-label">Microsoft Client ID</span>
            <div class="setting-control"><input class="input" id="setting-ms-client-id" value="${escapeHtml(s.ms_client_id || '')}" style="width:300px"></div>
          </div>
          <div class="setting-row">
            <span class="setting-label">CurseForge API Key</span>
            <div class="setting-control"><input class="input" id="setting-cf-api-key" value="${escapeHtml(s.curseforge_api_key || '')}" style="width:300px"></div>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">🤖 AI 设置</div>
        <div class="settings-section">
          <div class="setting-row">
            <span class="setting-label">AI 模式</span>
            <div class="setting-control">
              <select class="select" id="setting-ai-mode">
                <option value="public" ${s.ai_mode !== 'custom' ? 'selected' : ''}>公共免费</option>
                <option value="custom" ${s.ai_mode === 'custom' ? 'selected' : ''}>自定义 NewAPI</option>
              </select>
            </div>
          </div>
          <div class="setting-row">
            <span class="setting-label">自定义 NewAPI 地址</span>
            <div class="setting-control"><input class="input" id="setting-ai-base-url" value="${escapeHtml(s.ai_base_url || '')}" placeholder="https://例如.com/v1" style="width:300px"></div>
          </div>
          <div class="setting-row">
            <span class="setting-label">AI API Key</span>
            <div class="setting-control"><input class="input" id="setting-ai-api-key" type="password" value="${escapeHtml(s.ai_api_key || '')}" style="width:300px"></div>
          </div>
          <div class="setting-row">
            <span class="setting-label">AI 模型</span>
            <div class="setting-control"><input class="input" id="setting-ai-model" value="${escapeHtml(s.ai_model || '')}" style="width:300px"></div>
          </div>
        </div>
      </div>

      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn btn-primary" id="btn-save-settings">💾 保存设置</button>
      </div>
    </div>
  `;

  document.getElementById('btn-save-settings')?.addEventListener('click', async () => {
    const settings = {
      share_libraries: (document.getElementById('setting-share-libraries') as HTMLInputElement).checked,
      share_assets: (document.getElementById('setting-share-assets') as HTMLInputElement).checked,
      download_threads: parseInt((document.getElementById('setting-download-threads') as HTMLInputElement).value) || 8,
      default_memory_mb: parseInt((document.getElementById('setting-default-memory') as HTMLInputElement).value) || 4096,
      default_resolution: [
        parseInt((document.getElementById('setting-width') as HTMLInputElement).value) || 854,
        parseInt((document.getElementById('setting-height') as HTMLInputElement).value) || 480,
      ],
      download_source: (document.getElementById('setting-download-source') as HTMLSelectElement).value,
      allow_multi_instance: (document.getElementById('setting-allow-multi') as HTMLInputElement).checked,
      ui_dark: (document.getElementById('setting-ui-dark') as HTMLInputElement).checked,
      ui_fly_animation: (document.getElementById('setting-ui-fly') as HTMLInputElement).checked,
      ui_fly_duration_ms: parseInt((document.getElementById('setting-ui-fly-dur') as HTMLInputElement).value) || 620,
      ms_client_id: (document.getElementById('setting-ms-client-id') as HTMLInputElement).value,
      curseforge_api_key: (document.getElementById('setting-cf-api-key') as HTMLInputElement).value,
      ai_mode: (document.getElementById('setting-ai-mode') as HTMLSelectElement).value,
      ai_base_url: (document.getElementById('setting-ai-base-url') as HTMLInputElement).value,
      ai_api_key: (document.getElementById('setting-ai-api-key') as HTMLInputElement).value,
      ai_model: (document.getElementById('setting-ai-model') as HTMLInputElement).value,
    };
    try {
      // Python 桥的签名是 save_settings(data: dict)：设置必须包在 `data` 里，
      // 顶层散传会被 _call_kwargs 判成缺参直接报错。
      await bridge.call('save_settings', { data: settings });
      // 合并而不是替换：本页只提交 13 个键，直接 setSettings 会让 store 变成残缺 dict，
      // 后面别的页面拿去用就会把缺的键当成「用户清空了」再发一次。
      store.setSettings({ ...(store.settings || {}), ...settings } as any);
      applyTheme(settings.ui_dark);
      toast('设置已保存', 'success');
    } catch (e: any) {
      toast(e.message || '保存设置失败', 'error');
    }
  });
}