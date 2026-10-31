'use strict';

(() => {
  const $ = selector => document.querySelector(selector);
  const storageKey = 'latte.dashboard.key.v1';
  const demoMode = new URLSearchParams(location.search).get('demo') === '1';
  const statuses = {
    new_proposal: '新しい提案', awaiting_decision: 'あなたの判断待ち', approved: '方針決定・準備前',
    preparing: '準備中', ready_for_review: '内容の確認待ち', waiting_to_apply: '応募待ち', application_queued: '応募待ち',
    applied: '応募中', deferred: '保留中'
  };
  const groups = ['new', 'decision', 'deferred', 'applied'];
  let snapshot = null;
  let keyValue = null;
  let loading = false;
  let lastFetched = null;
  let requestGeneration = 0;
  let activeRequest = null;

  function node(tag, className, content) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (content != null) element.textContent = String(content);
    return element;
  }

  function formatDate(value) {
    if (!value) return '日時未記録';
    if (/^\d{4}-\d{2}-\d{2}$/.test(value)) {
      const [year, month, day] = value.split('-').map(Number);
      return `${year}/${month}/${day}`;
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return '日時未記録';
    return new Intl.DateTimeFormat('ja-JP', { timeZone: 'Asia/Tokyo', year: 'numeric', month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(date);
  }

  function safeURL(value) {
    if (typeof value !== 'string' || !value || /\s/.test(value)) return null;
    try {
      const url = new URL(value);
      return ['https:', 'http:'].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch (_) { return null; }
  }

  function validApplication(proposal) {
    return proposal.kind === 'job' && proposal.packet_ready === true && !!safeURL(proposal.job?.source_url) && !!proposal.job?.verified_at;
  }

  function validQueuedApplication(proposal) {
    const decision = proposal.user_decision;
    if (proposal.kind !== 'job' || !safeURL(proposal.job?.source_url) || !proposal.job?.verified_at || !decision || decision.choice !== 'yes' || !decision.quote) return false;
    const conversation = snapshot.conversations.find(item => item.id === decision.conversation_id);
    return !!conversation && typeof conversation.user_text === 'string' && conversation.user_text.includes(decision.quote);
  }

  function isConfirmedApplied(proposal) {
    return validApplication(proposal) && !!proposal.submission_confirmed_at;
  }

  function groupFor(proposal) {
    if (Object.hasOwn(proposal, 'display_group') && proposal.display_group === null) return null;
    // A malformed application claim must never appear as an actual application.
    if (proposal.status === 'applied' && !isConfirmedApplied(proposal)) return 'decision';
    if (proposal.status === 'application_queued' && !validQueuedApplication(proposal)) return 'decision';
    if (proposal.display_group) {
      if (proposal.display_group === 'applied' && !((proposal.status === 'applied' && isConfirmedApplied(proposal)) || (proposal.status === 'application_queued' && validQueuedApplication(proposal)) || (proposal.status === 'waiting_to_apply' && validApplication(proposal)))) return 'decision';
      return proposal.display_group;
    }
    if (proposal.status === 'new_proposal') return 'new';
    if (proposal.status === 'deferred') return 'deferred';
    if (proposal.status === 'applied') return 'applied';
    if (proposal.status === 'application_queued') return 'applied';
    return 'decision';
  }

  function statusFor(proposal) {
    if (proposal.status === 'applied' && !isConfirmedApplied(proposal)) return '応募状況の確認が必要';
    if (proposal.status === 'application_queued' && !validQueuedApplication(proposal)) return '応募意思の確認が必要';
    if (proposal.status === 'waiting_to_apply' && !validApplication(proposal)) return '応募条件の確認が必要';
    return proposal.dashboard?.status || statuses[proposal.status];
  }

  function field(label, value, name) {
    const element = node('dl', `record-field record-${name}`);
    element.append(node('dt', '', label), node('dd', '', value));
    return element;
  }

  function row(proposal) {
    const display = proposal.dashboard || {};
    const element = node('article', 'record-row');
    element.dataset.proposalId = proposal.id;
    const description = display.description || proposal.job?.description || proposal.title;
    const location = display.location || proposal.job?.location || proposal.location || '未確認';
    element.append(field('勤務地', location, 'location'), field('仕事内容', description, 'description'), field('今どうなっているか', statusFor(proposal), 'status'));
    const url = safeURL(display.detail_url || proposal.job?.source_url);
    if (url) {
      const link = node('a', 'record-link', '詳細 ↗');
      link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer';
      link.setAttribute('aria-label', `${description}の詳細URLを開く`);
      element.append(link);
    }
    return element;
  }

  function renderAll() {
    for (const group of groups) {
      const list = $(`#list-${group}`);
      list.replaceChildren();
      const records = snapshot.proposals.filter(proposal => groupFor(proposal) === group);
      if (!records.length) list.append(node('p', 'empty-state', 'まだありません'));
      records.forEach(proposal => list.append(row(proposal)));
    }
    $('#last-updated').textContent = `最終更新 ${formatDate(snapshot.updated_at)}`;
  }

  function showNotice(message, error = false) {
    const notice = $('#workspace-notice');
    notice.textContent = message;
    notice.classList.toggle('error', error);
    notice.hidden = !message;
  }

  function locked(title, detail, retry = false) {
    $('#locked-screen').hidden = false;
    $('#workspace').hidden = true;
    $('#locked-status-title').textContent = title;
    $('#locked-status-detail').textContent = detail;
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
      if (Object.hasOwn(item, 'display_group') && item.display_group !== null && !groups.includes(item.display_group)) fail();
      string(item, 'location');
      if (item.dashboard != null) {
        if (!object(item.dashboard)) fail();
        for (const key of ['location', 'description', 'status', 'detail_url']) string(item.dashboard, key);
      }
      if (item.packet_ready != null && typeof item.packet_ready !== 'boolean') fail();
      if (item.user_decision != null) {
        if (!object(item.user_decision)) fail();
        for (const key of ['choice', 'conversation_id', 'quote']) string(item.user_decision, key, true);
      }
      if (item.job != null) { if (!object(item.job)) fail(); for (const key of ['source_url', 'verified_at', 'location', 'description']) string(item.job, key); }
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
    loading = true;
    $('#refresh').disabled = true;
    $('#locked-retry').disabled = true;
    if (!snapshot) locked('記録を開いています', 'この端末で記録を復号しています。');
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
      } catch (_) { throw new Error('snapshot'); }
      if (generation !== requestGeneration || requestedKey !== keyValue) return;
      snapshot = decrypted;
      lastFetched = new Date();
      let keySaved = true;
      if (!demoMode) { try { localStorage.setItem(storageKey, requestedKey); } catch (_) { keySaved = false; } }
      $('#locked-screen').hidden = true;
      $('#workspace').hidden = false;
      $('#demo-banner').hidden = !demoMode;
      $('#disconnect').hidden = demoMode;
      renderAll();
      showNotice(keySaved ? '' : 'この端末の接続を保存できません。次回は専用リンクから開いてください。');
    } catch (error) {
      if (generation !== requestGeneration || requestedKey !== keyValue) return;
      const messages = {
        missing: ['記録の反映を準備しています', 'LATTEからの反映をお待ちください。'],
        network: ['記録を取得できませんでした', '通信状態を確認して、もう一度お試しください。'],
        key: ['この専用リンクでは開けませんでした', 'このチャットでお渡しした最新の専用リンクを開いてください。'],
        crypto: ['このブラウザーでは開けません', 'HTTPSのサイトを、最新版のSafariまたはChromeで開いてください。'],
        snapshot: ['記録を確認できませんでした', '安全に表示できないため開きませんでした。この表示をLATTEへ伝えてください。'],
        format: ['記録を確認できませんでした', 'しばらくして更新するか、この表示をLATTEへ伝えてください。']
      };
      const [title, detail] = messages[error.message] || messages.format;
      if (snapshot) showNotice(`更新できませんでした。${formatDate(lastFetched.toISOString())}に取得した記録を表示しています。`, true);
      else locked(title, detail, ['network', 'missing', 'format', 'snapshot'].includes(error.message));
    } finally {
      if (generation === requestGeneration) {
        loading = false;
        activeRequest = null;
        $('#refresh').disabled = false;
        $('#locked-retry').disabled = false;
      }
    }
  }

  function clearPrivateDisplay() {
    requestGeneration += 1;
    activeRequest?.abort(); activeRequest = null; loading = false;
    snapshot = null; lastFetched = null;
    $('#refresh').disabled = false; $('#locked-retry').disabled = false;
    groups.forEach(group => $(`#list-${group}`).replaceChildren());
    $('#workspace-notice').textContent = '';
    $('#workspace-notice').hidden = true;
    $('#last-updated').textContent = '';
  }

  function disconnect() {
    if (!window.confirm('この端末の接続を解除しますか？ 再び開くには専用リンクが必要です。')) return;
    let removed = true;
    try { localStorage.removeItem(storageKey); } catch (_) { removed = false; }
    keyValue = null;
    clearPrivateDisplay();
    locked(removed ? 'この端末の接続を解除しました' : '現在の表示を閉じました', removed ? '再び開くときは、このチャットで渡した専用リンクを使ってください。' : '保存された鍵の削除は確認できませんでした。接続を消すには、このサイトのブラウザーデータを削除してください。');
    window.scrollTo({ top: 0 });
  }

  $('#refresh').addEventListener('click', retrieve);
  $('#locked-retry').addEventListener('click', retrieve);
  $('#disconnect').addEventListener('click', disconnect);
  document.addEventListener('visibilitychange', () => { if (!demoMode && !document.hidden && keyValue && lastFetched && Date.now() - lastFetched.getTime() > 60000) retrieve(); });
  setInterval(() => { if (!demoMode && !document.hidden && keyValue) retrieve(); }, 120000);

  function consumePrivateFragment() {
    const provided = new URLSearchParams(location.hash.slice(1)).get('key');
    // Fragments are not sent in HTTP requests. Clear the private key from the address bar immediately.
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
