'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
let state = null;
let currentView = 'conversation';
let chatBusy = false;
let toastTimer;
let latestMessageCount = -1;
const viewNames = { conversation: 'ラテと話す', jobs: '仕事の候補', notes: '準備ノート', profile: '覚えていること' };
const yen = value => value == null ? '未確認' : `${Number(value).toLocaleString('ja-JP')}円`;

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = String(text);
  return el;
}

async function api(path, body) {
  if (window.LATTE_OFFLINE) return window.LATTE_OFFLINE.request(path, body);
  const options = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  const response = await fetch(path, options);
  let result;
  try { result = await response.json(); } catch (_) { throw new Error('応答を読み取れませんでした。もう一度試してください。'); }
  if (!response.ok) throw new Error(result.error || 'うまく保存できませんでした。少し待って、もう一度試してね。');
  return result;
}

function toast(message, isError = false) {
  const el = $('#toast');
  clearTimeout(toastTimer);
  el.textContent = message;
  el.classList.toggle('error', isError);
  el.hidden = false;
  toastTimer = setTimeout(() => { el.hidden = true; }, isError ? 9000 : 4500);
}

async function loadState() {
  try {
    state = await api('/api/state');
    $('#load-error').hidden = true;
    render();
    $('#chat-input').disabled = false;
    $('#chat-send').disabled = false;
    return true;
  } catch (error) {
    $('#load-error').hidden = false;
    $('#load-error span').textContent = `読み込めませんでした。${error.message}`;
    return false;
  }
}

function navigate(view, updateHash = true) {
  if (!viewNames[view]) view = 'conversation';
  currentView = view;
  $$('.view').forEach(el => { el.hidden = el.id !== `view-${view}`; });
  $$('.nav-item').forEach(el => {
    const active = el.dataset.view === view;
    el.classList.toggle('active', active);
    if (active) el.setAttribute('aria-current', 'page'); else el.removeAttribute('aria-current');
  });
  $('#view-title').textContent = viewNames[view];
  document.title = `${viewNames[view]} — LATTE`;
  if (updateHash) history.replaceState(null, '', `#${view}`);
}

function render() {
  if (!state) return;
  renderConnection();
  renderMessages();
  renderCheckin();
  renderNextStep();
  renderJobs();
  renderNotes();
  renderProfile();
}

function renderConnection() {
  const connection = state.connection || {};
  const isAI = connection.mode === 'ai';
  const badge = $('#connection-badge');
  badge.classList.toggle('ai', isAI);
  $('span:last-child', badge).textContent = connection.label || (isAI ? 'AIモード・接続未確認' : 'ガイドモード');
  $('#chat-mode-caption').textContent = isAI ? 'AIと、あなたの記録をもとに会話します。' : '記録をもとに、小さな一歩を案内します。';
  $('#chat-disclosure').textContent = isAI ? 'AIの返答には誤りが含まれることがあります。ChatGPTでの会話とは自動同期されません。' : 'ここは記憶と準備を保存するローカルガイド。ChatGPTでの会話とは自動同期されません。';
  $('#connection-title').textContent = isAI ? (connection.label || 'AIモード・接続未確認') : '現在はローカルガイドです';
  $('#connection-detail').textContent = connection.detail || '記録した情報を使った案内を行います。自由なAI会話はまだ接続されていません。';
  $('#ai-consent').checked = !!connection.consent;
  $('#ai-consent').disabled = !connection.configured;
  $('#consent-help').textContent = connection.configured ? '許可はいつでも取り消せます。取り消すと、外部AIに送らないガイドモードに戻ります。' : 'AI接続は未設定です。ガイドモードでは外部AIへ送信しません。';
}

function appendMessage(message, pending = false) {
  const role = message.role === 'user' ? 'user' : 'assistant';
  const wrapper = node('article', `message ${role}${pending ? ' pending' : ''}`);
  const meta = node('div', 'message-meta', role === 'user' ? 'YOU' : 'LATTE');
  if (message.created_at) {
    const date = new Date(message.created_at);
    if (!Number.isNaN(date.getTime())) {
      const time = node('time', '', date.toLocaleTimeString('ja-JP', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Tokyo' }));
      time.dateTime = message.created_at;
      meta.append(time);
    }
  }
  wrapper.append(meta, node('div', 'message-content', message.content));
  $('#chat-messages').append(wrapper);
  return wrapper;
}

function renderMessages() {
  const messages = state.messages || [];
  const shouldScroll = messages.length !== latestMessageCount;
  $('#chat-messages').replaceChildren();
  if (!messages.length) appendMessage({ role: 'assistant', content: 'ここは、ラテの作業ノート。\nChatGPTで話して決まったことを「覚えて：…」で残せるよ。気になる求人や、書類の準備もここで整理できるから、あなたのペースで使ってね。' });
  messages.forEach(message => appendMessage(message));
  if (chatBusy) appendMessage({ role: 'assistant', content: '今の話を読んでいるよ…' }, true);
  latestMessageCount = messages.length;
  $('#chat-prompts').hidden = messages.length > 2;
  if (shouldScroll) scrollMessages();
}

function scrollMessages() {
  requestAnimationFrame(() => { const el = $('#chat-messages'); el.scrollTop = el.scrollHeight; });
}

function latestCheckin() {
  const entries = state.checkins || [];
  if (!entries.length) return null;
  return [...entries].sort((a, b) => String(b.created_at || b.date || '').localeCompare(String(a.created_at || a.date || '')))[0];
}

function isTodayCheckin(checkin) {
  if (!checkin) return false;
  const raw = checkin.created_at || checkin.date;
  if (!raw) return false;
  const stamp = new Date(raw);
  return !Number.isNaN(stamp.getTime()) && stamp.toLocaleDateString('en-CA', { timeZone: 'Asia/Tokyo' }) === new Date().toLocaleDateString('en-CA', { timeZone: 'Asia/Tokyo' });
}

function renderCheckin() {
  const checkin = latestCheckin();
  const isToday = isTodayCheckin(checkin);
  $$('.mood-option').forEach(el => { el.setAttribute('aria-pressed', String(isToday && checkin.energy === el.dataset.energy)); });
  $('#checkin-feedback').textContent = isToday ? ({ low: '今日は、休むのも大事な一歩。小さくいこう。', okay: 'いつものペースで。ひとつずつ話していこう。', good: '余裕がある日だね。気になることを、ひとつ進めよう。' }[checkin.energy] || '今日の調子を覚えました。') : '';
}

function renderNextStep() {
  const checkin = latestCheckin();
  const jobs = (state.jobs || []).filter(job => job.status !== 'archived');
  const tasks = (state.tasks || []).filter(task => task.status !== 'cancelled' && !isTaskDone(task));
  let text = '今、いちばん気になっていることを聞かせて。考えをまとめるのは、そこからで大丈夫。';
  let label = '気持ちを話す';
  let action = () => { navigate('conversation'); $('#chat-input').focus(); };
  if (isTodayCheckin(checkin) && checkin.energy === 'low') {
    text = '今日は、無理に準備を進めなくてもいいよ。ここに気持ちを置いていくだけでも。';
  } else if (tasks.length) {
    text = tasks[0].title;
    label = '準備ノートを開く';
    action = () => navigate('notes');
  } else if (jobs.length) {
    text = '登録した候補を、一緒にひとつ見てみよう。条件が分からないところも、整理できるよ。';
    label = '仕事の候補を見る';
    action = () => navigate('jobs');
  }
  $('#next-step-text').textContent = text;
  const button = $('#next-step-action');
  button.textContent = `${label} ↗`;
  button.onclick = action;
}

function emptyState(icon, title, description, buttonLabel, action) {
  const wrapper = node('div', 'empty-state');
  wrapper.append(node('div', 'empty-icon', icon), node('h3', '', title), node('p', '', description));
  if (buttonLabel) {
    const button = node('button', 'primary-button', buttonLabel);
    button.type = 'button';
    button.addEventListener('click', action);
    wrapper.append(button);
  }
  return wrapper;
}

function jobCard(job) {
  const card = node('article', 'job-card');
  const top = node('div', 'job-top');
  const heading = node('div');
  heading.append(node('p', 'job-company', job.company || '会社名は未確認'), node('h3', '', job.title));
  const evaluation = job.evaluation || {};
  const fit = evaluation.fit || 'check';
  const fitText = job.status === 'preparing' ? '応募準備中・未応募' : job.status === 'archived' ? '保留中' : ({ promising: '条件を検討したい', check: '確認しながら検討', mismatch: '希望と違う点あり' }[fit] || '確認しながら検討');
  top.append(heading, node('span', `pill ${fit}`, fitText));
  card.append(top, node('p', 'job-source', 'あなたが登録・掲載内容は未照合'));
  const conditions = node('div', 'job-conditions');
  let salary = '未確認';
  if (job.salary_min != null || job.salary_max != null) salary = `${job.salary_min == null ? '下限不明' : yen(job.salary_min)} 〜 ${job.salary_max == null ? '上限不明' : yen(job.salary_max)}`;
  [['在宅', job.remote_days == null ? '未確認' : `週${job.remote_days}日`], ['月給・額面', salary], ['制作業務', job.creative_ratio == null ? '未確認' : `${job.creative_ratio}%`], ['勤務地', job.location || '未確認']].forEach(([label, value]) => {
    const el = node('span'); el.append(node('b', '', label), document.createTextNode(value)); conditions.append(el);
  });
  card.append(conditions);
  const evaluationWrap = node('div', 'job-evaluation');
  const reasons = node('div'); reasons.append(node('h4', '', 'いま分かること'));
  const knownReasons = [...(evaluation.reasons || []), ...(evaluation.cautions || [])];
  (knownReasons.length ? knownReasons : ['条件の判断材料は、まだ十分にありません。']).forEach(text => reasons.append(node('p', '', text)));
  const unknowns = node('div'); unknowns.append(node('h4', '', '確かめておきたいこと'));
  (evaluation.unknowns?.length ? evaluation.unknowns : ['掲載内容が現在も有効か、応募前に確認してください。']).forEach(text => unknowns.append(node('p', '', text)));
  evaluationWrap.append(reasons, unknowns); card.append(evaluationWrap);
  const footer = node('div', 'job-footer');
  if (job.status !== 'archived') {
    const prepare = node('button', 'secondary-button', job.status === 'preparing' ? '準備ノートを見る ↗' : 'この仕事の準備を進める ↗');
    prepare.addEventListener('click', async () => {
      if (job.status === 'preparing') { navigate('notes'); return; }
      prepare.disabled = true;
      try { state = await api(`/api/jobs/${encodeURIComponent(job.id)}/prepare`, {}); render(); navigate('notes'); toast('下書きと確認ステップを用意しました。応募はしていません。'); }
      catch (error) { toast(error.message, true); prepare.disabled = false; }
    });
    footer.append(prepare);
    const archive = node('button', 'text-button', 'いったん保留');
    archive.addEventListener('click', async () => {
      archive.disabled = true;
      try { state = await api(`/api/jobs/${encodeURIComponent(job.id)}/archive`, {}); render(); toast('候補を保留にしました。'); }
      catch (error) { toast(error.message, true); archive.disabled = false; }
    });
    footer.append(archive);
  }
  if (job.url) {
    try {
      const url = new URL(job.url);
      if (['https:', 'http:'].includes(url.protocol)) {
        const link = node('a', 'job-source-link', '掲載元を開く ↗');
        link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; footer.append(link);
      }
    } catch (_) { /* Invalid stored URLs are not made clickable. */ }
  }
  card.append(footer);
  return card;
}

function renderJobs() {
  const jobs = state.jobs || [];
  const active = jobs.filter(job => job.status !== 'archived');
  const archived = jobs.filter(job => job.status === 'archived');
  $('#jobs-count').textContent = active.length;
  const list = $('#jobs-list'); list.replaceChildren();
  if (!active.length) list.append(emptyState('⌕', '気になる仕事を、一緒に見てみよう。', '求人票をひとつ持ってきてくれたら、大切な条件と照らし合わせて整理するよ。まだ見つかっていなくても、焦らなくて大丈夫。', '＋ 求人を持ってくる', openJobDialog));
  active.slice(0, 3).forEach(job => list.append(jobCard(job)));
  if (active.length > 3) {
    const note = node('p', 'more-jobs-note', `まずは上位3件を表示しています。ほかに${active.length - 3}件の候補を保存しています。`);
    const showAll = node('button', 'text-button', '残りの候補も見る');
    showAll.addEventListener('click', () => { note.remove(); showAll.remove(); active.slice(3).forEach(job => list.append(jobCard(job))); });
    list.append(note, showAll);
  }
  $('#show-archived').hidden = archived.length === 0;
  $('#show-archived').textContent = `保留した候補を見る（${archived.length}件）`;
  $('#archived-jobs').replaceChildren(...archived.map(jobCard));
}

function isTaskDone(task) { return task.status === 'done' || task.status === 'completed' || task.completed === true; }

function renderNotes() {
  const tasks = state.tasks || [];
  const activeTasks = tasks.filter(task => task.status !== 'cancelled');
  $('#task-progress').textContent = activeTasks.length ? `${activeTasks.filter(isTaskDone).length} / ${activeTasks.length} 完了` : '';
  const taskList = $('#tasks-list'); taskList.replaceChildren();
  if (!tasks.length) taskList.append(node('p', 'small-empty', 'まだ、急いで進めることはありません。候補の仕事を選ぶと、必要な準備をここに整理します。'));
  tasks.forEach(task => {
    const label = node('label', `task-item${isTaskDone(task) ? ' done' : ''}${task.status === 'cancelled' ? ' cancelled' : ''}`);
    const checkbox = document.createElement('input'); checkbox.type = 'checkbox'; checkbox.checked = isTaskDone(task);
    checkbox.disabled = task.status === 'cancelled';
    checkbox.addEventListener('change', async () => {
      checkbox.disabled = true;
      try { state = await api(`/api/tasks/${encodeURIComponent(task.id)}/toggle`, {}); render(); }
      catch (error) { checkbox.checked = !checkbox.checked; checkbox.disabled = false; toast(error.message, true); }
    });
    label.append(checkbox, node('span', '', task.title));
    if (task.status === 'cancelled') label.append(node('small', 'cancelled-label', '候補を保留中'));
    taskList.append(label);
  });
  const drafts = $('#drafts-list'); drafts.replaceChildren();
  if (!(state.drafts || []).length) drafts.append(node('p', 'small-empty', '下書きは、ここに残していきます。求人の「この仕事の準備を進める」から始められます。'));
  (state.drafts || []).forEach(draft => {
    const card = node('article', 'draft-card');
    card.append(node('h3', '', draft.title), node('div', 'draft-body', draft.body));
    const actions = node('div', 'draft-actions');
    const download = node('button', 'text-button', 'テキストを保存 ↓');
    download.addEventListener('click', () => {
      const blob = new Blob([`${draft.title}\n\n${draft.body}`], { type: 'text/plain;charset=utf-8' });
      const url = URL.createObjectURL(blob); const a = node('a'); a.href = url; a.download = 'LATTE_応募準備メモ.txt'; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    });
    const copy = node('button', 'text-button', 'コピー');
    copy.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(draft.body); toast('下書きをコピーしました。'); }
      catch (_) { toast('コピーできませんでした。「テキストを保存」を使ってください。', true); }
    });
    actions.append(download, copy); card.append(actions); drafts.append(card);
  });
}

function renderProfile() {
  const facts = state.profile?.facts || [];
  const list = $('#facts-list'); list.replaceChildren();
  if (!facts.length) list.append(node('p', 'small-empty', '覚えていることは、まだありません。まずは大切にしたい条件から、教えてね。'));
  const statusLabels = { confirmed: '確認済み', reported: '本人の申告', unknown: '未確認' };
  facts.forEach(fact => {
    const item = node('article', 'fact-item'); const heading = node('div', 'fact-head');
    const actions = node('div', 'fact-actions');
    actions.append(node('span', 'pill', statusLabels[fact.status] || '記録'));
    const remove = node('button', 'delete-memory', '削除');
    remove.setAttribute('aria-label', `${fact.label || 'この記録'}を削除`);
    remove.addEventListener('click', async () => {
      if (!window.confirm(`「${fact.label || 'この記録'}」を削除しますか？`)) return;
      remove.disabled = true;
      try { state = await api(`/api/memories/${encodeURIComponent(fact.key)}/delete`, {}); render(); toast('この記録を削除しました。'); }
      catch (error) { remove.disabled = false; toast(error.message, true); }
    });
    actions.append(remove);
    heading.append(node('h3', '', fact.label || fact.key), actions);
    item.append(heading, node('p', 'fact-value', typeof fact.value === 'string' ? fact.value : JSON.stringify(fact.value)));
    if (fact.source) item.append(node('p', 'fact-source', `出典：${fact.source}`));
    list.append(item);
  });
  const preferences = state.profile?.preferences || {};
  const form = $('#preferences-form');
  Object.keys(preferences).forEach(key => { if (form.elements.namedItem(key)) form.elements.namedItem(key).value = preferences[key] ?? ''; });
}

async function sendChat(message) {
  if (chatBusy || !state || !message.trim()) return;
  chatBusy = true;
  $('#chat-send').disabled = true;
  $('#chat-input').disabled = true;
  $$('#chat-prompts button').forEach(button => { button.disabled = true; });
  appendMessage({ role: 'user', content: message });
  const pending = appendMessage({ role: 'assistant', content: '今の話を読んでいるよ…' }, true);
  scrollMessages();
  try {
    await api('/api/chat', { message });
    $('#chat-input').value = '';
    $('#chat-input').style.height = '';
  } catch (error) {
    toast(error.message, true);
    if (!$('#chat-input').value) $('#chat-input').value = message;
  } finally {
    pending.remove();
    chatBusy = false;
    await loadState();
    $('#chat-send').disabled = false;
    $('#chat-input').disabled = false;
    $$('#chat-prompts button').forEach(button => { button.disabled = false; });
    $('#chat-input').focus();
  }
}

let dialogOpener = null;
function openJobDialog() {
  dialogOpener = document.activeElement;
  $('#job-form-error').textContent = '';
  $('#job-dialog').showModal();
}

$('#close-job-dialog').addEventListener('click', () => $('#job-dialog').close());
$('#job-dialog').addEventListener('close', () => { if (dialogOpener?.isConnected) dialogOpener.focus(); });
$('#job-dialog').addEventListener('click', event => { if (event.target === $('#job-dialog')) { const r = event.target.getBoundingClientRect(); if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) event.target.close(); } });
$('#add-job').addEventListener('click', openJobDialog);
$('#retry-load').addEventListener('click', loadState);
$$('[data-view]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.view)));
$$('[data-open-profile]').forEach(button => button.addEventListener('click', () => { navigate('profile'); $('.connection-surface').scrollIntoView({ behavior: 'smooth', block: 'center' }); }));
window.addEventListener('hashchange', () => navigate(location.hash.slice(1), false));
$('#show-archived').addEventListener('click', () => { const expanded = $('#show-archived').getAttribute('aria-expanded') === 'true'; $('#show-archived').setAttribute('aria-expanded', String(!expanded)); $('#archived-jobs').hidden = expanded; });

$('#chat-form').addEventListener('submit', event => { event.preventDefault(); sendChat($('#chat-input').value.trim()); });
$('#chat-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('#chat-form').requestSubmit(); } });
$('#chat-input').addEventListener('input', event => { event.target.style.height = 'auto'; event.target.style.height = `${Math.min(event.target.scrollHeight, 150)}px`; });
$$('[data-prompt]').forEach(button => button.addEventListener('click', () => sendChat(button.dataset.prompt)));

$$('[data-energy]').forEach(button => button.addEventListener('click', async () => {
  if (!state) return;
  $$('.mood-option').forEach(el => { el.disabled = true; });
  try { state = await api('/api/checkins', { energy: button.dataset.energy }); render(); }
  catch (error) { toast(error.message, true); }
  finally { $$('.mood-option').forEach(el => { el.disabled = false; }); }
}));

$('#preferences-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; const button = $('button[type=submit]', form); button.disabled = true;
  const data = new FormData(form); const preferences = {};
  for (const [key, value] of data.entries()) preferences[key] = ['commute_minutes', 'remote_days', 'min_monthly_salary', 'overtime_hours'].includes(key) ? (value.trim() === '' ? null : Number(value)) : value.trim();
  try { state = await api('/api/preferences', preferences); render(); $('#preferences-feedback').textContent = '大切な条件を覚えました。'; }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

$('#memory-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; const button = $('button[type=submit]', form); button.disabled = true;
  const value = $('#memory-value').value.trim();
  if (!value) { button.disabled = false; return; }
  try { state = await api('/api/memories', { label: value.slice(0, 24), value }); render(); form.reset(); $('#memory-feedback').textContent = 'あなたから聞いたこととして、覚えました。'; }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; }
});

$('#ai-consent').addEventListener('change', async event => {
  const checkbox = event.currentTarget; checkbox.disabled = true;
  try { state = await api('/api/ai-consent', { enabled: checkbox.checked }); render(); toast(state.connection?.consent ? 'AIとの会話への送信を許可しました。' : '外部AIへの送信を停止しました。'); }
  catch (error) { checkbox.checked = !checkbox.checked; checkbox.disabled = false; toast(error.message, true); }
});

$('#job-form').addEventListener('submit', async event => {
  event.preventDefault(); const form = event.currentTarget; const button = $('#save-job'); button.disabled = true; $('#job-form-error').textContent = '';
  const data = new FormData(form); const job = {};
  for (const [key, value] of data.entries()) job[key] = ['remote_days', 'salary_min', 'salary_max', 'creative_ratio'].includes(key) ? (value.trim() === '' ? null : Number(value)) : value.trim();
  try { state = await api('/api/jobs', job); render(); form.reset(); $('#job-dialog').close(); toast('候補を記録し、分かること・確認したいことを整理しました。'); }
  catch (error) { $('#job-form-error').textContent = error.message; }
  finally { button.disabled = false; }
});

navigate(location.hash.slice(1) || 'conversation', false);
loadState();
