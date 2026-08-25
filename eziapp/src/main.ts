// PyMCL EziApp 主入口
import './styles/main.css';
import { bridge, initBridge } from './bridge';
import { router, type PageKey } from './router';
import { store } from './store';
import { renderLaunchPage } from './pages/launch';
import { renderInstancesPage } from './pages/instances';
import { renderDownloadPage } from './pages/downloads';
import { renderTasksPage } from './pages/tasks';
import { renderAccountsPage } from './pages/accounts';
import { renderJavaPage } from './pages/java';
import { renderAIPage } from './pages/ai';
import { renderSettingsPage } from './pages/settings';
import { renderFeedbackPage } from './pages/feedback';
import { renderToolsPage } from './pages/tools';
import { renderServersPage } from './pages/servers';
import { renderPlaytimePage } from './pages/playtime';
import { renderMultiplayerPage } from './pages/multiplayer';
import { initBridgeLifecycle, toast, clearPageCleanups, applyTheme } from './ui';

const app = document.getElementById('app')!;

interface NavItem {
  key: PageKey;
  label: string;
  icon: string;
  group: string;
}

const NAV_ITEMS: NavItem[] = [
  { key: 'launch', label: '启动', icon: '🚀', group: '启动器' },
  { key: 'instances', label: '实例', icon: '📦', group: '启动器' },
  { key: 'downloads', label: '下载中心', icon: '⬇️', group: '下载' },
  { key: 'tasks', label: '下载任务', icon: '📋', group: '下载' },
  { key: 'accounts', label: '账号', icon: '👤', group: '启动器' },
  { key: 'java', label: 'Java', icon: '☕', group: '启动器' },
  { key: 'servers', label: '服务器', icon: '🌐', group: '启动器' },
  { key: 'playtime', label: '游玩时长', icon: '⏱️', group: '启动器' },
  { key: 'multiplayer', label: '联机', icon: '🎮', group: '启动器' },
  { key: 'ai', label: 'AI 助手', icon: '🤖', group: '工具' },
  { key: 'settings', label: '设置', icon: '⚙️', group: '工具' },
  { key: 'feedback', label: '反馈与帮助', icon: '💬', group: '工具' },
  { key: 'tools', label: '工具', icon: '🔧', group: '工具' },
];

function renderShell() {
  const groups: Record<string, NavItem[]> = {};
  for (const item of NAV_ITEMS) {
    if (!groups[item.group]) groups[item.group] = [];
    groups[item.group].push(item);
  }

  app.innerHTML = `
    <div class="sidebar">
      <div class="sidebar-title"><span class="nav-icon">⛏️</span><span class="nav-label">PyMCL</span></div>
      <nav class="sidebar-nav">
        ${Object.entries(groups).map(([groupName, items]) => `
          <div class="nav-group">
            <div class="nav-group-title">${groupName}</div>
            ${items.map(it => `
              <a class="nav-item" data-page="${it.key}" title="${it.label}">
                <span class="nav-icon">${it.icon}</span><span class="nav-label">${it.label}</span>
                ${it.key === 'tasks' ? '<span class="badge" id="task-badge" style="display:none">0</span>' : ''}
              </a>
            `).join('')}
          </div>
        `).join('')}
      </nav>
      <div style="padding:12px 16px;font-size:11px;color:var(--text-disabled)">
        <div id="bridge-status">桥接: 未连接</div>
      </div>
    </div>
    <div class="main-content">
      <header class="page-header" id="page-title">PyMCL 启动器</header>
      <main class="page-content" id="page-content"></main>
    </div>
    <div class="toast-container" id="toast-container"></div>
  `;

  document.querySelectorAll('.nav-item').forEach(el => {
    el.addEventListener('click', () => {
      const page = (el as HTMLElement).dataset.page as PageKey;
      router.navigate(page);
    });
  });

  router.subscribe(() => renderPage(router.page));
}

function renderPage(page: PageKey) {
  const content = document.getElementById('page-content');
  const title = document.getElementById('page-title');
  if (!content || !title) return;

  clearPageCleanups();

  document.querySelectorAll('.nav-item').forEach(el => {
    el.classList.toggle('active', (el as HTMLElement).dataset.page === page);
  });

  const titles: Record<PageKey, string> = {
    launch: '启动', instances: '实例管理', downloads: '下载中心',
    vanilla: '原版游戏', mods: '模组', modpacks: '整合包',
    datapacks: '数据包', resourcepacks: '资源包', shaders: '光影包',
    tasks: '下载任务', accounts: '账号管理', java: 'Java 管理',
    servers: '服务器列表', playtime: '游玩时长', multiplayer: '陶瓦联机',
    ai: 'AI 助手', settings: '设置', feedback: '反馈与帮助', tools: '工具',
  };
  title.textContent = titles[page] || 'PyMCL';

  switch (page) {
    case 'launch': renderLaunchPage(content); break;
    case 'instances': renderInstancesPage(content); break;
    case 'downloads': renderDownloadPage(content); break;
    case 'vanilla': renderDownloadPage(content, 'vanilla'); break;
    case 'mods': renderDownloadPage(content, 'mods'); break;
    case 'modpacks': renderDownloadPage(content, 'modpacks'); break;
    case 'datapacks': renderDownloadPage(content, 'datapacks'); break;
    case 'resourcepacks': renderDownloadPage(content, 'resourcepacks'); break;
    case 'shaders': renderDownloadPage(content, 'shaders'); break;
    case 'tasks': renderTasksPage(content); break;
    case 'accounts': renderAccountsPage(content); break;
    case 'java': renderJavaPage(content); break;
    case 'servers': renderServersPage(content); break;
    case 'playtime': renderPlaytimePage(content); break;
    case 'multiplayer': renderMultiplayerPage(content); break;
    case 'ai': renderAIPage(content); break;
    case 'settings': renderSettingsPage(content); break;
    case 'feedback': renderFeedbackPage(content); break;
    case 'tools': renderToolsPage(content); break;
    default: renderLaunchPage(content);
  }
}

async function loadInitialData() {
  const results = await Promise.allSettled([
    bridge.call('get_settings'),
    bridge.call('get_instances'),
    bridge.call('get_version_list'),
    bridge.call('get_java_list'),
    bridge.call('get_account_rows'),
  ]);
  const [settings, instances, versions, javas, accounts] = results;
  if (settings.status === 'fulfilled') {
    store.setSettings(settings.value as any);
    applyTheme(!!(settings.value as any)?.ui_dark);
  }
  if (instances.status === 'fulfilled') store.setInstances(instances.value as any);
  if (versions.status === 'fulfilled') store.setVersionList(versions.value as any);
  if (javas.status === 'fulfilled') store.setJavaList(javas.value as any);
  if (accounts.status === 'fulfilled') store.setAccounts(accounts.value as any);
  store.notify();
  // Promise.allSettled 永远不 reject，原来外面那个 try/catch 是死代码，
  // 桥接全挂时用户什么提示都收不到。改成按实际失败数报。
  if (results.every(r => r.status === 'rejected')) {
    toast('无法连接 Python 桥接服务', 'error');
  }
}

/**
 * 后端每个任务成功都会发一次 ui_changed，装整合包时能连着发几十条，
 * 每条都触发 5 个桥接请求 = 请求风暴。PySide6 那边有 280ms 防抖，这里对齐。
 */
function debounce(fn: () => void, wait: number) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  return () => {
    if (timer !== undefined) clearTimeout(timer);
    timer = setTimeout(() => { timer = undefined; fn(); }, wait);
  };
}

const reloadInitialData = debounce(() => { void loadInitialData(); }, 280);

/**
 * 任务列表只靠实时事件攒（task_added/progress/finished），页面刷新后
 * 正在跑的下载就从任务页整个消失了。启动时用 list_tasks 补一次快照；
 * 只补 map 里没有的 id，不覆盖事件已带来的更新。
 */
async function hydrateTasks() {
  try {
    const rows = await bridge.call<{ id?: string; title?: string; status?: string;
      success?: boolean; message?: string }[]>('list_tasks');
    if (!Array.isArray(rows)) return;
    for (const r of rows) {
      if (!r?.id || store.tasks.has(r.id)) continue;
      const running = r.status === 'running' || r.status === 'cancelling';
      store.updateTask(r.id, {
        title: r.title || '',
        success: running ? undefined : !!r.success,
        finishedMessage: running ? '' : (r.message || ''),
      });
    }
  } catch { /* 老版本桥没有 list_tasks：保持原有纯事件行为 */ }
}

async function init() {
  renderShell();
  renderPage(router.page);
  const bridgeConfigured = await initBridge();
  if (!bridgeConfigured) {
    toast('未获得本次启动的桥接凭据，请通过 eziapp_launcher.py 启动', 'error', 7000);
    return;
  }

  initBridgeLifecycle(() => {
    // 全局任务事件
    bridge.subscribe('task_added', (data: any) => {
      store.updateTask(data.task_id, { title: data.title || '' });
    });
    bridge.subscribe('progress', (data: any) => {
      store.updateTask(data.task_id, {
        current: data.current || 0, total: data.total || 0, message: data.message || '',
      });
    });
    bridge.subscribe('log', (data: any) => {
      store.addLog(data.task_id, data.text || '');
    });
    bridge.subscribe('finished', (data: any) => {
      store.updateTask(data.task_id, {
        success: data.success, finishedMessage: data.message || '',
      });
    });
    bridge.subscribe('task_count_changed', (data: any) => {
      store.taskCount = data.count || 0;
      store.notify();
    });
    bridge.subscribe('game_started', () => {
      store.gameRunning = true;
      store.notify();
    });
    bridge.subscribe('game_exited', () => {
      store.gameRunning = false;
      store.notify();
    });
    bridge.subscribe('ui_changed', () => {
      reloadInitialData();
    });

    store.subscribe(() => {
      const badge = document.getElementById('task-badge');
      if (badge) {
        const count = store.taskCount;
        badge.textContent = String(count);
        badge.style.display = count > 0 ? '' : 'none';
      }
    });

    void hydrateTasks();
    loadInitialData();
  });
}

init();
