'use strict';

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const storageKey = 'latte.dashboard.key.v1';
  const demoMode = new URLSearchParams(location.search).get('demo') === '1';
  const names = { overview: '今の状況', conversations: '会話の記録', proposals: '提案と準備', profile: 'あなたの軸' };
  const statuses = { awaiting_decision: '方針の判断待ち', approved: '方針決定・準備前', preparing: 'ラテが準備中', ready_for_review: '内容の確認待ち', waiting_to_apply: '応募の判断待ち', applied: '応募済み', deferred: 'いったん保留' };
  const kinds = { approach: '進め方の提案', job: '求人候補', document: '書類・準備' };
  const text = value => value == null ? '' : typeof value === 'string' ? value : String(value);
  let snapshot = null;
  let keyValue = null;
  let currentView = 'overview';
  let filter = 'all';
  let loading = false;
  let lastFetched = null;
  let keySaved = true;
  let requestGeneration = 0;
  let activeRequest = null;

  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content != null) element.textContent = text(content);
    return element;
  }

  function formatDate(value, short = false) {
    if (!value) return '日時は未記録';
    if (/^\d{4}-\d{2}-\d{2}$/.test(text(value))) {
      const [year, month, day] = value.split('-').map(Number);
      return short ? `${month}月${day}日` : `${year}年${month}月${day}日`;
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '日時は未記録';
    return new Intl.DateTimeFormat('ja-JP', { timeZone: 'Asia/Tokyo', ...(short ? { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' } : { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' }) }).format(date);
  }

  function sourceLabel(source) {
    return typeof source === 'string' ? source : source && typeof source.label === 'string' ? source.label : 'このチャット';
  }

  function empty(title, description) {
    const element = node('div', 'empty-state');
    element.append(node('h3', '', title), node('p', '', description));
    return element;
  }

  function validApplication(proposal) {
    if (proposal.kind !== 'job' || proposal.packet_ready !== true || !proposal.job?.source_url || !proposal.job?.verified_at) return false;
    try { return ['http:', 'https:'].includes(new URL(proposal.job.source_url).protocol); }
    catch (_) { return false; }
  }

  function proposalStatus(proposal) {
    if (proposal.status === 'waiting_to_apply' && !validApplication(proposal)) return '応募条件の確認が必要';
    if (proposal.status === 'applied' && (!validApplication(proposal) || !proposal.submission_confirmed_at)) return '応募状況の確認が必要';
    return statuses[proposal.status] || '状況の確認が必要';
  }

  function navigate(view) {
    if (!names[view]) view = 'overview';
    currentView = view;
    $$('.view').forEach(element => { element.hidden = element.id !== `view-${view}`; });
    $$('.nav-item').forEach(button => {
      const active = button.dataset.view === view;
      button.classList.toggle('active', active);
      if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
    });
    $('#current-view-title').textContent = names[view];
    document.title = `${names[view]} — LATTE`;
  }

  function trace(conversationId, label = '元の会話を辿る →') {
    const button = node('button', 'text-button', label);
    const record = snapshot.conversations.find(item => item.id === conversationId);
    if (!record) {
      button.textContent = '元の会話は未反映';
      button.disabled = true;
      return button;
    }
    button.addEventListener('click', () => {
      $('#conversation-search').value = '';
      renderConversations();
      navigate('conversations');
      const details = $$('.conversation-card').find(element => element.dataset.recordId === conversationId);
      if (!details) return;
      details.open = true;
      details.classList.add('highlight');
      requestAnimationFrame(() => { details.scrollIntoView({ block: 'center', behavior: 'smooth' }); $('summary', details).focus({ preventScroll: true }); });
      setTimeout(() => details.classList.remove('highlight'), 3500);
    });
    return button;
  }

  function proposalCard(proposal, compact = false) {
    const card = node('article', 'proposal-card');
    const meta = node('div', 'card-meta');
    const color = proposal.status === 'awaiting_decision' || proposal.status === 'waiting_to_apply' ? ' waiting' : proposal.status === 'ready_for_review' ? ' ready' : proposal.status === 'deferred' ? ' deferred' : '';
    meta.append(node('span', 'pill kind', kinds[proposal.kind] || 'ラテの提案'), node('span', 'pill' + color, proposalStatus(proposal)));
    card.append(meta, node('h3', '', proposal.title), node('p', 'proposal-summary', proposal.summary));
    if (!compact) {
      const detail = node('div', 'proposal-detail');
      if (proposal.recommendation) detail.append(node('h4', '', 'LATTEの提案'), node('p', '', proposal.recommendation));
      if (proposal.reasons?.length) {
        detail.append(node('h4', '', 'そう考えた理由'));
        const list = node('ul'); proposal.reasons.forEach(reason => list.append(node('li', '', reason))); detail.append(list);
      }
      if (proposal.unknowns?.length) {
        detail.append(node('h4', '', 'まだ確認していないこと'));
        const list = node('ul'); proposal.unknowns.forEach(unknown => list.append(node('li', '', unknown))); detail.append(list);
      }
      if (proposal.next_action) detail.append(node('h4', '', '次にできること'), node('p', '', proposal.next_action));
      const decisions = snapshot.decisions.filter(item => item.proposal_ids?.includes(proposal.id));
      decisions.forEach(decision => { detail.append(node('h4', '', '本人の判断'), node('p', '', decision.choice)); });
      (proposal.evidence || []).forEach(evidence => {
        const conversation = snapshot.conversations.find(item => item.id === evidence.conversation_id);
        const label = evidence.who === 'assistant' ? 'LATTEの言葉' : conversation && sourceLabel(conversation.source).includes('添付') ? '資料の記載' : '本人の言葉';
        if (evidence.quote) detail.append(node('blockquote', 'evidence-quote', `${label}：${evidence.quote}`));
      });
      card.append(detail);
    }
    const footer = node('div', 'proposal-footer');
    const ids = [...new Set((proposal.evidence || []).map(item => item.conversation_id).filter(Boolean))];
    if (compact) {
      const more = node('button', 'text-button', '提案と理由を見る →');
      more.addEventListener('click', () => { filter = 'all'; renderProposalList(); navigate('proposals'); const target = $$('.proposal-card').find(element => element.dataset.proposalId === proposal.id); target?.scrollIntoView({ behavior: 'smooth', block: 'start' }); });
      footer.append(more);
    }
    ids.forEach((id, index) => footer.append(trace(id, ids.length > 1 ? `会話 ${index + 1} を辿る →` : undefined)));
    if (!ids.length) footer.append(node('span', 'muted', '会話との紐づけは未記録'));
    if (proposal.kind === 'job' && proposal.job?.source_url) {
      try {
        const url = new URL(proposal.job.source_url);
        if (['https:', 'http:'].includes(url.protocol)) {
          const link = node('a', 'text-button', '求人の掲載元を開く ↗');
          link.href = url.href; link.target = '_blank'; link.rel = 'noopener noreferrer'; footer.append(link);
        }
      } catch (_) { /* Stored data never becomes executable URL content. */ }
    }
    card.dataset.proposalId = proposal.id;
    card.append(footer);
    return card;
  }

  function renderOverview() {
    const pending = snapshot.proposals.filter(item => item.status === 'awaiting_decision');
    const ready = snapshot.documents.filter(item => item.status === 'ready_for_review');
    const applications = snapshot.proposals.filter(item => item.status === 'waiting_to_apply' && validApplication(item));
    const setStat = (selector, count) => { $(selector).replaceChildren(document.createTextNode(String(count)), node('small', '', '件')); };
    setStat('#stat-decisions', pending.length); setStat('#stat-documents', ready.length); setStat('#stat-applications', applications.length);
    $('#last-updated').textContent = `最終反映 ${formatDate(snapshot.updated_at)} · ${snapshot.coverage?.label || '反映済みの会話'} · ${snapshot.coverage?.record_count ?? snapshot.conversations.length}件`;
    const focus = $('#next-focus'); focus.replaceChildren();
    const next = pending[0] || applications[0] || snapshot.proposals.find(item => item.status === 'preparing');
    if (next) {
      focus.append(node('h3', '', next.title), node('p', '', next.next_action || next.summary));
      const button = node('button', 'text-button', '提案の背景を見る →');
      button.addEventListener('click', () => { filter = 'all'; renderProposalList(); navigate('proposals'); }); focus.append(button);
    } else if (ready[0]) {
      focus.append(node('h3', '', ready[0].title), node('p', '', 'ラテが用意した内容を、余裕のあるときに確認できます。判断や修正の相談は、いつものチャットで。'));
      const button = node('button', 'text-button', '用意したものを見る →'); button.addEventListener('click', () => navigate('proposals')); focus.append(button);
    } else focus.append(node('p', '', '今すぐ決めることはありません。いつものチャットで話したことを、LATTEが反映したらここで確認できます。'));
    const proposals = $('#overview-proposals'); proposals.replaceChildren();
    const active = snapshot.proposals.filter(item => !['deferred', 'applied'].includes(item.status)).slice(0, 3);
    if (!active.length) proposals.append(empty('まだ、急いで決めることはありません。', 'ラテから提案したことが反映されると、ここに理由と一緒に並びます。'));
    active.forEach(item => proposals.append(proposalCard(item, true)));
    const decisions = $('#recent-decisions'); decisions.replaceChildren();
    if (!snapshot.decisions.length) decisions.append(node('p', 'quiet-note', '本人が決めたことは、まだ記録されていません。提案と決定は分けて扱います。'));
    snapshot.decisions.slice(-3).reverse().forEach(decision => {
      const card = node('article', 'decision-item');
      card.append(node('h3', '', decision.title), node('p', 'decision-choice', decision.choice));
      if (decision.reason) card.append(node('p', 'decision-reason', decision.reason));
      (decision.conversation_ids || []).slice(0, 1).forEach(id => card.append(trace(id)));
      decisions.append(card);
    });
    const activities = $('#recent-activities'); activities.replaceChildren();
    if (!snapshot.activities.length) activities.append(node('p', 'quiet-note', '作業の進み具合が反映されると、ここに残ります。'));
    snapshot.activities.slice(-6).reverse().forEach(activity => {
      const item = node('article', 'activity-item');
      item.append(node('time', '', formatDate(activity.at, true)), node('p', '', activity.text));
      if (activity.conversation_id) item.append(trace(activity.conversation_id));
      activities.append(item);
    });
  }

  function renderConversations() {
    const coverage = snapshot.coverage || {};
    $('#coverage-description').textContent = coverage.description || 'この環境のLATTEが反映した会話を表示しています。';
    $('#coverage-meta').textContent = `${coverage.record_count ?? snapshot.conversations.length}件 · 最終記録 ${formatDate(coverage.last_recorded_at || snapshot.updated_at)}`;
    const search = $('#conversation-search').value.trim().toLocaleLowerCase('ja-JP');
    const conversations = snapshot.conversations.filter(record => !search || [record.title, record.user_text, record.assistant_text, record.summary, ...(record.tags || [])].join(' ').toLocaleLowerCase('ja-JP').includes(search));
    $('#conversation-count').textContent = `${conversations.length}件の記録${search ? ` / 全${snapshot.conversations.length}件` : ''}`;
    const list = $('#conversation-list'); list.replaceChildren();
    if (!conversations.length) list.append(empty(search ? 'その言葉を含む会話はありません。' : '会話は、まだ反映されていません。', search ? 'ほかの言葉で探してみてください。' : '反映された会話から、提案や判断の理由を辿れるようになります。'));
    conversations.slice().reverse().forEach(record => {
      const details = node('details', 'conversation-card'); details.dataset.recordId = record.id;
      const summary = node('summary');
      const kind = record.kind === 'summary' ? '要約' : record.kind === 'quoted_excerpt' ? '原文の抜粋' : '記録したやりとり';
      summary.append(node('h3', '', record.title));
      const meta = node('div', 'conversation-meta');
      meta.append(node('span', '', formatDate(record.at)), node('span', '', sourceLabel(record.source)), node('span', '', kind));
      summary.append(meta);
      if (record.tags?.length) { const tags = node('div', 'tags'); record.tags.forEach(tag => tags.append(node('span', 'tag', tag))); summary.append(tags); }
      const body = node('div', 'conversation-content');
      if (record.kind === 'summary') {
        body.append(node('h4', '', 'LATTEによる要約（原文ではありません）'), node('p', 'summary-text', record.summary || '要約文は未記録です。'));
      } else if (record.summary) body.append(node('h4', '', 'この会話のポイント（要約）'), node('p', 'summary-text', record.summary));
      if (record.user_text) body.append(node('h4', '', sourceLabel(record.source).includes('添付') ? '資料の記載' : 'あなたの言葉'), node('div', 'transcript-text user', record.user_text));
      if (record.assistant_text) body.append(node('h4', '', 'LATTEの返答'), node('div', 'transcript-text', record.assistant_text));
      const related = snapshot.proposals.filter(proposal => proposal.evidence?.some(evidence => evidence.conversation_id === record.id));
      if (related.length) {
        body.append(node('h4', '', 'この会話につながる提案'));
        related.forEach(proposal => {
          const button = node('button', 'text-button', `${proposal.title} →`);
          button.addEventListener('click', () => { filter = 'all'; renderProposalList(); navigate('proposals'); const target = $$('.proposal-card').find(element => element.dataset.proposalId === proposal.id); target?.scrollIntoView({ block: 'center', behavior: 'smooth' }); });
          body.append(button);
        });
      }
      details.append(summary, body); list.append(details);
    });
  }

  function renderProposalList() {
    $$('.filter-row button').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.filter === filter)));
    const list = $('#proposal-list'); list.replaceChildren();
    const proposals = snapshot.proposals.filter(proposal => filter === 'all' || filter === 'preparing' && ['approved', 'preparing', 'ready_for_review'].includes(proposal.status) || filter === 'waiting_to_apply' && proposal.status === filter && validApplication(proposal) || filter === 'awaiting_decision' && proposal.status === filter);
    if (!proposals.length) list.append(empty(filter === 'waiting_to_apply' ? '応募の判断待ちは、まだありません。' : 'ここに該当する提案はありません。', filter === 'waiting_to_apply' ? '実在する求人と準備が揃ったものだけを表示します。書類準備と応募済みは別に扱います。' : '記録していない提案や作業を、進行中として表示することはありません。'));
    proposals.forEach(proposal => list.append(proposalCard(proposal)));
    const documents = $('#document-list'); documents.replaceChildren();
    if (!snapshot.documents.length) documents.append(empty('書類は、まだ用意されていません。', 'このチャットでLATTEが作ったものを、確認が必要な点と一緒にここへ残します。'));
    snapshot.documents.forEach(documentRecord => {
      const card = node('article', 'document-card');
      const meta = node('div', 'card-meta'); meta.append(node('span', 'pill kind', 'LATTEが準備したもの'), node('span', 'pill ready', documentRecord.status === 'ready_for_review' ? '本人の確認待ち・未提出' : '下書き・未提出'));
      card.append(meta, node('h3', '', documentRecord.title), node('div', 'document-body', documentRecord.body));
      const actions = node('div', 'document-actions');
      (documentRecord.conversation_ids || []).forEach(id => actions.append(trace(id)));
      const download = node('button', 'text-button', 'テキストを保存 ↓');
      download.addEventListener('click', () => {
        const blob = new Blob([`${documentRecord.title}\n\n${documentRecord.body}`], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob); const anchor = document.createElement('a'); anchor.href = url; anchor.download = 'LATTE-preparation.txt'; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
      });
      actions.append(download); card.append(actions); documents.append(card);
    });
  }

  function renderProfile() {
    const preferences = snapshot.profile.preferences || {};
    const list = $('#preference-list'); list.replaceChildren();
    const rows = [['location', '勤務地・エリア', value => value], ['commute_minutes', '通勤の上限', value => `片道${value}分`], ['remote_days', '最低希望の在宅日数', value => `週${value}日`], ['min_monthly_salary', '月額額面給与の最低希望', value => `${Number(value).toLocaleString('ja-JP')}円`], ['employment', '希望の雇用形態', value => value], ['overtime_hours', '残業の上限', value => `月${value}時間`]];
    rows.forEach(([key, label, format]) => {
      const item = node('dl', 'preference-item'); const value = preferences[key];
      item.append(node('dt', '', label), node('dd', '', value == null || value === '' ? 'まだ確定していません' : format(value))); list.append(item);
    });
    const facts = $('#fact-list'); facts.replaceChildren();
    if (!snapshot.profile.facts.length) facts.append(empty('プロフィールは、まだ反映されていません。', 'このチャットで把握したことを、出典と未確認の点を分けて記録します。'));
    const labels = { confirmed: '確認済み', reported: '本人の申告', unknown: '未確認' };
    snapshot.profile.facts.forEach(fact => {
      const card = node('article', 'fact-card'); const meta = node('div', 'card-meta');
      meta.append(node('h3', '', fact.label), node('span', 'pill', labels[fact.status] || '記録'));
      card.append(meta, node('p', '', fact.value));
      if (fact.source) card.append(node('p', 'fact-source', `出典：${sourceLabel(fact.source)}`));
      facts.append(card);
    });
  }

  function renderAll() {
    renderOverview(); renderConversations(); renderProposalList(); renderProfile();
    $('#revision').textContent = `記録 v${snapshot.revision}`;
  }

  function showNotice(message, error = false) {
    const notice = $('#workspace-notice'); notice.textContent = message; notice.classList.toggle('error', error); notice.hidden = !message;
  }

  function locked(title, detail, retry = false) {
    $('#locked-screen').hidden = false; $('#workspace').hidden = true;
    $('#locked-status-title').textContent = title; $('#locked-status-detail').textContent = detail;
    $('#locked-retry').hidden = !retry;
  }

  function bytesFromBase64(value, urlSafe = false) {
    if (typeof value !== 'string' || !value) throw new Error('format');
    const source = urlSafe ? value.replace(/-/g, '+').replace(/_/g, '/') : value;
    try { return Uint8Array.from(atob(source), character => character.charCodeAt(0)); }
    catch (_) { throw new Error('format'); }
  }

  function validateSnapshot(value) {
    const fail = () => { throw new Error('snapshot'); };
    const object = item => item && typeof item === 'object' && !Array.isArray(item);
    const string = (item, key, required = false) => {
      if (item[key] == null && !required) return;
      if (typeof item[key] !== 'string' || required && !item[key]) fail();
    };
    const strings = (item, key) => { if (item[key] != null && (!Array.isArray(item[key]) || item[key].some(part => typeof part !== 'string'))) fail(); };
    const source = item => { if (item != null && typeof item !== 'string' && (!object(item) || typeof item.label !== 'string')) fail(); };
    const rows = (key, validate) => {
      if (!Array.isArray(value[key])) fail();
      value[key].forEach(item => { if (!object(item)) fail(); validate(item); });
    };
    if (!object(value) || value.schema_version !== 1 || !Number.isInteger(value.revision) || value.revision < 1 || !object(value.profile) || !Array.isArray(value.profile.facts) || !object(value.profile.preferences)) fail();
    string(value, 'updated_at', true);
    if (value.demo != null && typeof value.demo !== 'boolean') fail();
    if (value.coverage != null) {
      if (!object(value.coverage)) fail();
      for (const key of ['label', 'description', 'last_recorded_at']) string(value.coverage, key);
      if (value.coverage.record_count != null && (!Number.isInteger(value.coverage.record_count) || value.coverage.record_count < 0)) fail();
    }
    value.profile.facts.forEach(fact => {
      if (!object(fact)) fail();
      for (const key of ['key', 'label', 'value']) string(fact, key, true);
      string(fact, 'status'); source(fact.source);
    });
    for (const key of ['location', 'employment']) string(value.profile.preferences, key);
    for (const key of ['commute_minutes', 'remote_days', 'min_monthly_salary', 'overtime_hours']) if (value.profile.preferences[key] != null && (typeof value.profile.preferences[key] !== 'number' || !Number.isFinite(value.profile.preferences[key]))) fail();
    rows('conversations', item => {
      for (const key of ['id', 'title', 'at']) string(item, key, true);
      for (const key of ['user_text', 'assistant_text', 'summary']) string(item, key);
      if (!['exchange', 'summary', 'quoted_excerpt'].includes(item.kind)) fail();
      strings(item, 'tags'); source(item.source);
    });
    rows('proposals', item => {
      for (const key of ['id', 'title']) string(item, key, true);
      for (const key of ['summary', 'recommendation', 'next_action', 'submission_confirmed_at']) string(item, key);
      if (!['approach', 'job', 'document'].includes(item.kind) || !Object.hasOwn(statuses, item.status)) fail();
      strings(item, 'reasons'); strings(item, 'unknowns');
      if (item.packet_ready != null && typeof item.packet_ready !== 'boolean') fail();
      if (item.job != null) { if (!object(item.job)) fail(); string(item.job, 'source_url'); string(item.job, 'verified_at'); }
      if (item.evidence != null) {
        if (!Array.isArray(item.evidence)) fail();
        item.evidence.forEach(evidence => { if (!object(evidence)) fail(); string(evidence, 'conversation_id', true); string(evidence, 'quote'); string(evidence, 'who'); });
      }
    });
    rows('decisions', item => {
      for (const key of ['id', 'title', 'choice', 'at']) string(item, key, true);
      string(item, 'reason'); strings(item, 'conversation_ids'); strings(item, 'proposal_ids');
    });
    rows('documents', item => {
      for (const key of ['id', 'title', 'body']) string(item, key, true);
      if (!['draft', 'ready_for_review'].includes(item.status)) fail();
      strings(item, 'conversation_ids'); string(item, 'proposal_id');
    });
    rows('activities', item => {
      for (const key of ['id', 'at', 'text']) string(item, key, true);
      for (const key of ['kind', 'conversation_id', 'proposal_id']) string(item, key);
    });
    return value;
  }

  async function retrieve() {
    if (loading || !keyValue) return;
    const generation = ++requestGeneration;
    const requestedKey = keyValue;
    activeRequest = new AbortController();
    loading = true; $('#refresh').disabled = true; $('#locked-retry').disabled = true;
    if (!snapshot) locked('記録を開いています', '暗号化された記録を、この端末で復号しています。');
    try {
      if (!window.crypto?.subtle) throw new Error('crypto');
      let rawKey;
      try { rawKey = bytesFromBase64(requestedKey, true); } catch (_) { throw new Error('key'); }
      if (rawKey.length !== 32) throw new Error('key');
      let response;
      try { response = await fetch(demoMode ? './demo.enc.json' : './workspace.enc.json', { cache: 'no-store', redirect: 'error', credentials: 'omit', signal: activeRequest.signal }); }
      catch (_) { throw new Error('network'); }
      if (response.status === 404) throw new Error('missing');
      if (!response.ok) throw new Error('network');
      let envelope;
      try { envelope = await response.json(); } catch (_) { throw new Error('format'); }
      if (envelope.format !== 'latte-encrypted-v1' || envelope.algorithm !== 'AES-256-GCM') throw new Error('format');
      const nonce = bytesFromBase64(envelope.nonce);
      if (nonce.length !== 12) throw new Error('format');
      const ciphertext = bytesFromBase64(envelope.ciphertext);
      const key = await crypto.subtle.importKey('raw', rawKey, 'AES-GCM', false, ['decrypt']);
      let plain;
      try { plain = await crypto.subtle.decrypt({ name: 'AES-GCM', iv: nonce, additionalData: new TextEncoder().encode('LATTE workspace v1') }, key, ciphertext); }
      catch (_) { throw new Error('key'); }
      let decrypted;
      try {
        decrypted = validateSnapshot(JSON.parse(new TextDecoder().decode(plain)));
        if (demoMode && decrypted.demo !== true || !demoMode && decrypted.demo === true) throw new Error('snapshot');
      }
      catch (_) { throw new Error('snapshot'); }
      if (generation !== requestGeneration || requestedKey !== keyValue) return;
      snapshot = decrypted; lastFetched = new Date();
      keySaved = true;
      if (!demoMode) { try { localStorage.setItem(storageKey, requestedKey); } catch (_) { keySaved = false; } }
      $('#locked-screen').hidden = true; $('#workspace').hidden = false;
      $('#demo-banner').hidden = !demoMode;
      renderAll(); navigate(currentView);
      if (demoMode) {
        const panel = $('.connection-panel');
        $('h2', panel).textContent = '画面例について';
        $('p', panel).textContent = 'この画面の人物・会話・提案はすべて架空です。あなたの専用リンクの鍵は読み込まず、保存や変更もしません。';
        $('.fine-print', panel).textContent = '実際の記録は、暗号化データの保存方法を確認した後に反映します。';
        $('#disconnect').hidden = true;
      } else {
        const panel = $('.connection-panel');
        $('p', panel).textContent = keySaved ? '専用リンクの鍵を、このブラウザーに保存しています。記録の内容自体はブラウザーに保存せず、開くたびに復号して表示します。' : '専用リンクの鍵は、今開いている画面の中だけで保持しています。この端末への接続保存に失敗したため、次回は専用リンクから開いてください。';
      }
      $('#sync-badge').textContent = `最終確認 ${formatDate(lastFetched.toISOString(), true)}`;
      showNotice(keySaved ? '' : 'この端末の接続を保存できません。次回は専用リンクから開いてください。今回の記録は表示できます。');
    } catch (error) {
      if (generation !== requestGeneration || requestedKey !== keyValue) return;
      const messages = {
        missing: ['記録の反映を準備しています', '暗号化した記録は、まだ公開されていません。LATTEがこのチャットで案内するまでお待ちください。'],
        network: ['記録を取得できませんでした', '通信状態やサイトの公開状況を確認して、もう一度お試しください。'],
        key: ['この専用リンクでは開けませんでした', 'リンクの鍵が違うか、記録を確認できません。LATTEから案内された最新の専用リンクを開いてください。'],
        crypto: ['このブラウザーでは開けません', 'HTTPSのサイトを、最新版のSafariまたはChromeで開いてください。'],
        snapshot: ['記録の形式を確認できませんでした', '記録を安全に表示できないため開きませんでした。LATTEにこの表示を伝えてください。'],
        format: ['配信された記録を確認できませんでした', '暗号化データの形式を確認できません。しばらくして更新するか、LATTEにこの表示を伝えてください。']
      };
      const [title, detail] = messages[error.message] || messages.format;
      if (snapshot) {
        $('#sync-badge').textContent = '最新の取得に失敗';
        showNotice(`${title}。${detail} 今は${formatDate(lastFetched.toISOString(), true)}に取得した記録を表示しています。`, true);
      } else locked(title, detail, ['network', 'missing', 'format', 'snapshot'].includes(error.message));
    } finally {
      if (generation === requestGeneration) {
        loading = false; activeRequest = null; $('#refresh').disabled = false; $('#locked-retry').disabled = false;
      }
    }
  }

  function clearPrivateDisplay() {
    requestGeneration += 1;
    activeRequest?.abort(); activeRequest = null; loading = false;
    snapshot = null; lastFetched = null;
    $('#refresh').disabled = false; $('#locked-retry').disabled = false;
    for (const selector of ['#next-focus', '#overview-proposals', '#recent-decisions', '#recent-activities', '#conversation-list', '#proposal-list', '#document-list', '#preference-list', '#fact-list']) $(selector).replaceChildren();
    $('#conversation-search').value = ''; $('#workspace-notice').textContent = ''; $('#coverage-description').textContent = ''; $('#coverage-meta').textContent = '';
    $('#last-updated').textContent = ''; $('#conversation-count').textContent = ''; $('#revision').textContent = '';
    for (const selector of ['#stat-decisions', '#stat-documents', '#stat-applications']) $(selector).replaceChildren(document.createTextNode('0'), node('small', '', '件'));
  }

  function disconnect() {
    if (!window.confirm('この端末の接続を解除しますか？ 再び開くには専用リンクが必要です。')) return;
    let removed = true;
    try { localStorage.removeItem(storageKey); } catch (_) { removed = false; }
    keyValue = null;
    clearPrivateDisplay();
    locked(removed ? 'この端末の接続を解除しました' : '現在の記録表示を閉じました', removed ? 'もう一度記録を見るときは、このチャットで渡した専用リンクを開いてください。' : 'ブラウザーに保存された鍵の削除は確認できませんでした。完全に接続を消すには、このサイトのブラウザーデータを削除してください。');
    window.scrollTo({ top: 0 });
  }

  $$('.nav-item').forEach(button => button.addEventListener('click', () => { navigate(button.dataset.view); window.scrollTo({ top: 0 }); }));
  $$('[data-go]').forEach(button => button.addEventListener('click', () => navigate(button.dataset.go)));
  $('#coverage-link').addEventListener('click', () => navigate('conversations'));
  $('#conversation-search').addEventListener('input', () => { if (snapshot) renderConversations(); });
  $$('.filter-row button').forEach(button => button.addEventListener('click', () => { filter = button.dataset.filter; renderProposalList(); }));
  $('#refresh').addEventListener('click', retrieve); $('#locked-retry').addEventListener('click', retrieve); $('#disconnect').addEventListener('click', disconnect);
  document.addEventListener('visibilitychange', () => { if (!demoMode && !document.hidden && keyValue && lastFetched && Date.now() - lastFetched.getTime() > 60000) retrieve(); });
  setInterval(() => { if (!demoMode && !document.hidden && keyValue) retrieve(); }, 120000);

  function consumePrivateFragment() {
    const provided = new URLSearchParams(location.hash.slice(1)).get('key');
    // A private URL's fragment never goes to the server. Remove it from the address bar immediately.
    if (location.hash) history.replaceState(null, '', location.pathname + location.search);
    return provided;
  }
  const provided = consumePrivateFragment();
  if (demoMode) keyValue = btoa(String.fromCharCode(...Array.from({ length: 32 }, (_, index) => index))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  else if (provided) keyValue = provided;
  else { try { keyValue = localStorage.getItem(storageKey); } catch (_) { keyValue = null; } }
  if (keyValue) retrieve();
  window.addEventListener('hashchange', () => {
    const replacement = consumePrivateFragment();
    if (!demoMode && replacement) {
      clearPrivateDisplay(); keyValue = replacement; retrieve();
    }
  });
})();
