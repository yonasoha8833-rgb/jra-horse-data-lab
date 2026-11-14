# JRA HORSE DATA LAB

スマートフォン向けJRA公式成績データ分析プロジェクト。

初期版は公式成績PDFリンクの収集と表示までです。馬別成績・ラップ・調教データの構造化は未実装です。

JRA公式のレースラップはレース全体のラップであり、各馬個別の1Fラップではありません。推定値と実測値を区別します。

## GitHub Pages
Settings → Pages → Build and deployment → Deploy from a branch → main / (root) → Save

## 馬別集計モジュール（追加）

`race_analysis.py` は、PDF抽出処理が出力したレース結果レコードを馬名単位で集計します。

### 入力レコードの主なキー

- `horse_name`: 馬名（必須）
- `date`: 開催日（YYYY-MM-DD）
- `track`: 競馬場
- `surface`: 芝・ダート
- `distance`: 距離（m）
- `finish`: 着順
- `time_seconds`: 走破タイム（秒）
- `last3f`: 上がり3F（秒）
- `corner_positions`: コーナー通過順位
- `jockey`: 騎手

欠損値や数値に変換できない値は平均計算から除外し、推測で補完しません。

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
