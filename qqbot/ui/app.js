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
    overview: ['她今天怎么样', '首页'],
    chat: ['', '对话'],
    memory: ['她记住的事', '记忆'],
    goals: ['她想做的事', '计划'],
    persona: ['她是谁，由你写', '人格'],
    config: ['把连接和边界调好', '配置'],
    logs: ['她都做了什么', '日志'],
    diagnostics: ['她哪儿不舒服', '诊断'],
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

  function fmtWhen(ts) {
    if (!ts) return '还没有';
    const d = new Date(ts * 1000);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    const yesterday = new Date(now);
    yesterday.setDate(now.getDate() - 1);
    const time = d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    if (sameDay) return `今天 ${time}`;
    if (d.toDateString() === yesterday.toDateString()) return `昨天 ${time}`;
    return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' }) + ` ${time}`;
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
    if (page === 'memory') {
      loadMemory();
      loadStyleSuggestions();
    }
    if (page === 'goals') loadGoals();
    if (page === 'persona') loadPersona();
    if (page === 'config') loadSettings();
    if (page === 'logs') loadLogs();
    if (page === 'diagnostics') loadDiagnostics();
  }

  function renderStatus(data) {
    state.status = data;
    const alarm = data.runtime.emergency;
    const badAlert = (data.alerts || []).some((a) => a.level === 'bad');
    const kind = alarm || badAlert ? 'bad' : 'ok';
    const adapter = data.adapter || {};
    setDot($('#side-dot'), kind);
    setDot($('#hero-dot'), kind);
    setDot($('#adapter-dot'), adapter.online ? 'ok' : 'bad');
    $('#side-state').textContent = alarm ? '已停机' : badAlert ? '有异常' : '运行中';
    $('#hero-state').textContent = alarm ? '已停机' : '运行中';
    $('#adapter-state').textContent = adapter.online
      ? (adapter.nickname ? `${adapter.nickname} 在线` : 'QQ 在线')
      : 'QQ 未登录';
    $('#side-model').textContent = `QQ ${adapter.online ? '在线' : '离线'} · 模型 ${data.model || '—'}`;
    $('#hero-qq').textContent = data.master_qq || '—';
    $('#last-bot').textContent = fmtWhen(data.last && data.last.bot_at);
    $('#last-user').textContent = fmtWhen(data.last && data.last.user_at);
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
    const proactive = data.proactive || {};
    $$('#page-overview input[data-module]').forEach((box) => {
      box.checked = !(runtime.paused && runtime.paused[box.dataset.module]);
      box.disabled = runtime.emergency;
    });
    $$('.mute-today').forEach((button) => {
      const muted = !!proactive.muted_today;
      button.classList.toggle('on', muted);
      button.textContent = muted ? '恢复今天主动' : '今天别找我了';
      button.disabled = !!runtime.emergency;
    });
    const muteState = $('#proactive-mute-state');
    if (muteState) {
      muteState.textContent = proactive.muted_today
        ? `今天已静默，到 ${fmtTime(proactive.muted_until || 0, true)} 恢复。`
        : '今天还没有静默。';
    }
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
        if (bot) body.append(feedbackBar(msg));
        row.append(body);
        list.append(row);
      });
      list.scrollTop = list.scrollHeight;
    } catch (err) {
      list.replaceChildren(el('p', 'muted', err.message));
    }
  }

  function feedbackBar(msg) {
    const bar = el('div', 'chat-feedback');
    const good = el('button', '', '合拍');
    const bad = el('button', '', '不准确');
    good.type = 'button';
    bad.type = 'button';
    good.addEventListener('click', () => sendFeedback(msg, 'good', bar));
    bad.addEventListener('click', () => sendFeedback(msg, 'bad', bar));
    bar.append(good, bad);
    return bar;
  }

  async function sendFeedback(msg, rating, bar) {
    let note = '';
    if (rating === 'bad') {
      const answer = window.prompt(
        '哪里不合适？写成她该记住的偏好，比如：回复别太长、别把测试内容当成记忆。',
        '',
      );
      if (answer === null) return;
      note = answer.trim();
    }
    $$('button', bar).forEach((button) => { button.disabled = true; });
    try {
      const data = await api('/api/feedback', {
        method: 'POST',
        body: { message_id: msg.id, rating, note },
      });
      bar.replaceChildren(el('span', 'chat-feedback-done', data.card_id ? '已记下偏好' : '已收到'));
      toast(data.card_id ? '记下了，她以后会注意' : '收到，这条反馈已记录');
    } catch (err) {
      $$('button', bar).forEach((button) => { button.disabled = false; });
      toast(err.message, true);
    }
  }

  const GOAL_KIND = {
    relationship: '关系',
    self: '她自己',
    curiosity: '好奇',
    routine: '日常',
    shared: '一起',
  };
  const GOAL_STATUS = {
    candidate: '候选',
    active: '进行中',
    paused: '暂停',
    done: '完成',
    dropped: '已删除',
  };

  function goalKind(kind) {
    return GOAL_KIND[kind] || kind || '目标';
  }

  function goalStatus(status) {
    return GOAL_STATUS[status] || status || '候选';
  }

  function goalActionButton(text, cls, handler) {
    const button = el('button', cls, text);
    button.type = 'button';
    button.addEventListener('click', handler);
    return button;
  }

  function renderGoal(item, pendingAction) {
    const card = el('article', `goal-card ${item.status || 'candidate'}`);
    const head = el('div', 'goal-card-head');
    head.append(el('span', 'memory-kind', goalKind(item.kind)));
    head.append(el('span', `goal-badge ${item.status || 'candidate'}`, goalStatus(item.status)));
    head.append(el('span', 'tag', `#${item.id}`));
    card.append(head);
    card.append(el('h3', 'goal-title', item.title || '（空目标）'));
    if (item.why) card.append(el('p', 'goal-why', item.why));
    const meta = el('div', 'memory-meta');
    meta.append(el('span', '', `重要度 ${Number(item.priority || 0).toFixed(2)}`));
    if ((item.evidence || []).length) {
      meta.append(el('span', '', `证据 ${item.evidence.map((id) => `#${id}`).join(' ')}`));
    }
    meta.append(el('span', '', item.updated_at ? `更新 ${fmtTime(item.updated_at, true)}` : ''));
    card.append(meta);
    if (pendingAction) {
      const preview = el('div', 'goal-preview');
      preview.append(el('span', 'goal-preview-label', '准备发给她的话'));
      preview.append(el('p', 'goal-preview-text', (pendingAction.payload || {}).text || '（空）'));
      const previewActions = el('div', 'memory-actions');
      previewActions.append(goalActionButton('确认发送', 'primary-btn', () => approveGoalAction(pendingAction)));
      previewActions.append(goalActionButton('取消', '', () => cancelGoalAction(pendingAction)));
      preview.append(previewActions);
      card.append(preview);
    }
    const actions = el('div', 'memory-actions');
    if (item.status === 'candidate') {
      actions.append(goalActionButton('确认', 'primary-btn', () => setGoalStatus(item, 'active')));
      actions.append(goalActionButton('先放着', '', () => setGoalStatus(item, 'paused')));
    } else if (item.status === 'active') {
      actions.append(goalActionButton('准备主动', 'primary-btn', () => prepareGoalAction(item)));
      actions.append(goalActionButton('暂停', '', () => setGoalStatus(item, 'paused')));
      actions.append(goalActionButton('完成', '', () => setGoalStatus(item, 'done')));
    } else if (item.status === 'paused') {
      actions.append(goalActionButton('恢复', 'primary-btn', () => setGoalStatus(item, 'active')));
    }
    if (item.status !== 'dropped') {
      actions.append(goalActionButton('删除', 'danger', () => setGoalStatus(item, 'dropped', true)));
    }
    if (actions.childNodes.length) card.append(actions);
    return card;
  }

  async function prepareGoalAction(item) {
    try {
      await api('/api/goals/action/prepare', { method: 'POST', body: { id: item.id } });
      toast('准备好一句话，确认后才会发送');
      await loadGoals();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function approveGoalAction(action) {
    try {
      await api('/api/goals/action/approve', { method: 'POST', body: { id: action.id } });
      toast('已经发出去了');
      await loadGoals();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function cancelGoalAction(action) {
    try {
      await api('/api/goals/action/cancel', { method: 'POST', body: { id: action.id } });
      toast('这次先不发了');
      await loadGoals();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function setGoalStatus(item, status, confirm = false) {
    if (confirm && !window.confirm(`删掉「${item.title}」这个目标？`)) return;
    try {
      await api('/api/goals/update', { method: 'POST', body: { id: item.id, status } });
      toast(`目标已${status === 'active' ? '确认' : status === 'paused' ? '暂停' : status === 'done' ? '完成' : '删除'}`);
      await loadGoals();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function loadGoals() {
    const list = $('#goal-list');
    if (!list) return;
    list.replaceChildren(el('p', 'muted', '加载中……'));
    try {
      const data = await api('/api/goals');
      const items = (data.items || []).filter((item) => item.status !== 'dropped');
      const counts = data.counts || {};
      const byGoal = new Map((data.actions || []).map((action) => [String(action.goal_id), action]));
      $('#goal-count').textContent = `${items.length} 个 / 候选 ${counts.candidate ?? 0}`;
      list.replaceChildren();
      if (!items.length) {
        list.append(el('p', 'muted', '她还没有想做的事。可以点右上角「让她想一次」，或者在记忆页写几条偏好。'));
        return;
      }
      items.forEach((item) => list.append(renderGoal(item, byGoal.get(String(item.id)))));
    } catch (err) {
      list.replaceChildren(el('p', 'muted', err.message));
    }
  }

  async function reviewGoals() {
    const button = $('#goal-review');
    button.disabled = true;
    button.textContent = '她在想…';
    try {
      const data = await api('/api/goals/review', { method: 'POST', body: {} });
      toast((data.created || []).length ? `她想到了 ${data.created.length} 个候选计划` : '这次没有值得写下来的计划');
      await loadGoals();
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '让她想一次';
    }
  }

  const MEMORY_KIND = {
    fact: '事实',
    preference: '喜好',
    event: '经历',
    relation: '关系',
    identity: '身份',
    plan: '计划',
    emotion: '情绪',
    other: '记忆',
  };

  function memoryKind(kind) {
    return MEMORY_KIND[kind] || kind || '记忆';
  }

  function renderMemoryItem(item) {
    const card = el('article', 'memory-card');
    const head = el('div', 'memory-card-head');
    head.append(el('span', 'memory-kind', memoryKind(item.kind)));
    if (item.pinned) head.append(el('span', 'memory-badge pin', '钉住'));
    if (!item.confirmed) head.append(el('span', 'memory-badge', '待确认'));
    head.append(el('span', 'tag', `#${item.id}`));
    card.append(head);
    card.append(el('p', 'memory-summary', item.summary || '（空）'));
    if (item.detail) card.append(el('p', 'memory-detail', item.detail));
    const meta = el('div', 'memory-meta');
    meta.append(el('span', '', `重要度 ${Number(item.importance || 0).toFixed(2)}`));
    meta.append(el('span', '', `被想起 ${item.hit_count || 0} 次`));
    meta.append(el('span', '', item.updated_at ? `更新 ${fmtTime(item.updated_at, true)}` : ''));
    card.append(meta);
    const actions = el('div', 'memory-actions');
    const edit = el('button', '', '修改');
    edit.type = 'button';
    edit.addEventListener('click', () => startMemoryEdit(card, item, 'edit'));
    const wrong = el('button', '', '你记错了');
    wrong.type = 'button';
    wrong.addEventListener('click', () => startMemoryEdit(card, item, 'correct'));
    const remove = el('button', 'danger', '删除');
    remove.type = 'button';
    remove.addEventListener('click', () => deleteMemory(item));
    actions.append(edit, wrong, remove);
    card.append(actions);
    return card;
  }

  function startMemoryEdit(card, item, mode) {
    card.replaceChildren();
    const editor = el('div', 'memory-editor');
    editor.append(el('p', 'memory-hint', mode === 'correct'
      ? '她记错了？写下正确的说法，旧的会被替换掉。'
      : '修改这条记忆，保存后下一条消息生效。'));
    const summary = el('textarea');
    summary.value = item.summary || '';
    summary.placeholder = mode === 'correct' ? '正确说法，比如：他其实不讨厌香菜' : '她记住的事';
    editor.append(summary);
    let detail = null;
    if (mode === 'edit') {
      detail = el('input');
      detail.value = item.detail || '';
      detail.placeholder = '备注（可选）';
      editor.append(detail);
    }
    const foot = el('div', 'memory-editor-foot');
    const save = el('button', 'primary-btn', mode === 'correct' ? '改成这个' : '保存');
    save.type = 'button';
    const cancel = el('button', 'ghost-btn', '取消');
    cancel.type = 'button';
    cancel.addEventListener('click', () => loadMemory());
    save.addEventListener('click', async () => {
      const text = summary.value.trim();
      if (!text) {
        toast('先写一条她该记住的事', true);
        return;
      }
      save.disabled = true;
      save.textContent = '保存中…';
      try {
        if (mode === 'correct') {
          await api('/api/memory/correct', { method: 'POST', body: { id: item.id, summary: text } });
          toast('改好了，她以后按新的说法记');
        } else {
          await api('/api/memory/update', {
            method: 'POST',
            body: { id: item.id, summary: text, detail: detail ? detail.value : undefined },
          });
          toast('记忆已更新');
        }
        await loadMemory();
      } catch (err) {
        toast(err.message, true);
      } finally {
        save.disabled = false;
      }
    });
    foot.append(save, cancel);
    editor.append(foot);
    card.append(editor);
    summary.focus();
  }

  async function addPreference() {
    const summary = window.prompt(
      '写一条她该记住的相处偏好，比如：先问我怎么了，不要急着给建议；回复别太长。',
      '',
    );
    if (summary === null || !summary.trim()) return;
    try {
      await api('/api/memory/preference', { method: 'POST', body: { summary: summary.trim() } });
      toast('偏好记下了，下一条消息生效');
      await loadMemory();
    } catch (err) {
      toast(err.message, true);
    }
  }

  function renderStyleSuggestion(item) {
    const card = el('article', 'style-suggestion');
    const head = el('div', 'style-suggestion-head');
    head.append(el('span', 'memory-kind', '相处方式建议'));
    head.append(el('span', 'tag', `#${item.id}`));
    card.append(head, el('p', 'style-suggestion-text', item.text || ''));
    const actions = el('div', 'memory-actions');
    actions.append(goalActionButton('采用', 'primary-btn', () => applyStyleSuggestion(item)));
    actions.append(goalActionButton('忽略', '', () => dismissStyleSuggestion(item)));
    card.append(actions);
    return card;
  }

  async function loadStyleSuggestions() {
    const box = $('#style-suggestions');
    if (!box) return;
    try {
      const data = await api('/api/styles');
      const items = data.items || [];
      box.replaceChildren();
      box.hidden = !items.length;
      if (!items.length) return;
      box.append(el('p', 'style-suggestion-title', '根据反馈整理的相处方式（采用后才会生效）'));
      items.forEach((item) => box.append(renderStyleSuggestion(item)));
    } catch (_) {
      box.hidden = true;
    }
  }

  async function reviewStyles() {
    const button = $('#style-review');
    button.disabled = true;
    button.textContent = '整理中…';
    try {
      const data = await api('/api/styles/review', { method: 'POST', body: {} });
      toast((data.created || []).length ? '整理出新的相处建议' : '这次没有新的建议');
      await loadStyleSuggestions();
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '整理相处方式';
    }
  }

  async function applyStyleSuggestion(item) {
    try {
      await api('/api/styles/apply', { method: 'POST', body: { id: item.id } });
      toast('已采用，下一条消息开始生效');
      await Promise.all([loadStyleSuggestions(), loadMemory()]);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function dismissStyleSuggestion(item) {
    try {
      await api('/api/styles/dismiss', { method: 'POST', body: { id: item.id } });
      toast('这条先不采用');
      await loadStyleSuggestions();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function deleteMemory(item) {
    if (!window.confirm(`删掉「${item.summary}」？她会真的忘记这件事。`)) return;
    try {
      await api('/api/memory/delete', { method: 'POST', body: { id: item.id } });
      toast('删掉了，这件事她不再提');
      await loadMemory();
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function loadMemory() {
    const list = $('#memory-list');
    list.replaceChildren(el('p', 'muted', '加载中……'));
    const query = ($('#memory-search').value || '').trim();
    try {
      const data = await api(`/api/memory?q=${encodeURIComponent(query)}`);
      const items = data.items || [];
      const counts = data.counts || {};
      $('#memory-count').textContent = `${items.length} 条 / 共 ${counts.active ?? 0} 条`;
      list.replaceChildren();
      if (!items.length) {
        list.append(el('p', 'muted', query ? '没有搜到这条记忆。' : '她还没记住什么，多聊聊就会有了。'));
        return;
      }
      items.forEach((item) => list.append(renderMemoryItem(item)));
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
      await loadPersonaTemplates();
    } catch (err) {
      toast(err.message, true);
    }
  }

  let previewTemplateId = '';
  let previewTemplateName = '';

  async function loadPersonaTemplates() {
    const list = $('#persona-templates');
    if (!list) return;
    try {
      const data = await api('/api/persona/templates');
      list.replaceChildren();
      (data.items || []).forEach((item) => {
        const card = el('div', 'template-item');
        card.append(el('b', '', item.name));
        card.append(el('p', '', item.description || ''));
        const actions = el('div', 'memory-actions');
        const preview = el('button', '', '预览');
        preview.type = 'button';
        preview.addEventListener('click', () => previewPersonaTemplate(item.id));
        const apply = el('button', '', '用这套');
        apply.type = 'button';
        apply.addEventListener('click', () => applyPersonaTemplate(item.id, item.name));
        actions.append(preview, apply);
        card.append(actions);
        list.append(card);
      });
    } catch (err) {
      list.replaceChildren(el('p', 'muted small', err.message));
    }
  }

  async function previewPersonaTemplate(templateId) {
    try {
      const data = await api('/api/persona/preview', {
        method: 'POST',
        body: { template_id: templateId },
      });
      previewTemplateId = templateId;
      previewTemplateName = data.name || templateId;
      const panel = $('#persona-preview');
      panel.hidden = false;
      $('#persona-preview-title').textContent = `预览 · ${data.name}`;
      $('#persona-preview-count').textContent = `${data.chars || 0} 字`;
      $('#persona-preview-text').textContent = data.text || '';
      panel.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function applyPersonaTemplate(templateId, name) {
    try {
      const data = await api('/api/persona/apply', {
        method: 'POST',
        body: { template_id: templateId },
      });
      toast(`已切换到「${name || data.template?.name || templateId}」，下一条消息生效`);
      $('#persona-preview').hidden = true;
      previewTemplateId = '';
      previewTemplateName = '';
      await loadPersona();
      await loadStatus(true);
    } catch (err) {
      toast(err.message, true);
    }
  }

  async function restorePersona() {
    if (!window.confirm('恢复到安装时的默认人格？当前版本会先留档。')) return;
    try {
      const data = await api('/api/persona/restore', { method: 'POST', body: {} });
      toast(`已恢复默认人格（${data.version}）`);
      $('#persona-preview').hidden = true;
      previewTemplateId = '';
      previewTemplateName = '';
      await loadPersona();
      await loadStatus(true);
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
    const maxPerDay = Math.max(0, Math.min(20, Number(behavior.proactive_max_per_day ?? 5)));
    $('#proactive-max').value = maxPerDay;
    $('#proactive-max-label').textContent = `${maxPerDay} 条/天`;
    $('#proactive-quiet-start').value = `${String(behavior.proactive_quiet_start_hour ?? 23).padStart(2, '0')}:00`;
    $('#proactive-quiet-end').value = `${String(behavior.proactive_quiet_end_hour ?? 8).padStart(2, '0')}:00`;
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
      proactive_quiet_start_hour: Number(($('#proactive-quiet-start').value || '23:00').split(':')[0]),
      proactive_quiet_end_hour: Number(($('#proactive-quiet-end').value || '08:00').split(':')[0]),
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
    if (payload.proactive_quiet_start_hour === payload.proactive_quiet_end_hour) {
      toast('免打扰开始和结束不能是同一个小时', true);
      button.disabled = false;
      button.textContent = '保存配置';
      return;
    }
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

  function formatBytes(size) {
    const value = Number(size) || 0;
    if (value < 1024) return `${value} B`;
    if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
    return `${(value / 1024 / 1024).toFixed(1)} MB`;
  }

  function diagRow(title, detail, ok) {
    const row = el('div', 'diag-row' + (ok ? '' : ' bad'));
    const body = el('div');
    body.append(el('b', '', title), el('small', '', detail || ''));
    row.append(body, el('em', '', ok ? '正常' : '异常'));
    return row;
  }

  async function loadDiagnostics() {
    const ports = $('#diag-ports');
    const env = $('#diag-env');
    ports.replaceChildren(el('p', 'muted small', '检查中……'));
    env.replaceChildren(el('p', 'muted small', '读取中……'));
    try {
      const data = await api('/api/diagnostics');
      ports.replaceChildren();
      (data.ports || []).forEach((item) => {
        ports.append(diagRow(`${item.name} :${item.port}`, item.detail, item.ok));
      });
      const model = data.model || {};
      $('#diag-model-info').textContent = `${model.id || '未填写'} · ${model.base_url || '未填写'}\nAPI Key：${model.key_hint || '未设置'}`;
      const log = data.log || {};
      $('#diag-log-path').textContent = log.path || '—';
      $('#diag-log-size').textContent = log.exists ? formatBytes(log.bytes) : '还没有日志';
      env.replaceChildren();
      env.append(diagRow('Python', data.python || '—', true));
      env.append(diagRow('数据目录', data.data_dir || '—', true));
      env.append(diagRow('模型已配置', model.configured ? '是' : '否', !!model.configured));
    } catch (err) {
      ports.replaceChildren(el('p', 'muted small', err.message));
      env.replaceChildren(el('p', 'muted small', err.message));
    }
  }

  async function testDiagnosticsModel() {
    const button = $('#diag-test-model');
    const result = $('#diag-model-result');
    button.disabled = true;
    button.textContent = '测试中…';
    result.className = 'diag-result';
    result.textContent = '正在请求模型，只发一次最小请求……';
    try {
      const data = await api('/api/onboarding/test-model', { method: 'POST', body: {} });
      result.className = `diag-result ${data.ok ? 'ok' : 'bad'}`;
      result.textContent = data.ok
        ? `连接成功：${data.model_id || ''}，${data.latency_ms} ms`
        : `失败：${data.detail || '未知错误'}`;
    } catch (err) {
      result.className = 'diag-result bad';
      result.textContent = err.message;
    } finally {
      button.disabled = false;
      button.textContent = '测试一次';
    }
  }

  async function exportDiagnostics() {
    const button = $('#diagnostics-export');
    button.disabled = true;
    button.textContent = '打包中…';
    try {
      const data = await api('/api/diagnostics/export');
      const blob = new Blob([data.content || ''], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = data.filename || 'dafeiyu-diagnostics.txt';
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast(`已导出 ${data.filename || '诊断包'}`);
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      button.textContent = '导出日志';
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

  async function toggleMuteToday() {
    const muted = !!(state.status && state.status.proactive && state.status.proactive.muted_today);
    try {
      await api('/api/proactive/mute', { method: 'POST', body: { muted: !muted } });
      toast(muted ? '今天就到这里，她不会再主动找你' : '好，今天还是会去找你');
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
    $$('#page-overview input[data-module]').forEach((box) => {
      box.addEventListener('change', () => toggleModule(box));
    });
    $('#emergency').addEventListener('click', toggleEmergency);
    $$('.mute-today').forEach((button) => button.addEventListener('click', toggleMuteToday));
    $('#proactive-max').addEventListener('input', (event) => {
      $('#proactive-max-label').textContent = `${event.target.value} 条/天`;
    });
    $('#refresh').addEventListener('click', () => {
      loadStatus();
      if (state.page === 'chat') loadChat();
      if (state.page === 'memory') loadMemory();
      if (state.page === 'persona') loadPersona();
      if (state.page === 'config') loadSettings();
      if (state.page === 'logs') loadLogs();
      if (state.page === 'diagnostics') loadDiagnostics();
    });
    $('#diagnostics-refresh').addEventListener('click', loadDiagnostics);
    $('#diagnostics-export').addEventListener('click', exportDiagnostics);
    $('#diag-test-model').addEventListener('click', testDiagnosticsModel);
    $('#reload-chat').addEventListener('click', loadChat);
    $('#reload-memory').addEventListener('click', loadMemory);
    $('#add-preference').addEventListener('click', addPreference);
    $('#style-review').addEventListener('click', reviewStyles);
    $('#reload-goals').addEventListener('click', loadGoals);
    $('#goal-review').addEventListener('click', reviewGoals);
    let memorySearchTimer = null;
    $('#memory-search').addEventListener('input', () => {
      clearTimeout(memorySearchTimer);
      memorySearchTimer = setTimeout(loadMemory, 280);
    });
    $('#reload-logs').addEventListener('click', loadLogs);
    $('#save-persona').addEventListener('click', savePersona);
    $('#restore-persona').addEventListener('click', restorePersona);
    $('#persona-preview-close').addEventListener('click', () => {
      $('#persona-preview').hidden = true;
      previewTemplateId = '';
      previewTemplateName = '';
    });
    $('#persona-preview-apply').addEventListener('click', () => {
      if (!previewTemplateId) return;
      applyPersonaTemplate(previewTemplateId, previewTemplateName);
    });
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
