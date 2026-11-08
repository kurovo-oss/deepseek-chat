(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    status: null,
    persona: null,
    settings: null,
    page: 'overview',
    timer: null,
  };

  const PAGE_TITLE = {
    overview: ['今天也在这里', '总览'],
    chat: ['', '对话'],
    persona: ['她是谁，由你写', '人格'],
    config: ['把连接和边界调好', '配置'],
    logs: ['她都做了什么', '日志'],
  };

  const ORIGIN_LABEL = {
    reply: '回复',
    proactive: '主动',
    qzone: '空间',
    douyin: '抖音',
    douyin_share: '分享',
    watch: '陪玩',
    draw: '画画',
    voice: '语音',
  };

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function fmtTime(ts, withDate = false) {
    if (!ts) return '—';
    const d = new Date(ts * 1000);
    const opt = withDate
      ? { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }
      : { hour: '2-digit', minute: '2-digit' };
    return d.toLocaleString('zh-CN', opt);
  }

  function greeting() {
    const h = new Date().getHours();
    if (h < 5) return '凌晨好';
    if (h < 11) return '早上好';
    if (h < 14) return '中午好';
    if (h < 18) return '下午好';
    return '晚上好';
  }

  let toastTimer = null;
  function toast(message, bad = false) {
    const node = $('#toast');
    node.textContent = message;
    node.className = 'toast show' + (bad ? ' bad' : '');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { node.className = 'toast'; }, 3200);
  }

  async function api(path, options = {}) {
    const init = { credentials: 'same-origin', ...options };
    if (init.body && typeof init.body !== 'string') {
      init.body = JSON.stringify(init.body);
      init.headers = { 'Content-Type': 'application/json', ...(init.headers || {}) };
    }
    const res = await fetch(path, init);
    if (res.status === 401) {
      location.href = '/';
      throw new Error('未登录');
    }
    let data = null;
    try { data = await res.json(); } catch (_) { data = null; }
    if (!res.ok) throw new Error((data && data.error) || `请求失败（${res.status}）`);
    return data;
  }

  function setDot(node, kind) {
    if (!node) return;
    node.className = 'dot' + (kind ? ' ' + kind : '');
  }

  function switchPage(page) {
    state.page = page;
    $$('.nav-item').forEach((btn) => btn.classList.toggle('active', btn.dataset.page === page));
    $$('.page').forEach((node) => node.classList.toggle('active', node.id === `page-${page}`));
    const [title, crumb] = PAGE_TITLE[page] || PAGE_TITLE.overview;
    $('#page-title').textContent = title;
    $('#crumb').textContent = crumb;
    if (location.hash !== `#${page}`) history.replaceState(null, '', `#${page}`);
    if (page === 'chat') loadChat();
    if (page === 'persona') loadPersona();
    if (page === 'config') loadSettings();
    if (page === 'logs') loadLogs();
  }

  function renderStatus(data) {
    state.status = data;
    const alarm = data.runtime.emergency;
    const badAlert = (data.alerts || []).some((a) => a.level === 'bad');
    const kind = alarm || badAlert ? 'bad' : 'ok';
    setDot($('#side-dot'), kind);
    setDot($('#hero-dot'), kind);
    $('#side-state').textContent = alarm ? '已停机' : badAlert ? '有异常' : '运行中';
    $('#hero-state').textContent = alarm ? '已停机' : '在线';
    $('#side-model').textContent = `模型 ${data.model || '—'}`;
    $('#hero-qq').textContent = data.master_qq || '—';
    $('#greeting').textContent = greeting();

    const values = (data.mood && data.mood.values) || {};
    const moodBars = $('#mood-bars');
    moodBars.replaceChildren();
    const moods = [
      ['miss', '想念', 'mood-row'],
      ['warmth', '亲近', 'mood-row warm'],
      ['tired', '疲惫', 'mood-row tired'],
    ];
    moods.forEach(([key, label, cls]) => {
      const row = el('div', cls);
      row.append(el('span', '', label));
      const track = el('div', 'mood-track');
      const fill = el('div', 'mood-fill');
      const value = Math.max(0, Math.min(1, Number(values[key]) || 0));
      fill.style.width = `${Math.round(value * 100)}%`;
      track.append(fill);
      row.append(track, el('b', 'mood-val', value.toFixed(2)));
      moodBars.append(row);
    });
    const tag = values.tired >= 0.7 ? '有点累' : values.miss >= 0.75 ? '想他' : values.warmth >= 0.6 ? '暖' : '平稳';
    $('#mood-tag').textContent = tag;

    const today = data.today || {};
    $('#stat-messages').textContent = today.messages ?? 0;
    $('#stat-user').textContent = today.user ?? 0;
    $('#stat-bot').textContent = today.bot ?? 0;
    $('#stat-proactive').textContent = today.proactive ?? 0;
    $('#quota-proactive').textContent = `${today.proactive ?? 0} / ${today.proactive_max ?? 0}`;
    const guard = data.guard || {};
    const q = guard.qzone || {};
    const dy = guard.douyin || {};
    $('#quota-other').textContent = `空间 ${q.used_today ?? 0}/${q.daily_quota ?? 0} · 抖音 ${dy.used_today ?? 0}/${dy.daily_quota ?? 0}`;

    const alerts = data.alerts || [];
    $('#alert-count').textContent = String(alerts.length);
    const alertList = $('#alert-list');
    alertList.replaceChildren();
    if (!alerts.length) {
      alertList.append(el('p', 'muted small', '暂时没有异常。'));
    } else {
      alerts.forEach((item) => {
        const row = el('div', 'alert' + (item.level === 'bad' ? ' bad' : ''));
        row.append(el('i'));
        const body = el('div');
        body.append(el('b', '', item.title), el('span', '', item.detail));
        row.append(body);
        alertList.append(row);
      });
    }

    const recent = $('#recent-list');
    recent.replaceChildren();
    if (!(data.messages || []).length) {
      recent.append(el('p', 'muted small', '还没有消息。'));
    } else {
      data.messages.slice(-6).forEach((msg) => {
        const bot = msg.role === 'assistant';
        const row = el('div', 'mini-msg' + (bot ? ' bot' : ''));
        row.append(el('i'));
        const body = el('div');
        body.append(el('span', 'who', `${bot ? '她' : '你'} · ${ORIGIN_LABEL[msg.origin] || msg.origin || '回复'}`));
        body.append(el('p', '', msg.content || ''));
        row.append(body, el('time', '', fmtTime(msg.created_at)));
        recent.append(row);
      });
    }

    const runtime = data.runtime || { paused: {} };
    $$('#page-overview .control-row input[data-module]').forEach((box) => {
      box.checked = !(runtime.paused && runtime.paused[box.dataset.module]);
      box.disabled = runtime.emergency;
    });
    $('#runtime-tag').textContent = runtime.emergency ? '已停机' : '运行中';
    const emergency = $('#emergency');
    emergency.textContent = runtime.emergency ? '恢复运行' : '一键停机';
    emergency.classList.toggle('on', !!runtime.emergency);
  }

  async function loadStatus(silent = false) {
    try {
      const data = await api('/api/status');
      renderStatus(data);
    } catch (err) {
      setDot($('#side-dot'), 'bad');
      $('#side-state').textContent = '连接失败';
      if (!silent) toast(err.message, true);
    }
  }

  async function loadChat() {
    const list = $('#chat-list');
    list.replaceChildren(el('p', 'muted', '加载中……'));
    try {
      const data = await api('/api/messages?limit=60');
      list.replaceChildren();
      const rows = data.messages || [];
      if (!rows.length) {
        list.append(el('p', 'muted', '还没有消息。'));
        return;
      }
      rows.forEach((msg) => {
        const bot = msg.role === 'assistant';
        const row = el('div', 'chat-msg' + (bot ? ' bot' : ''));
        row.append(el('div', 'chat-avatar', bot ? '鲸' : '你'));
        const body = el('div', 'chat-body');
        const meta = el('div', 'chat-meta');
        meta.append(el('b', '', bot ? '大肥鱼' : '你'));
        meta.append(el('span', '', ORIGIN_LABEL[msg.origin] || msg.origin || '回复'));
        meta.append(el('time', '', fmtTime(msg.created_at, true)));
        body.append(meta, el('p', '', msg.content || ''));
        row.append(body);
        list.append(row);
      });
      list.scrollTop = list.scrollHeight;
    } catch (err) {
      list.replaceChildren(el('p', 'muted', err.message));
    }
  }

  function renderVersions(versions) {
    const list = $('#persona-versions');
    list.replaceChildren();
    if (!versions || !versions.length) {
      list.append(el('p', 'muted small', '还没有版本。'));
      return;
    }
    versions.forEach((item) => {
      const row = el('div', 'version');
      const body = el('div');
      body.append(el('b', '', fmtTime(item.at, true)));
      body.append(el('small', '', `${item.chars || 0} 字 · ${item.note || '无备注'}`));
      row.append(body, el('span', 'tag', '历史'));
      list.append(row);
    });
  }

  async function loadPersona() {
    try {
      const data = await api('/api/persona');
      state.persona = data;
      $('#persona-text').value = data.text || '';
      $('#persona-count').textContent = `${data.chars || 0} 字`;
      $('#persona-updated').textContent = data.updated_at ? fmtTime(data.updated_at, true) : '—';
      $('#persona-warnings').textContent = (data.warnings && data.warnings.length)
        ? `提醒：${data.warnings.join('；')}`
        : '边界词齐全。';
      renderVersions(data.versions);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function savePersona() {
    const button = $('#save-persona');
    button.disabled = true;
    button.textContent = '发布中…';
    try {
      const data = await api('/api/persona', {
        method: 'PUT',
        body: {
          text: $('#persona-text').value,
          note: $('#persona-note').value,
        },
      });
      $('#persona-note').value = '';
      toast(`已发布 ${data.version}，下一条消息生效`);
      await loadPersona();
      await loadStatus(true);
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '发布';
    }
  }

  function fillSettings(data) {
    state.settings = data;
    const model = data.model || {};
    const behavior = data.behavior || {};
    $('#master-qq').value = data.master_qq || '';
    $('#model-base').value = model.base_url || '';
    $('#model-id').value = model.id || '';
    $('#request-timeout').value = model.timeout || 60;
    $('#model-key').value = '';
    $('#key-hint').textContent = model.api_key_set ? `已设置（${model.api_key_hint}）` : '未设置';
    $('#proactive-enabled').checked = !!behavior.proactive_enabled;
    $('#proactive-max').value = behavior.proactive_max_per_day ?? 5;
    $('#qzone-enabled').checked = !!behavior.qzone_enabled;
    $('#qzone-dry').checked = !!behavior.qzone_dry_run;
    $('#qzone-max').value = behavior.qzone_daily_max ?? 1;
    $('#douyin-enabled').checked = !!behavior.douyin_enabled;
    $('#douyin-dry').checked = !!behavior.douyin_dry_run;
    $('#douyin-max').value = behavior.douyin_daily_max ?? 5;
    $('#douyin-share-max').value = behavior.douyin_share_daily_max ?? 2;
  }

  async function loadSettings() {
    try {
      fillSettings(await api('/api/settings'));
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function saveSettings() {
    const button = $('#save-settings');
    button.disabled = true;
    button.textContent = '保存中…';
    const payload = {
      master_qq: $('#master-qq').value.trim(),
      model_base_url: $('#model-base').value.trim(),
      model_id: $('#model-id').value.trim(),
      request_timeout: Number($('#request-timeout').value),
      proactive_enabled: $('#proactive-enabled').checked,
      proactive_max_per_day: Number($('#proactive-max').value),
      qzone_enabled: $('#qzone-enabled').checked,
      qzone_dry_run: $('#qzone-dry').checked,
      qzone_daily_max: Number($('#qzone-max').value),
      douyin_enabled: $('#douyin-enabled').checked,
      douyin_dry_run: $('#douyin-dry').checked,
      douyin_daily_max: Number($('#douyin-max').value),
      douyin_share_daily_max: Number($('#douyin-share-max').value),
    };
    const key = $('#model-key').value.trim();
    if (key) payload.model_api_key = key;
    try {
      const data = await api('/api/settings', { method: 'PUT', body: payload });
      $('#model-key').value = '';
      toast(`已写入 .env（${data.saved.length} 项），重启机器人生效`);
      await loadSettings();
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '保存配置';
    }
  }

  async function loadLogs() {
    const box = $('#log-lines');
    box.textContent = '加载中……';
    const audit = $('#audit-list');
    audit.replaceChildren(el('p', 'muted small', '加载中……'));
    try {
      const [logs, records] = await Promise.all([api('/api/logs?limit=200'), api('/api/audit')]);
      box.textContent = (logs.lines || []).join('') || '（还没有日志）';
      box.scrollTop = box.scrollHeight;
      audit.replaceChildren();
      const items = records.items || [];
      if (!items.length) {
        audit.append(el('p', 'muted small', '还没有审计记录。'));
        return;
      }
      items.forEach((item) => {
        const row = el('div', 'audit');
        row.append(el('time', '', fmtTime(item.at, true)));
        const body = el('div');
        body.append(el('b', '', `${item.module || '—'} · ${item.action || '—'}`));
        body.append(el('small', '', item.target || item.error || '—'));
        row.append(body, el('span', `res ${item.result || ''}`, item.result || '—'));
        audit.append(row);
      });
    } catch (err) {
      box.textContent = err.message;
      audit.replaceChildren(el('p', 'muted small', err.message));
    }
  }

  async function toggleModule(box) {
    const module = box.dataset.module;
    const paused = !box.checked;
    try {
      const data = await api('/api/runtime', { method: 'PUT', body: { module, paused } });
      if (data.emergency && !paused) {
        toast('当前是停机状态，先点“恢复运行”', true);
      } else {
        toast(`${module === 'proactive' ? '主动消息' : module === 'qzone' ? 'QQ 空间' : '抖音'}${paused ? '已暂停' : '已恢复'}`);
      }
      await loadStatus(true);
    } catch (err) {
      box.checked = !box.checked;
      toast(err.message, true);
    }
  }

  async function toggleEmergency() {
    const on = !(state.status && state.status.runtime && state.status.runtime.emergency);
    try {
      await api('/api/runtime', { method: 'PUT', body: { emergency: on } });
      toast(on ? '已停机：她不再回复，也不主动做事' : '已恢复运行');
      await loadStatus(true);
    } catch (err) {
      toast(err.message, true);
    }
  }

  function tickClock() {
    $('#clock').textContent = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
  }

  function init() {
    $$('.nav-item').forEach((btn) => btn.addEventListener('click', () => switchPage(btn.dataset.page)));
    $$('[data-page-link]').forEach((btn) => btn.addEventListener('click', () => switchPage(btn.dataset.pageLink)));
    $$('#page-overview .control-row input[data-module]').forEach((box) => {
      box.addEventListener('change', () => toggleModule(box));
    });
    $('#emergency').addEventListener('click', toggleEmergency);
    $('#refresh').addEventListener('click', () => {
      loadStatus();
      if (state.page === 'chat') loadChat();
      if (state.page === 'persona') loadPersona();
      if (state.page === 'config') loadSettings();
      if (state.page === 'logs') loadLogs();
    });
    $('#reload-chat').addEventListener('click', loadChat);
    $('#reload-logs').addEventListener('click', loadLogs);
    $('#save-persona').addEventListener('click', savePersona);
    $('#save-settings').addEventListener('click', saveSettings);
    $('#persona-text').addEventListener('input', (event) => {
      $('#persona-count').textContent = `${event.target.value.length} 字`;
    });

    tickClock();
    setInterval(tickClock, 1000);
    const page = (location.hash || '#overview').slice(1);
    switchPage(PAGE_TITLE[page] ? page : 'overview');
    loadStatus();
    state.timer = setInterval(() => loadStatus(true), 20000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
