// 陶瓦联机页
import { bridge } from '../bridge';
import { router } from '../router';
import { confirmDialog, inputDialog, registerPageCleanup, toast, flyToTasks } from '../ui';
import { errorMessage, escapeHtml } from './common';

interface TerracottaProfile {
  name?: string;
  kind?: string;
  vendor?: string;
}

interface TerracottaSnapshot {
  supported?: boolean;
  installed?: boolean;
  running?: boolean;
  state?: string;
  label?: string;
  room?: string;
  url?: string;
  error?: string;
  error_hint?: string;
  difficulty_hint?: string;
  profiles?: TerracottaProfile[];
  game_running?: boolean;
}

const STATE_COLORS: Record<string, string> = {
  'host-ok': '#2E9B6B',
  'guest-ok': '#2E9B6B',
  waiting: '#2E9B6B',
  idle: '#4C8BF5',
  exception: '#D95568',
  fatal: '#D95568',
  unsupported: '#D95568',
  missing: '#E8862E',
};

export function renderMultiplayerPage(container: HTMLElement) {
  let busy = false;
  let autoStarted = false;
  let lastState = '';
  let pollTimer: ReturnType<typeof setInterval> | undefined;

  container.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:16px;max-width:900px" id="mp-root">
      <div class="card">
        <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
          <div class="card-header" style="margin:0">🎮 陶瓦联机</div>
          <span id="mp-state-pill" style="display:inline-block;padding:4px 10px;border-radius:999px;font-size:12px;color:#fff;background:#888">未就绪</span>
        </div>
        <div id="mp-lan-hint" style="font-size:12px;color:var(--text-secondary);margin-top:8px;line-height:1.6"></div>
        <div id="mp-status" style="font-size:13px;margin-top:10px;line-height:1.6;color:var(--text-secondary)">正在检查联机内核…</div>
      </div>
      <div class="card" id="mp-firewall-card">
        <div class="card-header">🛡️ 防火墙</div>
        <div style="font-size:13px;color:var(--text-secondary);line-height:1.6;margin-bottom:10px">
          陶瓦在后台运行，Windows 通常不会弹窗。点「允许访问」后在 UAC 选是；若装了安全软件还需手动放行。
        </div>
        <div class="grid-item-actions">
          <button class="btn btn-primary" id="mp-fw-allow">允许访问</button>
          <button class="btn" id="mp-fw-open">打开设置</button>
        </div>
      </div>
      <div class="card" id="mp-room-card" style="display:none">
        <div class="card-header">邀请码（点击复制）</div>
        <div id="mp-room" style="font-size:20px;font-weight:700;margin:8px 0;cursor:pointer;user-select:all">—</div>
        <div id="mp-url-hint" style="font-size:12px;color:var(--text-secondary);line-height:1.5"></div>
      </div>
      <div id="mp-actions" style="display:flex;flex-direction:column;gap:10px"></div>
      <div class="card" id="mp-players-card" style="display:none">
        <div class="card-header">房间成员</div>
        <div id="mp-players" style="display:flex;flex-direction:column;gap:8px"></div>
      </div>
    </div>
  `;

  const statePill = container.querySelector<HTMLElement>('#mp-state-pill')!;
  const lanHint = container.querySelector<HTMLElement>('#mp-lan-hint')!;
  const statusEl = container.querySelector<HTMLElement>('#mp-status')!;
  const roomCard = container.querySelector<HTMLElement>('#mp-room-card')!;
  const roomEl = container.querySelector<HTMLElement>('#mp-room')!;
  const urlHint = container.querySelector<HTMLElement>('#mp-url-hint')!;
  const actionsEl = container.querySelector<HTMLElement>('#mp-actions')!;
  const playersCard = container.querySelector<HTMLElement>('#mp-players-card')!;
  const playersEl = container.querySelector<HTMLElement>('#mp-players')!;

  void bridge.call<string>('lan_hint').then((text) => {
    if (container.isConnected) lanHint.textContent = text || '';
  }).catch(() => { /* ignore */ });

  roomEl.addEventListener('click', () => {
    const text = roomEl.textContent?.trim();
    if (!text || text === '—') return;
    void navigator.clipboard.writeText(text).then(() => toast('已复制邀请码', 'success'));
  });

  container.querySelector<HTMLButtonElement>('#mp-fw-allow')?.addEventListener('click', async () => {
    try {
      const msg = await bridge.call<string>('terracotta_allow_firewall');
      toast(msg || '防火墙规则已处理', 'success');
    } catch (error) {
      toast(errorMessage(error, '防火墙操作失败'), 'error');
    }
  });
  container.querySelector<HTMLButtonElement>('#mp-fw-open')?.addEventListener('click', async () => {
    try {
      await bridge.call('terracotta_open_firewall_settings');
    } catch (error) {
      toast(errorMessage(error, '无法打开防火墙设置'), 'error');
    }
  });

  async function snapshot(): Promise<TerracottaSnapshot> {
    try {
      return await bridge.call<TerracottaSnapshot>('terracotta_snapshot');
    } catch (error) {
      return { state: 'fatal', label: errorMessage(error, '获取状态失败'), error: errorMessage(error, '') };
    }
  }

  async function prepare() {
    if (busy) return;
    busy = true;
    try {
      const taskId = await bridge.call<string>('terracotta_prepare');
      toast('正在准备陶瓦联机…', 'info');
      await flyToTasks(actionsEl, '联', '#2E9B6B');
      router.navigate('tasks');
      void taskId;
    } catch (error) {
      busy = false;
      toast(errorMessage(error, '准备失败'), 'error');
    }
  }

  function renderActionCard(letter: string, color: string, title: string, desc: string, buttons: string): string {
    return `
      <div class="card" style="display:flex;gap:14px;align-items:center">
        <div style="width:46px;height:46px;border-radius:10px;background:${color};color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700">${escapeHtml(letter)}</div>
        <div style="flex:1;min-width:0">
          <div style="font-weight:600;margin-bottom:4px">${escapeHtml(title)}</div>
          <div style="font-size:12px;color:var(--text-secondary);line-height:1.5">${escapeHtml(desc)}</div>
        </div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">${buttons}</div>
      </div>
    `;
  }

  function fillActions(state: string, info: TerracottaSnapshot, room: string) {
    const label = info.label || state;
    if (state === 'unsupported') {
      actionsEl.innerHTML = renderActionCard('!', '#D95568', '当前系统不支持', '陶瓦联机暂未提供此架构的官方内核。', `<a class="btn" href="https://github.com/burningtnt/Terracotta" target="_blank" rel="noreferrer">了解</a>`);
      return;
    }
    if (state === 'missing') {
      actionsEl.innerHTML = renderActionCard('瓦', '#2E9B6B', '下载陶瓦联机内核', '首次使用需要下载官方内核，之后可直接开房。', `<button class="btn btn-primary" id="mp-act-prepare">下载</button>`);
      actionsEl.querySelector('#mp-act-prepare')?.addEventListener('click', () => void prepare());
      return;
    }
    if (state === 'idle') {
      actionsEl.innerHTML = renderActionCard('▶', '#4C8BF5', '启动联机内核', '内核已安装，点一下即可开始联机。', `<button class="btn btn-primary" id="mp-act-prepare">启动</button>`);
      actionsEl.querySelector('#mp-act-prepare')?.addEventListener('click', () => void prepare());
      return;
    }
    if (state === 'waiting') {
      actionsEl.innerHTML =
        renderActionCard('房', '#2E9B6B', '我想当房主', '创建房间并生成邀请码，与好友一起畅玩。', `<button class="btn btn-primary" id="mp-act-host">创建</button>`) +
        renderActionCard('客', '#4C8BF5', '我想当房客', '输入房主提供的邀请码加入游戏世界。', `<button class="btn" id="mp-act-join">加入</button>`);
      actionsEl.querySelector('#mp-act-host')?.addEventListener('click', () => void onHost());
      actionsEl.querySelector('#mp-act-join')?.addEventListener('click', () => void onJoin());
      return;
    }
    if (state === 'host-scanning' || state === 'host-starting') {
      actionsEl.innerHTML = renderActionCard('扫', '#E8862E', '正在扫描局域网世界', '请启动游戏，进入单人世界，按 ESC，选择对局域网开放。', `<button class="btn" id="mp-act-idle">退出</button>`);
      actionsEl.querySelector('#mp-act-idle')?.addEventListener('click', () => void onIdle());
      return;
    }
    if (state === 'host-ok') {
      actionsEl.innerHTML =
        renderActionCard('复', '#2E9B6B', '复制邀请码', '好友在联机页选择房客并输入该邀请码即可加入。', `<button class="btn btn-primary" id="mp-act-copy">复制</button>`) +
        renderActionCard('返', '#888888', '退出', '这将同时彻底关闭房间，其他房客将退出。', `<button class="btn" id="mp-act-idle">退出</button>`);
      actionsEl.querySelector('#mp-act-copy')?.addEventListener('click', () => {
        if (room) void navigator.clipboard.writeText(room).then(() => toast('已将邀请码复制到剪贴板', 'success'));
      });
      actionsEl.querySelector('#mp-act-idle')?.addEventListener('click', () => void onIdle());
      return;
    }
    if (state === 'guest-connecting' || state === 'guest-starting') {
      actionsEl.innerHTML = renderActionCard('连', '#4C8BF5', '正在加入房间', info.difficulty_hint || '正在与房主建立连接。', `<button class="btn" id="mp-act-idle">退出</button>`);
      actionsEl.querySelector('#mp-act-idle')?.addEventListener('click', () => void onIdle());
      return;
    }
    if (state === 'guest-ok') {
      actionsEl.innerHTML =
        renderActionCard('进', '#2E9B6B', '进入世界', '启动游戏后到多人游戏双击「陶瓦联机大厅」，或点这里直接进入。', `<button class="btn btn-primary" id="mp-act-enter">进入</button>`) +
        renderActionCard('返', '#888888', '退出', '这不会影响其他房客加入当前房间。', `<button class="btn" id="mp-act-idle">退出</button>`);
      actionsEl.querySelector('#mp-act-enter')?.addEventListener('click', () => void onEnterWorld());
      actionsEl.querySelector('#mp-act-idle')?.addEventListener('click', () => void onIdle());
      return;
    }
    if (state === 'exception' || state === 'fatal') {
      actionsEl.innerHTML =
        renderActionCard('!', '#D95568', '联机失败', info.error_hint || info.error || '请返回后重试，或检查网络。', `<button class="btn" id="mp-act-idle">返回</button>`) +
        renderActionCard('直', '#4C8BF5', '朋友是公网就直连', '让他把单人世界对局域网开放，并在路由映射该端口，然后填他的公网 IP:端口。', `<button class="btn" id="mp-act-direct">直连</button>`) +
        renderActionCard('启', '#4C8BF5', '重新启动内核', '若内核已退出，点此重新拉起。', `<button class="btn btn-primary" id="mp-act-prepare">重启</button>`);
      actionsEl.querySelector('#mp-act-idle')?.addEventListener('click', () => void onIdle());
      actionsEl.querySelector('#mp-act-direct')?.addEventListener('click', () => void onDirect());
      actionsEl.querySelector('#mp-act-prepare')?.addEventListener('click', () => void prepare());
      return;
    }
    actionsEl.innerHTML = renderActionCard('…', '#888888', '请稍候', label || '正在准备联机内核。', `<button class="btn" id="mp-act-refresh">刷新</button>`);
    actionsEl.querySelector('#mp-act-refresh')?.addEventListener('click', () => void reload());
  }

  async function onHost() {
    const info = await snapshot();
    if (!info.game_running) {
      const ok = await confirmGameRunning();
      if (!ok) return;
    }
    try {
      await bridge.call('terracotta_host');
      toast('正在创建房间…', 'info');
    } catch (error) {
      toast(errorMessage(error, '创建房间失败'), 'error');
    }
    void reload();
  }

  async function onJoin() {
    const room = await inputDialog('我想当房客', '请输入房主提供的邀请码', '');
    if (!room?.trim()) return;
    try {
      await bridge.call('terracotta_join', { room: room.trim() });
      toast('正在加入房间…', 'info');
    } catch (error) {
      toast(errorMessage(error, '邀请码错误'), 'error');
    }
    void reload();
  }

  async function onIdle() {
    try {
      await bridge.call('terracotta_idle');
      toast('已退出当前联机状态', 'info');
    } catch (error) {
      toast(errorMessage(error, '返回失败'), 'error');
    }
    void reload();
  }

  async function onEnterWorld() {
    try {
      const result = await bridge.call<string>('terracotta_enter_world');
      if (typeof result === 'string' && result.startsWith('task-')) {
        toast('正在启动游戏，启动后会直接进入陶瓦联机大厅', 'success');
        await flyToTasks(actionsEl, '进', '#2E9B6B');
        router.navigate('tasks');
      } else {
        toast(result || '请到多人游戏双击「陶瓦联机大厅」。', 'success');
      }
    } catch (error) {
      toast(errorMessage(error, '进入世界失败'), 'error');
    }
  }

  async function onDirect() {
    const address = await inputDialog('公网直连', '请输入房主的公网地址，例如 1.2.3.4:25565', '');
    if (!address?.trim()) return;
    try {
      const result = await bridge.call<string>('terracotta_direct_connect', { address: address.trim() });
      if (typeof result === 'string' && result.startsWith('task-')) {
        toast('正在直连，启动后会进入该服务器', 'success');
        await flyToTasks(actionsEl, '直', '#4C8BF5');
        router.navigate('tasks');
      } else {
        toast(result || '启动后会进入该服务器。', 'success');
      }
    } catch (error) {
      toast(errorMessage(error, '直连失败'), 'error');
    }
  }

  async function confirmGameRunning(): Promise<boolean> {
    return confirmDialog('您似乎忘记启动游戏了', '请先启动游戏，进入单人世界，按 ESC，选择对局域网开放。');
  }

  let uiState = '';

  async function renderSnapshot(info: TerracottaSnapshot) {
    let state = info.state || 'missing';
    if (busy && (state === 'missing' || state === 'idle')) state = 'installing';
    else if (state === 'waiting' || state === 'host-ok' || state === 'guest-ok' || (state === 'idle' && !busy)) busy = false;

    const pillText = (state === 'exception' || state === 'fatal') ? '加入失败' : (info.label || state);
    statePill.textContent = pillText;
    statePill.style.background = STATE_COLORS[state] || '#888888';

    let statusText = info.error || info.label || '';
    if (info.error_hint) statusText = `${info.error || ''}\n${info.error_hint}`;
    else if (info.difficulty_hint) statusText = `${info.label || ''}\n${info.difficulty_hint}`;
    statusEl.textContent = statusText;

    const room = (info.room || '').trim();
    const url = (info.url || '').trim();
    if (room || url) {
      roomCard.style.display = '';
      roomEl.textContent = room || '陶瓦联机大厅';
      urlHint.textContent = url
        ? '请启动游戏，选择多人游戏，双击进入陶瓦联机大厅。'
        : '请提醒好友在联机页选择「我想当房客」，并输入该邀请码。';
    } else {
      roomCard.style.display = 'none';
    }

    if (state !== uiState) {
      uiState = state;
      fillActions(state, info, room);
    }

    const profiles = info.profiles || [];
    if (profiles.length) {
      playersCard.style.display = '';
      playersEl.innerHTML = profiles.map((p) => {
        const kind = String(p.kind || '').toUpperCase() === 'HOST' ? '房主' : '成员';
        return `
          <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--border-light)">
            <div>
              <div style="font-weight:600">${escapeHtml(p.name || '玩家')}</div>
              <div style="font-size:12px;color:var(--text-secondary)">${escapeHtml(p.vendor || kind)}</div>
            </div>
            <span style="font-size:12px;padding:2px 8px;border-radius:999px;background:var(--accent-soft, rgba(76,139,245,.15));color:var(--accent,#4C8BF5)">${escapeHtml(kind)}</span>
          </div>
        `;
      }).join('');
    } else {
      playersCard.style.display = 'none';
      playersEl.innerHTML = '';
    }

    if (room && state === 'host-ok' && lastState !== 'host-ok') {
      void navigator.clipboard.writeText(room).then(() => toast('已将邀请码复制到剪贴板', 'success'));
    }
    lastState = state;

    if (!autoStarted && !busy && info.supported && info.installed && !info.running) {
      autoStarted = true;
      void prepare();
    }
  }

  async function reload() {
    const info = await snapshot();
    if (container.isConnected) await renderSnapshot(info);
  }

  void reload();
  pollTimer = setInterval(() => { void reload(); }, 1200);

  registerPageCleanup(() => {
    if (pollTimer !== undefined) clearInterval(pollTimer);
  });
}
