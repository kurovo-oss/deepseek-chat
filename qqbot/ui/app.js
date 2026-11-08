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

  const onboarding = {
    open: false,
    step: 1,
    data: null,
    tested: false,
    testDetail: '',
    testOk: false,
    busy: false,
    poll: null,
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

  // ---------------- 首次启动引导 ----------------
  function onbButton(label, kind, handler) {
    const button = el('button', 'onb-btn' + (kind ? ` ${kind}` : ''), label);
    button.type = 'button';
    button.addEventListener('click', handler);
    return button;
  }

  function onbStatus(title, detail, kind = '', badge = '') {
    const row = el('div', 'onb-status');
    const main = el('div', 'onb-status-main');
    main.append(el('b', '', title));
    if (detail) main.append(el('small', '', detail));
    row.append(main);
    if (badge) row.append(el('em', kind, badge));
    return row;
  }

  function stopOnboardingPoll() {
    if (onboarding.poll) {
      clearInterval(onboarding.poll);
      onboarding.poll = null;
    }
  }

  function startOnboardingPoll() {
    if (onboarding.poll) return;
    onboarding.poll = setInterval(() => {
      if (!document.hidden && onboarding.open && onboarding.step === 2) refreshOnboarding();
    }, 4000);
  }

  function hideOnboarding() {
    onboarding.open = false;
    stopOnboardingPoll();
    $('#onboarding').hidden = true;
  }

  async function refreshOnboarding() {
    if (!onboarding.open) return;
    try {
      const data = await api('/api/onboarding');
      onboarding.data = data;
      if (!data.needed) {
        if (!data.finished && onboarding.step === 2) {
          onboarding.step = 3;
          renderOnboarding();
        } else {
          hideOnboarding();
        }
        return;
      }
      if (onboarding.step === 2) renderOnboarding();
    } catch (_) {
      // 轮询失败不弹错，下一次再试。
    }
  }

  async function loadOnboarding() {
    try {
      const data = await api('/api/onboarding');
      onboarding.data = data;
      if (!data.needed) {
        hideOnboarding();
        return;
      }
      if (!onboarding.open) {
        onboarding.open = true;
        onboarding.step = 1;
        onboarding.tested = false;
        onboarding.testDetail = '';
        onboarding.testOk = false;
      }
      $('#onboarding').hidden = false;
      renderOnboarding();
    } catch (_) {
      // 老版本后端没有这个接口时静默跳过，不影响主界面。
    }
  }

  function renderOnboarding() {
    if (!onboarding.open || !onboarding.data) return;
    const steps = $('#onboarding-steps');
    steps.replaceChildren();
    ['模型', 'QQ 登录', '开始使用'].forEach((label, index) => {
      const number = index + 1;
      const node = el('div', 'onb-step' + (number === onboarding.step ? ' active' : number < onboarding.step ? ' done' : ''));
      node.append(el('i', '', String(number)), el('span', '', label));
      steps.append(node);
    });

    const body = $('#onboarding-body');
    const actions = $('#onboarding-actions');
    body.replaceChildren();
    actions.replaceChildren();
    stopOnboardingPoll();

    if (onboarding.step === 1) renderOnboardingModel(body, actions);
    else if (onboarding.step === 2) renderOnboardingQQ(body, actions);
    else renderOnboardingDone(body, actions);
  }

  function renderOnboardingModel(body, actions) {
    const model = onboarding.data.model || {};
    body.append(onbStatus(
      '模型连接',
      `${model.model_id || '未填写模型'} · ${model.base_url || '未填写地址'}`,
      model.configured ? 'ok' : 'bad',
      model.configured ? '已配置' : '未配置',
    ));
    body.append(onbStatus(
      '主人 QQ',
      model.master_qq || '还没有填写主人 QQ',
      model.master_set ? 'ok' : 'bad',
      model.master_set ? '已填写' : '未填写',
    ));
    body.append(el('p', 'onb-note', model.key_set
      ? `API Key 已保存（${model.key_hint}），不会在页面里显示明文。`
      : '还没有保存 API Key，请先去配置页填写。'));
    body.append(el('div', 'onb-hint', '使用提醒：她通过第三方 NapCat 连接 QQ，自动化操作有账号被限制或封禁的风险，建议用小号；API Key 目前以明文保存在本机 .env，请单独申请并设置额度。'));
    body.append(el('p', 'onb-lead', '先点一次测试。只有真的调通模型，她才能开口说话。'));

    const result = el('p', `onb-result ${onboarding.testOk ? 'ok' : onboarding.testDetail ? 'bad' : ''}`.trim());
    result.textContent = onboarding.testDetail || '还没有测试。';
    body.append(result);

    const test = onbButton(onboarding.busy ? '测试中…' : '测试模型连接', '', async () => {
      if (onboarding.busy) return;
      onboarding.busy = true;
      onboarding.testOk = false;
      onboarding.testDetail = '正在请求模型…';
      renderOnboarding();
      try {
        const data = await api('/api/onboarding/test-model', { method: 'POST', body: {} });
        onboarding.testOk = !!data.ok;
        onboarding.testDetail = data.ok
          ? `连通，${data.latency_ms}ms · ${data.model_id}`
          : `失败：${data.detail || '模型没有返回'}`;
      } catch (err) {
        onboarding.testOk = false;
        onboarding.testDetail = `失败：${err.message}`;
      } finally {
        onboarding.busy = false;
        renderOnboarding();
      }
    });
    test.disabled = onboarding.busy;
    actions.append(test);

    const config = onbButton('去配置页', 'ghost', () => {
      hideOnboarding();
      switchPage('config');
    });
    actions.append(config);

    const next = onbButton('下一步：登录 QQ', 'primary', () => {
      onboarding.step = 2;
      renderOnboarding();
    });
    next.disabled = onboarding.busy || !onboarding.testOk || !model.master_set;
    actions.append(next);
  }

  function renderOnboardingQQ(body, actions) {
    const adapter = onboarding.data.adapter || {};
    const napcat = onboarding.data.napcat || {};
    body.append(onbStatus(
      'QQ 登录',
      adapter.online
        ? `${adapter.nickname || '已登录'}${adapter.user_id ? ` · ${adapter.user_id}` : ''}`
        : napcat.running ? 'NapCat 在运行，但还没有登录 QQ' : 'NapCat 还没有启动',
      adapter.online ? 'ok' : 'bad',
      adapter.online ? '在线' : '离线',
    ));
    body.append(el('p', 'onb-lead', adapter.online
      ? 'QQ 已经在线了，点下一步就能进控制台。'
      : '先启动 NapCat，再打开扫码页。用手机 QQ 扫码后回到这里，状态会自动刷新。'));
    if (!napcat.found) {
      body.append(el('p', 'onb-note', '没有找到 NapCat。请确认安装包完整，或手动运行 tools\\NapCat-*\\launcher.bat。'));
    } else if (!napcat.login_page_available) {
      body.append(el('p', 'onb-note', 'NapCat 还没有生成登录 token；等它启动完成后重试，或直接打开 http://127.0.0.1:6099/webui。'));
    }

    const start = onbButton(napcat.running ? 'NapCat 已启动' : '启动 NapCat', '', async () => {
      if (onboarding.busy) return;
      onboarding.busy = true;
      renderOnboarding();
      try {
        await api('/api/onboarding/start-napcat', { method: 'POST', body: {} });
        toast('NapCat 正在启动，稍等几秒');
        setTimeout(refreshOnboarding, 2500);
      } catch (err) {
        toast(err.message, true);
      } finally {
        onboarding.busy = false;
        renderOnboarding();
      }
    });
    start.disabled = onboarding.busy || !!napcat.running;
    actions.append(start);

    const open = onbButton('打开扫码登录页', 'primary', async () => {
      try {
        await api('/api/onboarding/open-napcat', { method: 'POST', body: {} });
        toast('已打开浏览器里的 NapCat 登录页');
      } catch (err) {
        toast(err.message, true);
      }
    });
    open.disabled = !napcat.login_page_available;
    actions.append(open);

    const check = onbButton('我已登录，重新检查', '', () => refreshOnboarding());
    check.disabled = onboarding.busy;
    actions.append(check);

    const back = onbButton('上一步', 'ghost', () => {
      onboarding.step = 1;
      renderOnboarding();
    });
    actions.append(back);

    const next = onbButton('下一步：开始使用', 'primary', () => {
      onboarding.step = 3;
      renderOnboarding();
    });
    next.disabled = !adapter.online || onboarding.busy;
    actions.append(next);

    if (!adapter.online) startOnboardingPoll();
  }

  function renderOnboardingDone(body, actions) {
    const model = onboarding.data.model || {};
    const adapter = onboarding.data.adapter || {};
    body.append(onbStatus('模型连接', `${model.model_id || '—'} · 已测试`, 'ok', '可用'));
    body.append(onbStatus('QQ 登录', `${adapter.nickname || '已登录'}${adapter.user_id ? ` · ${adapter.user_id}` : ''}`, 'ok', '在线'));
    body.append(el('p', 'onb-lead', '两件事都连上了。以后她会在你说话时回你，也会在自己想说话的时候先开口。'));
    body.append(el('div', 'onb-hint', '如果之后要换模型、改主人 QQ，去“配置”页保存，然后重启一下机器人。'));

    const finish = onbButton('进入控制台', 'primary', async () => {
      try {
        await api('/api/onboarding/finish', { method: 'POST', body: {} });
      } catch (_) {
        // 标记失败不挡人，下一次打开最多再看到一次引导。
      }
      hideOnboarding();
      toast('都准备好了');
      loadStatus(true);
    });
    actions.append(finish);
  }

  function finishOnboardingLater() {
    hideOnboarding();
    toast('先跳过，下次打开还会提醒');
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
    $('#onboarding-later').addEventListener('click', finishOnboardingLater);
    $('#persona-text').addEventListener('input', (event) => {
      $('#persona-count').textContent = `${event.target.value.length} 字`;
    });

    tickClock();
    setInterval(tickClock, 1000);
    const page = (location.hash || '#overview').slice(1);
    switchPage(PAGE_TITLE[page] ? page : 'overview');
    loadStatus();
    loadOnboarding();
    state.timer = setInterval(() => loadStatus(true), 20000);
  }

  document.addEventListener('DOMContentLoaded', init);
})();
