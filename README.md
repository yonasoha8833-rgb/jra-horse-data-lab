# JRA HORSE DATA LAB

スマートフォン向けJRA公式成績データ分析プロジェクト。

初期版は公式成績PDFリンクの収集と表示までです。馬別成績の基本集計と公式PDFの初期取り込み処理を実装中です。ラップ・調教データの本格的な構造化は未実装です。

JRA公式のレースラップはレース全体のラップであり、各馬個別の1Fラップではありません。推定値と実測値を区別します。

## GitHub Pages
Settings → Pages → Build and deployment → Deploy from a branch → main / (root) → Save

## 馬別集計モジュール（追加）

`race_analysis.py` は、PDF抽出処理が出力したレース結果レコードを馬名単位で集計します。

### 入力レコードの主なキー

- `horse_name`: 馬名（必須）
- `horse_registration_number`: JRA等の公式登録番号（取得できる場合）。馬別集計はこの番号を優先し、未取得時は馬名による仮集計として扱います。
- `date`: 開催日（YYYY-MM-DD）
- `track`: 競馬場
- `surface`: 芝・ダート
- `distance`: 距離（m）
- `finish`: 着順（着順を付与できない場合は `null`）
- `result_status`: 公式PDFの行末で確認できた結果状態（競走中止・失格・中止・取消・除外など）
- `declared_field_size`: PDFに記載された出走頭数（読み取れた場合）
- `race_validation_warnings`: 馬番重複・記載頭数との不一致などの検証警告
- `finish_time_seconds`: 走破タイム（秒、公式PDF取り込み形式）
- `individual_last3f`: 個別馬の上がり3F（公式値がある場合のみ）
- `race_last3f`: レース全体の上がり3F（個別馬の値とは別項目）
- `time_seconds` / `last3f`: 旧形式データとの互換用キー
- `corner_positions`: コーナー通過順位
- `jockey`: 騎手

欠損値や数値に変換できない値は平均計算から除外し、推測で補完しません。馬別集計には出走数・着順記録数・着順なしの走数・結果状態別件数も含まれます。画面では馬名・競馬場・芝/ダート・距離による絞り込みと、出走数・勝利数・勝率・参考指数による並べ替えができます。各馬カードには競馬場別、芝/ダートと距離の組み合わせ別に、出走数・勝利数・勝率・複勝回数を折りたたみ表示します。比較対象に最大3頭を選ぶと、全取り込み記録を使って出走数・勝利数・勝率・複勝率・参考指数を横並びで比較できます。着順なしには中止等のほか、抽出できなかった行も含まれるため、必ずしも全件が競走中止を意味するわけではありません。

### 利用例

```python
import json
from race_analysis import summarize_horses, filter_records

with open("data/race_results.json", encoding="utf-8") as f:
    records = json.load(f)

tokyo_mile = filter_records(records, track="東京", surface="芝", distance=1600)
print(json.dumps(summarize_horses(tokyo_mile), ensure_ascii=False, indent=2))
```

### テスト

```bash
python -m unittest discover -s tests -v
```

GitHub Actions の `Python tests` が push / pull request 時に自動実行します。

※ 現段階では集計エンジンとテストを追加した段階です。JRA PDFから上記キーを安定して抽出し、公式値と照合する処理は次の開発工程です。

## JRA公式PDFの馬別データ取り込み

PDF本文の読み取りには Poppler の `pdftotext` を使用します。ローカル環境では Poppler をインストールしたうえで実行してください。

```bash
python -m unittest discover -s tests -v
python import_results.py --limit 1
```

GitHub上では Actions → **Import JRA race results** → Run workflow から実行できます。最初は1 PDFずつ取り込んで抽出結果を確認してください。入力の `limit` を増やすと複数PDFを処理します。

取り込み先は `data/race_results.json` です。画面の「データ品質の詳細を表示」から、集計レース数、警告レース数、馬番重複、頭数不一致、日付・競馬場・距離・走破タイムの欠損、不正な走破タイムを確認できます。品質レポートは警告フィールドが欠損・不正形式の場合も安全に集計します。重複取り込みはPDF URL・競走番号・馬番で抑制します。`import_failures` には失敗URL・試行回数・最終試行日時・エラー内容を保存し、失敗または抽出0件のPDFは処理済みにせず次回実行で再試行します。`quality_report` には競走数・検証警告のある競走数・馬番重複・頭数不一致・日付/競馬場/距離/走破タイムの欠損件数と、秒数が不正な走破タイムの件数を集計します。未試行PDFを失敗済みPDFより優先するため、1件の失敗が後続PDFの取り込みを妨げません。Actionsの取り込み処理は、PDFを取得する前にユニットテストを実行します。

### データ精度に関する注意

- 着順は走破タイムを抽出できた馬行の掲載順で付与します。タイムがない馬行は着順を `null` とし、後続馬の着順をずらしません。行末に明記された「中止」「競走中止」「失格」「取消」「除外」は `result_status` に保存し、失格等にタイムが記載されていても着順を付与しません。馬主名などの途中に同じ文字列が含まれるだけでは状態と判定しません。同着・取消・除外などの詳細な区別は今後の検証対象です。
- `race_last3f` はレース全体の上がり3Fです。個別馬の上がり3Fではありません。
- `individual_last3f` は公式資料から個別値を確認できるまで `null` のままです。
- 距離は `ｍ`・`m`・`メートル` の単位表記を伴う数値だけを採用し、1,000〜5,000mの範囲外は欠損扱いにします。開催年などの数字を距離と誤認しないようにしています。開催日は年・月・日の実在する暦日か検証し、2月30日などは欠損扱いにします。馬行は枠番1〜8、馬番1〜18、年齢2〜20歳の範囲外を除外し、文字抽出の異常値が集計に混ざらないようにします。レース番号は1〜12のみ採用します。走破タイムの秒部分が60以上の場合はタイムを欠損扱いにして `runner_validation_warnings` に記録します。レース内の馬番重複や、PDF記載頭数と抽出馬行数の不一致は `race_validation_warnings` に記録し、元の行は確認用に保持します。初期パーサーは基本項目を抽出する段階です。全競馬場・全ページでの精度検証後に本格集計へ接続します。
