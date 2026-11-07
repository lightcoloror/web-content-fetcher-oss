# Third-Party Notices

Updated: 2026-08-05 13:55:45 | Codex (GPT-5)

Web Content Fetcher is independently written orchestration, normalization,
quality, and archive-indexing code. The repository does not vendor the source
of the projects listed below.

## Installed Python Dependencies

| Project | Verified version or revision | License | Integration |
| --- | --- | --- | --- |
| [Trafilatura](https://github.com/adbar/trafilatura) | installed 2.0.0; reviewed source `2f4702d2117b0f95fabdd4ea35c9c2a4f3f39d04` | Apache-2.0 | Required Python API for article extraction |
| [readability-lxml](https://github.com/buriy/python-readability) | installed 0.8.4.1 | Apache-2.0 | Required Python API fallback |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | installed 4.2.0 | MIT | Required Markdown rendering |
| [Playwright Python](https://github.com/microsoft/playwright-python) | installed 1.54.0 | Apache-2.0 | Optional browser extra |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | installed 0.8.9; reviewed source `cdf2ead7ed4b78594d06b87bae930a819c685825` | Apache-2.0 | Optional Python backend |
| [MarkItDown](https://github.com/microsoft/markitdown) | installed 0.1.5; reviewed source `e144e0a2be95b34df17433bac904e635f2c5e551` | MIT | Optional attachment conversion |
| [Scrapling](https://github.com/D4Vinci/Scrapling) | reviewed source `149050685e2c0d8f511bfc042b5da247f9942afd` | BSD-3-Clause | Optional Python or subprocess backend |
| [Crawlee for Python](https://github.com/apify/crawlee-python) | reviewed source `6c099e8cae7ed5724f7d501af13ecab5b4c33b5a` | Apache-2.0 | Optional crawl-job extra |

The complete license texts for installed distributions are provided by those
distributions. Their licenses do not replace this project's Apache-2.0 license.

## External CLI and Service Bridges

These projects are not copied into this repository. WCF only invokes a local
executable, container, or HTTP API when the operator configures it.

| Project | Reviewed revision | License | Boundary |
| --- | --- | --- | --- |
| [Defuddle](https://github.com/kepano/defuddle) | `6c39f0959a2f566469a93a190b50e4b34bcf52c4` | MIT | HTML is sent to a local CLI over stdin |
| [ArchiveBox](https://github.com/ArchiveBox/ArchiveBox) | `e6c774f6378f73a499eb90e968ac5f20efdb4dc6` | MIT | External archive CLI |
| [Browsertrix Crawler](https://github.com/webrecorder/browsertrix-crawler) | `ab39c23e317e5fa83c9b59f1dc2752f91fd4ec76` | AGPL-3.0 | External Docker/CLI WACZ producer |
| [Readeck](https://codeberg.org/readeck/readeck) | `02508d1f7cb0058191026432e7e3be2b99852504` | AGPL-3.0 | External local HTTP service |
| [wechat-download-api](https://github.com/tmwgsicp/wechat-download-api) | `0d1439fc12c999fde9937980e6e701bba1969eab` | AGPL-3.0 | Local official-login HTTP service; bypass modules are excluded |
| [Firecrawl](https://github.com/firecrawl/firecrawl) | service selected by operator | upstream terms apply | Remote or self-hosted API; no code bundled |
| [WeRSS](https://github.com/letuswerss/werss) | deployment selected by operator | verify with deployed revision | Local HTTP service; no code bundled |
| [SingleFile](https://github.com/gildas-lormeau/SingleFile) | executable selected by operator | upstream terms apply | External snapshot CLI |
| [Monolith](https://github.com/Y2Z/monolith) | executable selected by operator | upstream terms apply | External single-file snapshot CLI |

AGPL software remains a separate program or service. This repository does not
copy, link, or redistribute its source or binaries.

## Source-Review Only

The offline Xiaohongshu normalizer was informed by field layouts and media
handling in [bagaoji](https://github.com/hope0719/bagaoji), fixed revision
`4c5470c1d9938dd2b3d888cc4c3c20a3a4c3e607`, specifically
`bagaoji/adapters/xhs.py`. The upstream LICENSE and source were verified at
that revision. Bagaoji is MIT licensed, copyright (c) 2026 hope0719; its
license text is retained in [LICENSES/bagaoji-MIT.txt](LICENSES/bagaoji-MIT.txt).
WCF does not import or vendor its downloader, browser, network or model code.
This attribution does not grant rights to platform content.

Defuddle, Readeck, Browsertrix, and other projects described in the architecture
audit were read to choose stable integration contracts. Reading their source
does not mean their code was copied. Any future vendoring or code
transplant requires a new provenance and license review.
