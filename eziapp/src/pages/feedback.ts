// 反馈与帮助页。
import { bridge } from '../bridge';
import { store } from '../store';
import { showError, showLoading, toast } from '../ui';
import { errorMessage, escapeHtml } from './common';

interface FeedbackRecord {
  id?: string;
  ts?: number;
  category?: string;
  title?: string;
  ok?: boolean;
}

const categories = [
  ['bug', '功能异常'],
  ['crash', '崩溃闪退'],
  ['download', '下载问题'],
  ['multiplayer', '联机'],
  ['ai', 'AI 助手'],
  ['ui', '界面体验'],
  ['suggest', '建议'],
  ['other', '其他'],
] as const;

export function renderFeedbackPage(container: HTMLElement) {
  showLoading(container);
  void loadAndRender(container);
}

async function loadAndRender(container: HTMLElement) {
  try {
    const [history, articles, settings] = await Promise.all([
      bridge.call<FeedbackRecord[]>('feedback_history'),
      bridge.call<HelpArticle[]>('help_articles').catch(() => [] as HelpArticle[]),
      bridge.call<{ feedback_consent?: boolean }>('get_settings'),
    ]);
    if (!container.isConnected) return;
    render(container, Array.isArray(history) ? history : [], Array.isArray(articles) ? articles : [],
      settings?.feedback_consent === true);
  } catch (error) {
    if (!container.isConnected) return;
    showError(container, `加载反馈记录失败：${errorMessage(error, '未知错误')}`, () => void loadAndRender(container));
  }
}

interface HelpArticle {
  id?: string;
  title?: string;
  body?: string;
}

function render(container: HTMLElement, history: FeedbackRecord[], articles: HelpArticle[], consent: boolean) {
  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:16px;max-width:900px">
      <div class="card">
        <div class="card-header">💬 提交反馈</div>
        <div style="font-size:13px;color:var(--text-secondary);margin-bottom:16px">反馈将发送给已配置的反馈服务。提交前可选择是否附带系统与 Java 环境信息。</div>
        <div class="form-row">
          <div class="form-group" style="min-width:180px;flex:1">
            <label class="form-label">类型</label>
            <select class="select" id="feedback-category" style="width:100%">
              ${categories.map(([value, label]) => `<option value="${value}">${label}</option>`).join('')}
            </select>
          </div>
          <div class="form-group" style="min-width:260px;flex:3">
            <label class="form-label">标题</label>
            <input class="input" id="feedback-title" style="width:100%" maxlength="120" placeholder="简要描述遇到的问题">
          </div>
        </div>
        <div class="form-group">
          <label class="form-label">详细说明</label>
          <textarea class="input" id="feedback-body" style="width:100%;height:150px;resize:vertical" placeholder="请说明操作步骤、预期结果和实际结果。"></textarea>
        </div>
        <div class="form-row" style="align-items:flex-end">
          <div class="form-group" style="min-width:240px;flex:1">
            <label class="form-label">联系方式（可选）</label>
            <input class="input" id="feedback-contact" style="width:100%" placeholder="邮箱、QQ 或其他联系渠道">
          </div>
          <label style="display:flex;align-items:center;gap:6px;font-size:13px;margin-bottom:14px"><input type="checkbox" id="feedback-sysinfo" checked> 附带系统信息</label>
          <button class="btn btn-primary" id="feedback-submit">发送反馈</button>
          <button class="btn" id="feedback-sysinfo-preview">查看系统信息</button>
        </div>
        <label style="display:flex;align-items:center;gap:6px;font-size:13px;margin-top:4px">
          <input type="checkbox" id="feedback-consent" ${consent ? 'checked' : ''}>
          允许上传诊断数据（未同意时后端会拒绝发送反馈，可随时取消）
        </label>
      </div>
      <div class="card">
        <div class="card-header">常见问题</div>
        <div style="font-size:13px;color:var(--text-secondary);margin-bottom:10px">先扫一眼，很多启动 / 崩溃问题这里就有答案。</div>
        <div id="feedback-faq">${renderFaq(articles)}</div>
      </div>
      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:8px">
          <div class="card-header" style="margin:0">最近提交</div>
          <button class="btn btn-sm" id="feedback-refresh">↻ 刷新</button>
        </div>
        ${history.length ? `
          <table class="table">
            <thead><tr><th>时间</th><th>类型</th><th>标题</th><th>状态</th></tr></thead>
            <tbody>${history.slice(0, 20).map(renderHistoryRow).join('')}</tbody>
          </table>
        ` : '<div class="empty-state"><div class="empty-state-icon">📝</div><div>还没有提交记录</div></div>'}
      </div>
      <div class="card">
        <div class="card-header">帮助</div>
        <div style="font-size:13px;line-height:1.7;color:var(--text-secondary)">
          <div>下载或安装出错时，请先查看“下载任务”的日志。</div>
          <div>游戏无法启动时，可在“启动”页保留日志，并在反馈中描述所用实例、版本和加载器。</div>
          <div>桥接服务地址：${escapeHtml(bridgeUrlLabel())}</div>
        </div>
      </div>
    </div>
  `;

  container.querySelector<HTMLButtonElement>('#feedback-refresh')?.addEventListener('click', () => void loadAndRender(container));
  container.querySelector<HTMLInputElement>('#feedback-consent')?.addEventListener('change', async (event) => {
    const box = event.currentTarget as HTMLInputElement;
    const granted = box.checked;
    try {
      // 只提交这一个键：桥端 save_settings 是补丁语义，不会动别的设置。
      await bridge.call('save_settings', { data: { feedback_consent: granted } });
      store.setSettings({ ...(store.settings || {}), feedback_consent: granted } as any);
    } catch (error) {
      box.checked = !granted;
      toast(errorMessage(error, '保存失败'), 'error');
    }
  });
  container.querySelector<HTMLButtonElement>('#feedback-submit')?.addEventListener('click', async () => {
    const category = container.querySelector<HTMLSelectElement>('#feedback-category')!.value;
    const title = container.querySelector<HTMLInputElement>('#feedback-title')!.value.trim();
    const body = container.querySelector<HTMLTextAreaElement>('#feedback-body')!.value.trim();
    const contact = container.querySelector<HTMLInputElement>('#feedback-contact')!.value.trim();
    const includeSysinfo = container.querySelector<HTMLInputElement>('#feedback-sysinfo')!.checked;
    if (!title || !body) {
      toast('请填写标题和详细说明', 'warning');
      return;
    }
    if (!container.querySelector<HTMLInputElement>('#feedback-consent')?.checked) {
      toast('请先勾选「允许上传诊断数据」，否则后端会拒绝这次反馈', 'warning');
      return;
    }
    const button = container.querySelector<HTMLButtonElement>('#feedback-submit')!;
    button.disabled = true;
    button.textContent = '发送中…';
    try {
      await bridge.call('submit_feedback', { category, title, body, contact, include_sysinfo: includeSysinfo });
      toast('反馈已提交', 'success');
      await loadAndRender(container);
    } catch (error) {
      toast(errorMessage(error, '提交反馈失败'), 'error');
      button.disabled = false;
      button.textContent = '发送反馈';
    }
  });
  container.querySelector<HTMLButtonElement>('#feedback-sysinfo-preview')?.addEventListener('click', async () => {
    try {
      const info = await bridge.call<Record<string, unknown>>('collect_sysinfo', { scan_system_java: true });
      showSystemInfo(info);
    } catch (error) {
      toast(errorMessage(error, '读取系统信息失败'), 'error');
    }
  });
}

function renderFaq(articles: HelpArticle[]): string {
  if (!articles.length) {
    return '<div style="font-size:13px;color:var(--text-secondary)">暂无帮助条目</div>';
  }
  return articles.slice(0, 12).map(a => `
    <details style="margin-bottom:8px;padding:8px 10px;border:1px solid var(--border-color,#e5e5e5);border-radius:8px">
      <summary style="cursor:pointer;font-weight:600">${escapeHtml(a.title || a.id || '条目')}</summary>
      <pre style="white-space:pre-wrap;font-size:12px;margin:8px 0 0;font-family:inherit">${escapeHtml(a.body || '')}</pre>
    </details>
  `).join('');
}

function renderHistoryRow(record: FeedbackRecord): string {
  const typeLabel = categories.find(([id]) => id === record.category)?.[1] || record.category || '其他';
  return `<tr><td>${escapeHtml(formatTimestamp(record.ts))}</td><td>${escapeHtml(typeLabel)}</td><td>${escapeHtml(record.title || '未命名反馈')}</td><td>${record.ok === false ? '<span style="color:var(--danger)">失败</span>' : '<span style="color:var(--success)">已提交</span>'}</td></tr>`;
}

function formatTimestamp(value: unknown): string {
  const time = Number(value);
  if (!Number.isFinite(time) || time <= 0) return '未知时间';
  return new Intl.DateTimeFormat('zh-CN', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(time * 1000));
}

function bridgeUrlLabel(): string {
  try {
    return new URL(store.bridgeUrl).origin;
  } catch {
    return '本地桥接服务';
  }
}

function showSystemInfo(info: Record<string, unknown>) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  const text = JSON.stringify(info, null, 2);
  overlay.innerHTML = `
    <div class="modal" style="width:min(760px, calc(100vw - 32px))">
      <div class="modal-title">系统信息预览</div>
      <pre class="log-box" style="max-height:55vh">${escapeHtml(text)}</pre>
      <div class="modal-actions"><button class="btn btn-primary" id="close-sysinfo">关闭</button></div>
    </div>
  `;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector<HTMLButtonElement>('#close-sysinfo')?.addEventListener('click', close);
  overlay.addEventListener('click', (event) => { if (event.target === overlay) close(); });
}
