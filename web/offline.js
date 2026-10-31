/* Standalone edition. No network requests, credentials, or external libraries. */
'use strict';
(() => {
  const storageKey = 'latte.personal.workspace.v1:' + location.pathname;
  const clone = value => JSON.parse(JSON.stringify(value));
  const stamp = () => new Date().toISOString();
  const id = () => 'local-' + crypto.randomUUID();
  let data;
  let loadError;
  const fields = ['profile', 'jobs', 'tasks', 'drafts', 'messages', 'checkins'];
  function validate(value) {
    if (!value || typeof value !== 'object' || !value.profile || !Array.isArray(value.profile.facts) || !value.profile.preferences) throw new Error('LATTEのバックアップではありません。');
    for (const key of fields.slice(1)) if (!Array.isArray(value[key])) throw new Error('バックアップの形式が違います。');
    for (const f of value.profile.facts) if (!f || typeof f.key !== 'string' || typeof f.label !== 'string' || typeof f.value !== 'string') throw new Error('記憶データの形式が違います。');
    for (const j of value.jobs) if (!j || typeof j.id !== 'string' || typeof j.title !== 'string' || !['review','preparing','archived'].includes(j.status)) throw new Error('求人データの形式が違います。');
    for (const t of value.tasks) if (!t || typeof t.id !== 'string' || typeof t.title !== 'string' || !['todo','done','cancelled'].includes(t.status)) throw new Error('タスクデータの形式が違います。');
    for (const d of value.drafts) if (!d || typeof d.id !== 'string' || typeof d.body !== 'string') throw new Error('下書きの形式が違います。');
    for (const m of value.messages) if (!m || !['assistant','user'].includes(m.role) || typeof m.content !== 'string') throw new Error('会話の形式が違います。');
    for (const c of value.checkins) if (!c || !['low','okay','good'].includes(c.energy)) throw new Error('調子の記録形式が違います。');
    return Object.fromEntries(fields.map(k => [k, clone(value[k])]));
  }
  function persist(value) {
    try { localStorage.setItem(storageKey, JSON.stringify(value)); }
    catch (_) { throw new Error('ブラウザーに保存できません。通常のChromeかEdgeで開き、空き容量と保存の設定を確認してください。変更は保存されていません。'); }
  }
  try {
    const saved = localStorage.getItem(storageKey);
    data = validate(JSON.parse(saved === null ? document.getElementById('latte-seed').textContent : saved));
    persist(data);
  } catch (error) { loadError = error; }

  function text(body, key, limit = 4000, required = false) {
    const value = body[key] == null ? '' : body[key];
    if (typeof value !== 'string' || value.trim().length > limit || (required && !value.trim())) throw new Error(`${key}の入力内容を確認してください。`);
    return value.trim();
  }
  function number(body, key, max) {
    const value = body[key];
    if (value == null || value === '') return null;
    if (typeof value !== 'number' || !Number.isInteger(value) || value < 0 || value > max) throw new Error(`${key}の数値を確認してください。`);
    return value;
  }
  function preferences(body) {
    return {location:text(body,'location',150), employment:text(body,'employment',150), commute_minutes:number(body,'commute_minutes',300), remote_days:number(body,'remote_days',5), min_monthly_salary:number(body,'min_monthly_salary',10000000), overtime_hours:number(body,'overtime_hours',200)};
  }
  function evaluate(j, p) {
    const reasons = [], unknowns = [], cautions = [];
    let mismatch = false;
    if (!j.description) unknowns.push('求人本文が未登録です。');
    if (j.creative_ratio == null) unknowns.push('実際に制作を担当する割合は未確認です。');
    else if (j.creative_ratio >= 50) reasons.push(`登録した制作割合は${j.creative_ratio}%です。`);
    else cautions.push(`制作割合は${j.creative_ratio}%です。希望との確認が必要です。`);
    if (j.remote_days == null) unknowns.push('在宅日数と制度の適用条件は未確認です。');
    else if (p.remote_days == null) unknowns.push(`登録情報では在宅週${j.remote_days}日。希望の最低日数は未設定です。`);
    else if (j.remote_days < p.remote_days) { mismatch = true; cautions.push('在宅日数が最低希望を下回っています。'); }
    else reasons.push('登録された在宅日数は最低希望を満たしています。');
    if (j.salary_min == null) unknowns.push('月額額面給与の下限は未確認です。手取りとは比較しません。');
    else if (p.min_monthly_salary == null) unknowns.push('希望の月額額面給与は未設定です。');
    else if (j.salary_min >= p.min_monthly_salary) reasons.push('登録された月額額面給与の下限は最低希望を満たしています。');
    else if (j.salary_max != null && j.salary_max < p.min_monthly_salary) { mismatch = true; cautions.push('給与上限が最低希望を下回っています。'); }
    else cautions.push('給与下限が最低希望を下回っています。個別提示額の確認が必要です。');
    if (!j.location || p.location || p.commute_minutes != null) unknowns.push('勤務地・実際の通勤経路や時間は未照合です。');
    if (!j.employment || p.employment) unknowns.push('雇用形態と希望の照合が必要です。');
    if (p.overtime_hours != null) unknowns.push('残業時間が希望の上限以内か確認が必要です。');
    const fit = mismatch ? 'mismatch' : unknowns.length || cautions.length ? 'check' : 'promising';
    cautions.push('本人登録の情報です。掲載元の内容・募集継続は未確認です。');
    return {fit,reasons,unknowns,cautions};
  }
  function snapshot() {
    const result = clone(data);
    result.jobs.forEach(j => { j.evaluation = evaluate(j, result.profile.preferences); });
    const rank = {promising:0,check:1,mismatch:2};
    result.jobs.sort((a,b) => Number(a.status === 'archived') - Number(b.status === 'archived') || rank[a.evaluation.fit] - rank[b.evaluation.fit]);
    result.connection = {mode:'guide', label:'このブラウザーに保存', configured:false,consent:false,verified:false, detail:'記憶・求人・準備ノートはこのブラウザーに保存します。外部AIやサーバーに情報を送信しません。自由な相談はChatGPTのLATTEへ。機種変更やブラウザーのデータ削除に備え、バックアップを保存してください。'};
    result.stats = {jobs:result.jobs.filter(j=>j.status!=='archived').length,tasks:result.tasks.filter(t=>t.status==='todo').length,drafts:result.drafts.length};
    return result;
  }
  function memory(s, label, value) {
    s.profile.facts.push({key:id(),label,value,status:'reported',source:'このアプリでの本人の入力'});
  }
  function guide(message,s) {
    if (/疲れ|しんど|つら|不安/.test(message)) return '今日は、ここに気になることを置いておくだけでも大丈夫。ゆっくり話す続きはChatGPTのLATTEに聞かせてね。ここではメモと準備を残せるよ。';
    if (/強み|経験/.test(message)) {
      const facts=s.profile.facts.filter(f=>/experience|skill|leadership|client/.test(f.key));
      return '本人から聞いた経験を整理したよ。\n'+(facts.map(f=>`・${f.label}：${f.value}`).join('\n') || 'まだ経験のメモがありません。')+'\n提出前には内容と公開許可を一緒に確認しよう。';
    }
    if (/覚え|私の|状況|条件/.test(message)) return '記憶の一部を確認したよ。\n'+s.profile.facts.slice(0,5).map(f=>`・${f.label}：${f.value}`).join('\n')+'\n一覧は「覚えていること」で確認・削除できるよ。';
    return 'メッセージをこのブラウザーに保存したよ。ここは記憶と転職準備の作業ノートで、自由に話すAIではありません。\n覚えておきたいことは「覚えて：…」で残せるよ。気になる求人は「仕事の候補」から登録して、一緒に条件を整理しよう。相談の続きはChatGPTのLATTEへ。';
  }
  async function request(path, body) {
    if (loadError) throw loadError;
    if (body === undefined && path === '/api/state') return snapshot();
    if (!body || typeof body !== 'object') throw new Error('入力を確認してください。');
    const next = clone(data);
    let response;
    const find = (items,key) => { const item=items.find(x=>x.id===key); if(!item) throw new Error('対象の記録が見つかりません。'); return item; };
    let match;
    if (path === '/api/preferences') next.profile.preferences = preferences({...next.profile.preferences,...body});
    else if (path === '/api/memories') memory(next,text(body,'label',100,true),text(body,'value',3000,true));
    else if ((match = path.match(/^\/api\/memories\/([\w-]+)\/delete$/))) {
      if (!next.profile.facts.some(f=>f.key===match[1])) throw new Error('記憶が見つかりません。');
      next.profile.facts=next.profile.facts.filter(f=>f.key!==match[1]);
    } else if (path === '/api/jobs') {
      const job={id:id(),created_at:stamp(),status:'review'};
      for (const key of ['title','company','url','description','location','employment']) job[key]=text(body,key,key==='description'?30000:key==='url'?2048:['location','employment'].includes(key)?150:200,key==='title');
      for (const [key,max] of [['remote_days',5],['creative_ratio',100],['salary_min',10000000],['salary_max',10000000]]) job[key]=number(body,key,max);
      if (job.url) { let u; try { u=new URL(job.url); } catch (_) { throw new Error('掲載元URLを確認してください。'); } if(!['http:','https:'].includes(u.protocol)||u.username||u.password) throw new Error('掲載元URLは認証情報を含まないhttpかhttpsにしてください。'); }
      if(job.salary_min!=null&&job.salary_max!=null&&job.salary_min>job.salary_max) throw new Error('給与の下限が上限を超えています。');
      next.jobs.push(job);
    } else if ((match=path.match(/^\/api\/jobs\/([\w-]+)\/(prepare|archive)$/))) {
      const j=find(next.jobs,match[1]);
      if(match[2]==='archive') { j.status='archived'; next.tasks.filter(t=>t.job_id===j.id).forEach(t=>{t.status='cancelled';}); }
      else {
        if(j.status==='archived') throw new Error('保留した候補です。');
        j.status='preparing';
        if(!next.drafts.some(d=>d.job_id===j.id)) {
          const facts=next.profile.facts.filter(f=>['confirmed','reported'].includes(f.status)&&/experience|skill|achievement|leadership|client/.test(f.key));
          next.drafts.push({id:id(),job_id:j.id,title:`${j.company||j.title} への応募準備`,kind:'preparation',created_at:stamp(),body:`${j.company||'企業名未確認'} / ${j.title}\n\n応募準備メモ（未提出・本人確認前）\n\n【志望動機のたたき台】\n制作の実務に取り組み、自分の経験を活かせる仕事を探しています。貴社の業務と担当できる制作領域について、詳しく確認したいと考えています。\n※未確認の企業理解や実績は書き足していません。\n\n【本人申告の経験（提出前に要確認）】\n${facts.map(f=>`・${f.label}：${f.value}`).join('\n')||'公開可能な経験・実績は未登録です。'}\n\n【確認すること】\n・実際の制作割合\n・在宅制度と給与の条件\n・作品の公開許可と職歴の正確性\n\n登録した出典：${j.url||'未登録'}\n本人入力の情報です。掲載元の確認・応募・外部送信はしていません。`});
          for (const title of ['制作割合・在宅・給与の未確認点を調べる','公開許可のある作品を選ぶ','応募書類を本人が確認する']) next.tasks.push({id:id(),job_id:j.id,title,status:'todo',created_at:stamp()});
        }
      }
    } else if ((match=path.match(/^\/api\/tasks\/([\w-]+)\/toggle$/))) {
      const t=find(next.tasks,match[1]); if(t.status==='cancelled') throw new Error('保留中のタスクは変更できません。'); t.status=t.status==='done'?'todo':'done';
    } else if (path === '/api/checkins') {
      if(!['low','okay','good'].includes(body.energy)) throw new Error('今日の調子を選んでください。');
      next.checkins.push({id:id(),energy:body.energy,note:text(body,'note',1000),created_at:stamp()});
      next.messages.push({id:id(),role:'assistant',created_at:stamp(),content:{low:'今日は、休むのも大事な一歩。ここでは気になることを置いておくだけでも大丈夫。',okay:'いつものペースで。準備はひとつずつにしよう。',good:'余裕がある日だね。気になることをひとつだけ進めよう。'}[body.energy]});
    } else if (path === '/api/chat') {
      const content=text(body,'message',5000,true); let reply;
      next.messages.push({id:id(),role:'user',content,created_at:stamp()});
      const remembered=content.match(/^覚えて[：:]\s*([\s\S]+)$/);
      if(remembered) { const value=text({value:remembered[1]},'value',3000,true); memory(next,value.slice(0,24),value); reply='覚えたよ。このブラウザーに、あなたから聞いたこととして保存しました。「覚えていること」で確認・削除できるよ。'; }
      else reply=guide(content,next);
      const message={id:id(),role:'assistant',content:reply,created_at:stamp()}; next.messages.push(message); response={message,mode:'guide'};
    } else throw new Error('この保存版では、その操作を利用できません。');
    persist(next); data=next;
    return response || snapshot();
  }
  window.LATTE_OFFLINE={request};
  document.addEventListener('DOMContentLoaded',()=>{
    document.getElementById('export-backup').addEventListener('click',()=>{
      if(loadError) { alert(loadError.message); return; }
      const blob=new Blob([JSON.stringify({format:'LATTE-backup-v1',...data},null,2)],{type:'application/json'});
      const url=URL.createObjectURL(blob), a=document.createElement('a'); a.href=url; a.download='LATTE-backup-'+new Date().toLocaleDateString('sv-SE',{timeZone:'Asia/Tokyo'})+'.json'; a.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
    });
    document.getElementById('import-backup').addEventListener('change',async event=>{
      const file=event.target.files[0]; if(!file) return;
      try {
        if(file.size>10000000) throw new Error('10MB以下のバックアップを選んでください。');
        const restored=validate(JSON.parse(await file.text()));
        if(!confirm('現在のブラウザーの記録を、このバックアップに置き換えます。続けますか？')) return;
        persist(restored); data=restored; loadError=null; location.reload();
      } catch(error) { alert(error.message); }
      finally { event.target.value=''; }
    });
  });
})();
