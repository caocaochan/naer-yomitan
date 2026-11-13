# NAER Terms Dictionary (樂詞網)

Chinese technical, academic and official terms with their English equivalents, built from every term list on the [NAER Web of Words download page](https://terms.naer.edu.tw/download/) (國家教育研究院 樂詞網). It is meant for looking up specialist vocabulary that general dictionaries do not cover: engineering, medicine, law, chemistry, biology and species names, music, place and person names, government titles.

## Yomitan

Download [`naer_terms.zip`](https://github.com/caocaochan/naer-yomitan/releases/latest/download/naer_terms.zip) and import it from Yomitan's **Dictionaries** settings.

The dictionary is updatable: **Check for updates** in Yomitan's dictionary settings reads this stable index URL, which always describes the latest release:
`https://raw.githubusercontent.com/caocaochan/naer-yomitan/main/index.json`

NAER adds and revises lists throughout the year. The download page is checked daily, and a new release is built only when a list was added, removed or updated; [`source_catalog.tsv`](source_catalog.tsv) records the lists and update dates of the current release, so its history shows what changed.

## Contents

<!-- stats:start -->
- 1,286,172 headwords from 2,028,417 rows in 240 NAER term lists (newest update 2026-11-11), 2,451,838 simplified and traditional lookup entries
- Yomitan package: dictionary format 3, 88 MB
<!-- stats:end -->
- Each entry starts with traditional and simplified badges, styled like the HYDCD, chengyu and Wikipedia dictionaries, followed by numbered senses: the English equivalent, then labels for the subject areas whose lists contain it (hover a label for the full list name; `+N` hides the rest). Senses found in more subject areas come first.
- Mainland Chinese equivalents (中國大陸譯名, 大陸用語) are included from the cross-strait lists and from every subject list that has the column. Both sides are headwords and refer to each other: 软体 shows `大陆：软件`, 软件 shows `台湾：软体`, and 存取 and 访问 both carry `access` with the other side's term.
- Simplified and traditional lookup forms, including OpenCC's Taiwan and Hong Kong variants, so the dictionary works on text in either script.
- Synonyms in one cell (打斷；放棄；中斷) become separate headwords. Bracketed notes and optional characters (故障(測試), 分佈群(組), 文档（件）, 夭折【动】) are dropped from the headword and shown in the sense instead.
- Useful extra columns appear under the sense: chemical formulas, element symbols, families of species, musicians' dates, nationality and speciality, German/French/Japanese equivalents in the law lists, and remarks.
- English equivalents that differ only in case, spacing, hyphens or a plural -s (`flash over`, `flashover`) are merged into one sense. About 5,400 misspelt English words in the source lists (`coufiguration`, `eletron`, `keakage`) are corrected through [`en_corrections.tsv`](en_corrections.tsv); the candidates were words seen once or twice that are one edit from a common word, each judged as typo or genuine against its Chinese term, and a correction is kept only if the corrected word itself occurs in the lists.
- Pinyin readings from pypinyin; no frequency rankings.

The three lists published only as PDF (營建管理 PDF, 化學命名原則, 中小學常用中英雙語詞彙彙編) are not included. Terms longer than 16 characters (Yomitan's default scan length) and fill-in-the-blank templates (`___區戶政事務所`) are left out. Chinese text in the definitions is converted to simplified Chinese with OpenCC.

## Building

Requires Python 3, about 130 MB of disk space for the downloaded lists and about 3 GB of RAM (2.8 GB peak measured on the GitHub runner).

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe build_dictionary.py all
```

Steps can be run separately: `catalog` lists every file on the download page into `data/catalog.json` and `source_catalog.tsv`; `fetch` does that and downloads the files not yet in `data/raw` (an updated list gets a new file name on the site, so it is picked up automatically and the old file removed; `--refresh` downloads everything again); `build` reads the lists and writes `dist/naer_terms.zip`, `index.json`, `source_attribution.txt`, the stats above and the release notes. A full build takes about 7 minutes and prints progress every 100,000 rows and entries. `validate.py` checks the ZIP against Yomitan's own JSON schemas.

The [build workflow](.github/workflows/build.yml) runs daily and on changes to the generator. It rebuilds only when `source_catalog.tsv` changed (or when started by hand with **force**), validates the ZIP, attaches it to a new release, then commits the new `index.json` so Yomitan's updater sees the new revision only once the download is live.

The site's certificate chain lacks a Subject Key Identifier, which Python 3.13+ rejects in strict mode; `naer_fetch.py` turns off only `VERIFY_X509_STRICT` and keeps normal certificate and hostname verification.

## Files

- `build_dictionary.py` — fetch and build steps, column mapping, subject labels, entry layout
- `naer_fetch.py` — reads the download page and downloads the archives
- `naer_read.py` — reads the ODS, DOCX and XLSX tables inside the archives
- `en_corrections.tsv` — misspelt English words in the source lists and their corrections
- `validate.py` — checks a built ZIP against Yomitan's schemas before release
- `styles.css` — entry styles packaged into the ZIP (same stylesheet as hydcd-yomitan, chengyu-yomitan and the Wikipedia dictionary)
- `index.json` — stable Yomitan update index; use its raw GitHub URL above
- `source_attribution.txt` — attribution and license notes, also packaged into the ZIP
- `source_catalog.tsv` — the NAER lists and update dates behind the current release
- `.github/workflows/build.yml` — daily check, build, validation and release

## License

Term data from [國家教育研究院 樂詞網](https://terms.naer.edu.tw/), used under the site's [政府網站資料開放宣告](https://terms.naer.edu.tw/mysite/about/2/) (open data declaration: free, non-exclusive, irrevocable reuse, adaptation and redistribution, with the source cited). This is not an official NAER product.
