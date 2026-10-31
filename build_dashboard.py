#!/usr/bin/env python3
"""Build the generic dashboard and an explicitly fictional, public demonstration."""
import argparse
import base64
import json
import secrets
import shutil
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parent
STATIC_FILES = ('index.html', 'styles.css', 'app.js')


def demo_snapshot():
    snapshot = {
        'demo': True,
        'schema_version': 1, 'revision': 1, 'updated_at': '2026-10-31T03:00:00Z',
        'coverage': {'label': '架空データによる画面例', 'description': '機能を説明するための架空の会話です。あなたの記録ではありません。', 'last_recorded_at': '2026-10-31T03:00:00Z', 'record_count': 2},
        'profile': {'facts': [{'key': 'demo-focus', 'label': '仕事選びの軸（架空）', 'value': '制作に集中できる仕事を探したい。', 'status': 'reported', 'source': '架空の会話・機能説明用'}], 'preferences': {}},
        'conversations': [
            {'id': 'demo-talk-01', 'at': '2026-10-31', 'title': '探す方向を、一緒に絞る', 'kind': 'exchange', 'user_text': '制作に集中できる仕事を探したい。まず選択肢を整理したい。', 'assistant_text': '探す職種を少数に絞る案を用意します。実際の求人は、掲載内容を確認してから比較しましょう。', 'summary': '仕事選びの軸と、探し方の提案を整理した架空の会話。', 'tags': ['仕事選び', '提案のきっかけ'], 'source': '架空の会話（機能説明用）'},
            {'id': 'demo-talk-02', 'at': '2026-10-31', 'title': '経験を、準備メモにする', 'kind': 'exchange', 'user_text': '書類に書ける経験を先に整理して。', 'assistant_text': '経験の下書きを用意しました。提出前に本人の確認が必要です。まだ応募はしていません。', 'summary': '相談から下書きの準備に進んだ架空の会話。', 'tags': ['準備', '本人の判断'], 'source': '架空の会話（機能説明用）'},
        ],
        'proposals': [
            {'id': 'demo-approach', 'kind': 'approach', 'title': 'まず、探す方向を少数に絞る', 'status': 'awaiting_decision', 'summary': '求人を大量に見る前に、仕事内容の軸を整理する提案です。実在求人の紹介ではありません。', 'recommendation': '制作を担当できる割合を重視し、候補職種を比較するところから。', 'reasons': ['選択肢を整理したいという相談があったため。'], 'unknowns': ['勤務地や給与の最低条件は、この架空の会話では未確認です。'], 'next_action': '進め方について、いつものチャットで相談する。', 'evidence': [{'conversation_id': 'demo-talk-01', 'quote': 'まず選択肢を整理したい。', 'who': 'user'}]},
            {'id': 'demo-preparation', 'kind': 'document', 'title': '経験の下書きを確認する', 'status': 'ready_for_review', 'summary': '本人から聞いた内容を整理した下書きの例です。', 'recommendation': '内容を確認し、事実に合わない点をチャットで直す。', 'reasons': ['経験を先に整理する方針が決まったため。'], 'unknowns': ['具体的な年数や定量成果は未確認です。'], 'next_action': '下書きを読んで、チャットで修正点を伝える。', 'evidence': [{'conversation_id': 'demo-talk-02', 'quote': '書類に書ける経験を先に整理して。', 'who': 'user'}]},
        ],
        'decisions': [{'id': 'demo-decision', 'at': '2026-10-31', 'title': '経験の整理から始める', 'choice': '書類に使う経験を先に整理する。', 'reason': '架空の会話で本人が依頼したため。', 'conversation_ids': ['demo-talk-02'], 'proposal_ids': ['demo-preparation']}],
        'documents': [{'id': 'demo-document', 'title': '制作経験の整理メモ（架空の例）', 'status': 'ready_for_review', 'body': 'これは機能説明用の架空の下書きです。\n\n【本人から聞いた経験】\n・ここに、会話で確認した制作経験を整理します。\n\n【未確認】\n・経験年数\n・公開できる作品\n・定量的な成果\n\n未確認の実績は書き足しません。応募・外部送信はしていません。', 'conversation_ids': ['demo-talk-02'], 'proposal_id': 'demo-preparation'}],
        'activities': [{'id': 'demo-activity', 'at': '2026-10-31', 'kind': 'prepared', 'text': '会話をもとに、経験の下書きを用意（架空の例）。', 'conversation_id': 'demo-talk-02', 'proposal_id': 'demo-preparation'}],
    }
    # Keep detailed records available behind the dashboard, not on the paper view.
    for proposal in snapshot['proposals']:
        proposal['display_group'] = None
    snapshot['conversations'].append({'id':'demo-choice','at':'2026-10-31','title':'応募の方向を決める（架空）','kind':'exchange','user_text':'○ この求人は応募待ちにして。','assistant_text':'応募待ちとして記録します。まだ提出はしていません。','summary':'機能説明用の架空のやりとり。','tags':[],'source':'架空の会話（機能説明用）'})
    snapshot['coverage']['record_count'] = len(snapshot['conversations'])
    rows = [
        ('demo-new','new_proposal','new','東京都・在宅併用（架空）','EC向けの画像制作（架空の例）','新しく見つけた提案'),
        ('demo-question','awaiting_decision','decision','大阪府（架空）','SNS向けの動画編集（架空の例）','仕事内容について返答待ち'),
        ('demo-hold','deferred','deferred','出社条件を確認中（架空）','画像のレタッチ（架空の例）','出社日数の確認まで保留'),
        ('demo-queue','application_queued','applied','在宅併用（架空）','販促物のデザイン（架空の例）','応募待ち・書類を準備中'),
        ('demo-applied','applied','applied','東京都（架空）','Web画像の制作（架空の例）','応募済み・返答待ち'),
    ]
    for record_id,status,group,location,description,label in rows:
        item={'id':record_id,'kind':'job','title':description,'status':status,'display_group':group,
              'summary':'架空の画面例。実在求人ではありません。','recommendation':'','reasons':[],'unknowns':[],
              'next_action':'','evidence':[],
              'dashboard':{'location':location,'description':description,'status':label,'detail_url':'https://example.invalid/'+record_id},
              'job':{'source_url':'https://example.invalid/'+record_id,'verified_at':'2026-10-31T03:00:00Z','location':location,'description':description}}
        if status=='application_queued':
            item['user_decision']={'choice':'yes','conversation_id':'demo-choice','quote':'○ この求人は応募待ちにして。'}
        if status=='applied':
            item['packet_ready']=True
            item['submission_confirmed_at']='2026-10-31T03:00:00Z'
        snapshot['proposals'].append(item)
    return snapshot


def build(output_dir):
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    for filename in STATIC_FILES:
        shutil.copyfile(ROOT / 'dashboard' / filename, destination / filename)
    (destination / '.nojekyll').write_text('')
    # The public demo key is intentionally known; it never protects real records.
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(bytes(range(32))).encrypt(nonce, json.dumps(demo_snapshot(), ensure_ascii=False).encode(), b'LATTE workspace v1')
    envelope = {'format': 'latte-encrypted-v1', 'algorithm': 'AES-256-GCM', 'nonce': base64.b64encode(nonce).decode(), 'ciphertext': base64.b64encode(ciphertext).decode()}
    (destination / 'demo.enc.json').write_text(json.dumps(envelope), encoding='utf-8')
    return destination


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', default=str(ROOT / '.latte/dashboard-public'))
    args = parser.parse_args()
    print('Generic dashboard and fictional demo built:', build(args.output_dir))
