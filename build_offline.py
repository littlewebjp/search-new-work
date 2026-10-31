#!/usr/bin/env python3
"""Build a private, self-contained, downloadable LATTE edition. No uploads."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile, ZIP_DEFLATED
from latte import Store, ROOT, PREFS

GUIDE = """LATTE はじめかた（パソコン版）

1. ZIPの場合は先に「すべて展開」または解凍してください。
2. LATTE.html を Chrome または Edge で開いてください。
   通常はダブルクリックで開けます。文字だけが出たら右クリック→プログラムから開く→Chrome/Edge。
3. 「覚えていること」に引き継ぎ情報が入っています。
   「仕事の候補」から求人の本文を登録し、比較・準備メモを作れます。

インストール・GitHub操作・サーバー起動・追加契約は不要です。
日々の自由な相談は、いつものChatGPTでLATTEに話しかけてください。
この保存版は記憶と準備のノートで、生成AIの自由会話や求人の自動検索は行いません。
ChatGPTの会話とは自動同期しません。

保存は同じPC・同じブラウザー・同じ場所のLATTE.htmlで行ってください。
ブラウザーのデータ削除・プライベートモード・ファイル移動で記録を失うことがあります。
「覚えていること」→「バックアップを保存」で控えを作れます。
別のPCへ移す場合は、そのバックアップを「バックアップから戻す」で読み込みます。
保存できない場合は画面にエラーを表示します。成功したふりはしません。

このHTML自体に個人用の引き継ぎ情報が含まれます。他人に渡したり公開したりしないでください。
アプリからプロフィールや記録を外部へ送信する機能はありません。
自分で「掲載元を開く」を押した場合は、その外部サイトが開きます。
スマートフォンではHTMLファイルを直接操作できない機種・アプリがあります。
"""

def build(data_dir, output_dir, public_site=False):
    # Public builds must never read the personal store.
    state = ({'profile':{'facts':[], 'preferences':dict(PREFS)},
              'jobs':[], 'tasks':[], 'drafts':[], 'messages':[], 'checkins':[]}
             if public_site else Store(data_dir).state())
    seed = {key: state[key] for key in ['profile','jobs','tasks','drafts','messages','checkins']}
    encoded = json.dumps(seed, ensure_ascii=False).replace('<', '\\u003c').replace('>', '\\u003e').replace('&', '\\u0026')
    html = (ROOT/'web/index.html').read_text()
    css = (ROOT/'web/styles.css').read_text()
    offline = (ROOT/'web/offline.js').read_text()
    app = (ROOT/'web/app.js').read_text()
    policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'"
    html = html.replace('<link rel="stylesheet" href="/styles.css">', f'<meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="{policy}"><style>{css}</style>')
    html = html.replace('<script src="/app.js" defer></script>', '')
    backup = '''<section class="surface" style="margin-top:22px"><h2>記録のバックアップ</h2><p class="section-description">このパソコンの同じブラウザーに保存します。ファイルの移動やブラウザーデータ削除に備え、控えを残してください。バックアップには個人情報が含まれます。</p><button id="export-backup" class="primary-button">バックアップを保存</button><label class="text-button" style="display:block;margin-top:16px">バックアップから戻す<input id="import-backup" type="file" accept="application/json,.json" style="display:block;margin-top:8px;max-width:100%"></label></section>'''
    html = html.replace('<section class="surface connection-surface">', backup+'<section class="surface connection-surface">')
    html = html.replace('<body>', '<body><div style="padding:12px 20px;background:#344b3c;color:white;font-size:13px;line-height:1.8">LATTE｜保存して使うパソコン版　<span>記憶と準備はこのブラウザーに保存。自由な相談はChatGPTへ。</span></div>')
    if public_site:
        html = html.replace('保存して使うパソコン版', 'あなたの転職準備ノート')
        html = html.replace('このパソコンの同じブラウザーに保存します。', 'このスマートフォンの同じブラウザーに保存します。')
        html = html.replace('<div id="load-error"', '<section class="surface" style="margin-bottom:24px"><h2>はじめて使うとき</h2><p class="section-description">個人情報はこのサイトには入っていません。「覚えていること」から、手元の引き継ぎファイルを読み込めます。入力した記録はこのブラウザーだけに保存します。</p><a class="text-button" href="#profile">引き継ぎファイルを読み込む →</a></section><div id="load-error"')
    scripts = '<script type="application/json" id="latte-seed">'+encoded+'</script>\n<script>'+offline.replace('</script','<\\/script')+'</script>\n<script>'+app.replace('</script','<\\/script')+'</script>'
    html = html.replace('</body>', scripts+'\n</body>')
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    if public_site:
        (destination/'index.html').write_text(html, encoding='utf-8')
        (destination/'.nojekyll').write_text('', encoding='utf-8')
        return destination
    (destination/'LATTE.html').write_text(html, encoding='utf-8')
    (destination/'はじめに.txt').write_text(GUIDE, encoding='utf-8')
    with ZipFile(destination/'LATTE.zip','w',ZIP_DEFLATED) as archive:
        archive.write(destination/'LATTE.html','LATTE/LATTE.html')
        archive.write(destination/'はじめに.txt','LATTE/はじめに.txt')
    return destination

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir',default=str(ROOT/'.latte'))
    parser.add_argument('--output-dir',default=str(ROOT/'.latte/delivery'))
    parser.add_argument('--public-site',action='store_true',help='Build a generic static site without reading or embedding personal data')
    args=parser.parse_args()
    result=build(args.data_dir,args.output_dir,args.public_site)
    print(f'{"Generic public site" if args.public_site else "Private downloadable edition"} created in {result}.')
