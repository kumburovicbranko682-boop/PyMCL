// Java 管理页
import { bridge } from '../bridge';
import { store } from '../store';
import { toast, showLoading, showError, flyToTasks } from '../ui';
import { escapeHtml } from './common';

export async function renderJavaPage(container: HTMLElement) {
  showLoading(container);
  try {
    const [javas, settings] = await Promise.all([
      bridge.call<any[]>('get_java_list', { scan_system: true }),
      bridge.call<any>('get_settings'),
    ]);
    store.setJavaList(javas);
    store.setSettings(settings);
    render(container);
  } catch (e: any) {
    showError(container, '加载 Java 列表失败: ' + (e.message || '未知错误'), () => renderJavaPage(container));
  }
}

function render(container: HTMLElement) {
  const javas = store.javaList;
  const currentDefault = ((store.settings || {}) as any).default_java || '';
  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:16px">
      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
        <button class="btn btn-primary" id="btn-scan-java">🔍 扫描系统 Java</button>
        <button class="btn btn-primary" id="btn-download-java-8">下载 Java 8</button>
        <button class="btn btn-primary" id="btn-download-java-11">下载 Java 11</button>
        <button class="btn btn-primary" id="btn-download-java-17">下载 Java 17</button>
        <button class="btn btn-primary" id="btn-download-java-21">下载 Java 21</button>
      </div>
      <div class="grid-list" id="java-list">
        ${javas.length === 0
          ? '<div class="empty-state"><div class="empty-state-icon">☕</div><div>未检测到 Java</div></div>'
          : javas.map(j => {
            const isDefault = !!j.path && j.path === currentDefault;
            return `
            <div class="grid-item" data-java="${escapeHtml(j.path)}">
              <div class="grid-item-title">${escapeHtml(j.name)}${isDefault ? ' ✅' : ''}</div>
              <div class="grid-item-meta">
                <span>Java ${escapeHtml(j.major)}</span>
                <span style="word-break:break-all;font-family:monospace;font-size:11px">${escapeHtml(j.path)}</span>
              </div>
              <div class="grid-item-actions">
                <button class="btn btn-sm ${isDefault ? '' : 'btn-primary'}" data-action="set-default" data-java="${escapeHtml(j.path)}" ${isDefault ? 'disabled' : ''}>${isDefault ? '当前默认' : '设为默认'}</button>
              </div>
            </div>
          `;
          }).join('')}
      </div>
    </div>
  `;

  document.getElementById('btn-scan-java')?.addEventListener('click', async () => {
    try {
      const javas = await bridge.call<any[]>('get_java_list', { scan_system: true });
      store.setJavaList(javas);
      render(container);
      toast('扫描完成', 'success');
    } catch (e: any) {
      toast(e.message || '扫描失败', 'error');
    }
  });

  ['8', '11', '17', '21'].forEach(major => {
    document.getElementById(`btn-download-java-${major}`)?.addEventListener('click', async () => {
      const btn = document.getElementById(`btn-download-java-${major}`);
      try {
        await bridge.call<string>('download_java', { major });
        toast(`开始下载 Java ${major}`, 'info');
        await flyToTasks(btn, 'J', '#E8862E');
        const { router } = await import('../router');
        router.navigate('tasks');
      } catch (e: any) {
        toast(e.message || `下载 Java ${major} 失败`, 'error');
      }
    });
  });

  document.querySelectorAll('#java-list .grid-item').forEach(el => {
    const javaPath = (el as HTMLElement).dataset.java!;
    el.querySelector('[data-action="set-default"]')?.addEventListener('click', async () => {
      try {
        // 只提交这一个键。以前是 `{...store.settings, default_java}`，
        // store 里可能是别处存进去的残缺 settings，整份回传会把没带上的键一起写坏。
        await bridge.call('save_settings', { data: { default_java: javaPath } });
        store.setSettings({ ...(store.settings || {}), default_java: javaPath } as any);
        render(container);
        toast('已设为默认 Java', 'success');
      } catch (e: any) {
        toast(e.message || '设置失败', 'error');
      }
    });
  });
}