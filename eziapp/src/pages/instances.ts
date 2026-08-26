// 实例管理页
import { bridge } from '../bridge';
import { store } from '../store';
import { toast, showLoading, showEmpty, showError, confirmDialog, inputDialog } from '../ui';
import { escapeHtml } from './common';

export async function renderInstancesPage(container: HTMLElement) {
  showLoading(container);
  try {
    const instances = await bridge.call<any[]>('get_instances');
    store.setInstances(instances);
    render(container);
  } catch (e: any) {
    showError(container, '加载实例失败: ' + (e.message || '未知错误'), () => renderInstancesPage(container));
  }
}

function render(container: HTMLElement) {
  const instances = store.instances;
  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:16px">
      <div style="display:flex;gap:8px;align-items:center">
        <button class="btn btn-primary" id="btn-create-instance">➕ 新建实例</button>
      </div>
      <div class="grid-list" id="instance-list">
        ${instances.length === 0
          ? '<div class="empty-state"><div class="empty-state-icon">📦</div><div>暂无实例，请创建一个</div></div>'
          : instances.map(inst => `
            <div class="grid-item" data-instance="${escapeHtml(inst.name)}">
              <div class="grid-item-title">${escapeHtml(inst.name)}</div>
              <div class="grid-item-meta">
                <span>MC: ${escapeHtml(inst.mc || '?')}</span>
                <span>版本: ${escapeHtml(inst.versions || 0)}</span>
                ${inst.pack ? `<span>整合包: ${escapeHtml(inst.pack)}</span>` : ''}
                <span>Java: ${escapeHtml(inst.javaLabel || '自动')}</span>
              </div>
              <div class="grid-item-actions">
                <button class="btn btn-sm btn-primary" data-action="launch">▶ 启动</button>
                <button class="btn btn-sm" data-action="rename">✏ 重命名</button>
                <button class="btn btn-sm" data-action="open">📂 打开目录</button>
                <button class="btn btn-sm" data-action="versions">📋 版本</button>
                <button class="btn btn-sm btn-danger" data-action="delete">🗑 删除</button>
              </div>
            </div>
          `).join('')}
      </div>
    </div>
  `;

  document.getElementById('btn-create-instance')?.addEventListener('click', async () => {
    const name = await inputDialog('新建实例', '实例名称', '');
    if (!name) return;
    try {
      await bridge.call('create_instance', { name });
      toast('实例创建成功', 'success');
      renderInstancesPage(container);
    } catch (e: any) {
      toast(e.message || '创建失败', 'error');
    }
  });

  document.querySelectorAll('#instance-list .grid-item').forEach(el => {
    const instanceName = (el as HTMLElement).dataset.instance!;
    el.querySelectorAll('[data-action]').forEach(btn => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const action = (btn as HTMLElement).dataset.action!;
        switch (action) {
          case 'launch': {
            store.currentInstance = instanceName;
            const { router } = await import('../router');
            router.navigate('launch');
            break;
          }
          case 'rename': {
            const newName = await inputDialog('重命名实例', '新名称', instanceName);
            if (!newName || newName === instanceName) return;
            try {
              await bridge.call('rename_instance', { name: instanceName, new_name: newName });
              toast('重命名成功', 'success');
              renderInstancesPage(container);
            } catch (err: any) {
              toast(err.message || '重命名失败', 'error');
            }
            break;
          }
          case 'open': {
            try {
              await bridge.call('open_instance_folder', { name: instanceName });
              toast('已打开实例目录', 'info');
            } catch (err: any) {
              toast(err.message || '打开失败', 'error');
            }
            break;
          }
          case 'versions': {
            showInstanceVersions(container, instanceName);
            break;
          }
          case 'delete': {
            const confirmed = await confirmDialog('删除实例', `确定要删除实例 "${instanceName}" 吗？此操作不可恢复。`);
            if (!confirmed) return;
            try {
              await bridge.call('delete_instance', { name: instanceName });
              toast('实例已删除', 'success');
              renderInstancesPage(container);
            } catch (err: any) {
              toast(err.message || '删除失败', 'error');
            }
            break;
          }
        }
      });
    });
  });
}

async function showInstanceVersions(container: HTMLElement, instanceName: string) {
  try {
    const versions = await bridge.call<string[]>('get_installed_versions', { instance: instanceName });
    const modal = document.createElement('div');
    modal.className = 'modal-overlay';
    modal.innerHTML = `
      <div class="modal" style="min-width:500px">
        <div class="modal-title">📋 实例版本 - ${escapeHtml(instanceName)}</div>
        <div style="margin-bottom:12px">
          <button class="btn btn-sm btn-primary" id="btn-add-version">➕ 安装版本</button>
        </div>
        <div id="version-list">
          ${versions.length === 0
            ? '<div style="color:var(--text-secondary);padding:16px;text-align:center">暂无已安装版本</div>'
            : versions.map(v => `
              <div style="display:flex;align-items:center;justify-content:space-between;padding:8px;border-bottom:1px solid var(--border-light)">
                <span><strong>${escapeHtml(v)}</strong></span>
                <div style="display:flex;gap:4px">
                  <button class="btn btn-sm btn-danger" data-action="uninstall-version" data-version="${escapeHtml(v)}">卸载</button>
                </div>
              </div>
            `).join('')}
        </div>
        <div class="modal-actions"><button class="btn" id="close-versions">关闭</button></div>
      </div>
    `;
    document.body.appendChild(modal);

    modal.querySelector('#close-versions')?.addEventListener('click', () => modal.remove());
    modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });

    modal.querySelectorAll('[data-action="uninstall-version"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const version = (btn as HTMLElement).dataset.version!;
        const confirmed = await confirmDialog('卸载版本', `确定要卸载版本 ${version} 吗？`);
        if (!confirmed) return;
        try {
          // spec 不带实例前缀时后端落到 default_instance——从非默认实例的
          // 版本列表点卸载会删错实例。带上本弹窗对应的实例名。
          await bridge.call('uninstall_version', { spec: `${instanceName} / ${version}` });
          toast('版本已卸载', 'success');
          modal.remove();
          showInstanceVersions(container, instanceName);
        } catch (e: any) {
          toast(e.message || '卸载失败', 'error');
        }
      });
    });

    modal.querySelector('#btn-add-version')?.addEventListener('click', () => {
      modal.remove();
      store.currentInstance = instanceName;
      import('../router').then(({ router: r }) => r.navigate('downloads'));
    });
  } catch (e: any) {
    toast(e.message || '加载版本失败', 'error');
  }
}