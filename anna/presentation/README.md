# 查询与阅读的消费边界

分析层需要调整提取方式和内部存储，Anna 与 Stella 则需要稳定的查询、定位与阅读行为。`presentation` 从已发布的数据生成消费模型，使分析实现的变化集中在读取与映射处，不直接传播到浏览器。它不重新提取原文、不重放引用裁定，也不写入条目或裁定。

## 职责

| 模块 | 回答的问题 |
| --- | --- |
| `catalog.py` | 当前条件命中哪些条目，如何排序、叠放版本、计数和导出？ |
| `collections.py` | 服务提供哪些资料库，各由哪个只读提供者（含可选的导览与阅读器）回答？ |
| `library.py` | 读者选定的资料库、优先级、规则版本与作品范围如何合成一张表？同名版本彼此是什么关系？ |
| `alignment.py` | 不同资料库中的哪些作品是同一部出版物？ |
| `our5etools.py` | 如何只读消费已构建的 `data/our5etools/` 产物，提供独立的查询、范围导览与 Stella 章节阅读？ |
| `references.py` | 当前发布快照中有哪些有效引用、裁定结果与缺失定义？ |
| `layout.py`、`reader.py` | 如何从条目或页面中的一段范围回到原文排版、目录页面与定义锚点？ |
| `tour.py` | 读者从哪里开始，每一层通向哪里？精选入口之下按目录、页面条目与条目关系展开导览的各层 |
| `contracts.py` | 哪些存储字段进入现有的条目与引用响应？ |

HTTP 路由、状态码、下载头和快照切换由 `anna/server/` 负责；条目查询、导出和引用候选命令行共用同一只读消费边界。来源上下文位于 `anna/review/context.py`，可迁移锚点位于 `anna/review/anchors.py`，供构建与引用裁定共同使用。

## 保持什么稳定

消费契约包括响应字段及其含义、缺失与空值、数组顺序、筛选计数、同名版本叠放、条目与局部效果标识、证据范围、解析和引用状态。查询参数、错误响应、HTML 中的批注和引用属性、链接版的 `anna-bindings`、定义锚点及阅读消息协议同样属于边界。仅保持 JSON 的字段名称不足以保持阅读体验。

`contracts.py` 明确选择条目和引用的公共字段。新增存储字段不会自动进入这些响应。证据、字段映射及其他语义 JSON 是已公开的结构化内容，按原值复制；它们的结构若改变，同样需要在适配处转换并验证消费契约。

规则链和知识记录不再属于消费边界。Anna 的阅读栏提供正文、属性、版本对照和关系；Stella 使用有效引用生成链接标记。旧知识表即使留在历史构建中，也不进入新的响应。

本轮保留原有身份与快照规则：同一快照内的链接和恢复行为不变；快照变化后，浏览器只保留查询条件，清除依赖旧快照的位置。跨快照身份迁移属于独立的数据能力，不能通过复用旧快照摘要绕过校验。

## 接入资料库

资料库标识内容来自哪套整理数据库；`source` 仍是该库中的书籍等文献，`ruleset` 仍是规则代际，`package` 标识所属内容包。各库保存自己的条目、证据与身份，条目 ID 只在本库内唯一，不同库的同名条目不合并身份、不按名称互相回退。导览与 Stella 每次只读一个资料库，由 `collection` 指定；Anna 的查询读取读者的资料库（见下节），带 `collection` 时只读这一库。

接入新的库时，实现 `CatalogProvider`（可选配合 `TourProvider` 与 `ReaderProvider`），返回既有的查询／筛选／详情消费结构，并提供资料库合表所需的 `works`、`ranked`、`rows`、`schema`、`capabilities` 与 `signature`。`works` 的每部作品另给出别的资料库可能用来称呼它的名字（`codes` 为 5etools 来源代码，`abbreviations`，`titles`）与打开它的 Stella 书根 `reader_root`；`ranked` 的每行给出所属作品 `work`，以及复制来源 `base`、`base_work`、`base_name_en`（没有时为 null）。`Capabilities.same_name_groups` 说明本库自己的版本组是否已经汇集不同作品的同名条目：不全书的同名候选组是，our5etools 只按记录的重印分组，不是；`entries`、`facets` 与 `export` 接受可选的 `Restriction`（规则版本与作品集合），范围不收窄时按原签名调用。若同时提供导览或整书阅读，可在 `Collection` 上挂载可选的 `reader_url`、`snapshot`、`tour`、`reader`、`book_base` 与 `media_base` 字段。提供者负责只读访问自己的数据和生成经安全处理的排版，不得在导入或请求阶段加载任何构建、审计或提取模块；没有可用排版时返回 `{"status":"empty"}`，正文仍来自详情的 `body`。使用 `create_server(..., additional_collections=(Collection(key, zh, en, provider, ...),))` 注册；`anna.server.app` 在存在 `--our5etools-data` 的 `manifest.json` 时按需懒加载 `load_our5etools_collection()` 注册 `our5etools`（`collection=our5etools`），在存在预留 `--5etools-data`（默认 `data/5etools/`）的 `manifest.json` 时加载 `load_fivetools_collection()` 注册独立的 `5etools`（`collection=5etools`），运行时仅消费各自自包含快照（含 `media/`）而不读取源镜像；未传入对应数据时，`our5etools` 与 `5etools` 均保持 `reserved` 占位。构建用的 `5ETOOLS_DIR` 仍只供应不全书对照，不会启用 `our5etools` 或 `5etools` 资料库。

`/api/meta` 返回首个资料库、默认库、各库状态（`ready`／`reserved`）与能力，以及首个资料库自身的 `reader_url`、`tour_url` 和 `snapshot`；未知、未启用、重复／空库参数以及不支持导览或阅读的资料库分别返回明确错误（404／409／400）。非不全书资料库不开放 `/source` 原文范围；`our5etools` 的导览（`/tour?collection=our5etools`，含书目／资料包范围继承与显式「放宽到全库」）与 Stella 阅读（`/read?collection=our5etools`，正文路由 `/our5etools-book/`、本地媒体路由 `/our5etools-media/` 从 `data/our5etools/media/` 提供服务，保持同源 `CSP img-src 'self' data:`，并在传入 `--lazy-media` 时按需校验并原子缓存远端配图）与预留 `5etools`（`/5etools-book/`、`/5etools-media/`）均绑定各自构建快照，不全书的导览、Stella 和原文证据继续沿用原有快照与锚点。

## 读者的资料库

读者在浏览器中选择要读的资料库及其顺序、同名条目是否合为一行与优先的规则版本、同一作品由谁的文本领头、要显示的规则版本，以及内容性质与逐部作品（`web/library.js`）。Anna 的每个请求带上这份选择；`library.parse_scope` 把它从查询参数中取出，其余参数仍是查询条件：

| 参数 | 含义 |
| --- | --- |
| `libraries=5echm,our5etools` | 读者勾选的资料库，按优先级排列；服务当前无法打开的资料库跳过并列入 `unavailable`，全部无法打开时回到不全书并标出 `fallback` |
| `collection=…` | 只读这一个资料库（来自该库的导览、Stella 或旧链接）；未知、未启用或空值分别返回 404／409／400 |
| `versions=stacked`／`each`、`prefer=5r`／`5e` | 同名条目是否合为一行，以及行首优先的规则版本 |
| `rulesets=5e,5r,待核` | 显示的规则版本；全部选中等同不限制 |
| `content=official,partnered,unverified,curated,community` | 显示的内容性质，对应各库的来源性质（官方、合作、待核、本站精选、社区） |
| `hide=<资料库>:<作品 ID>` | 逐部隐藏的作品，可重复 |
| `lead=<资料库>:<作品 ID>` | 读者指定领头的作品：同一部作品在几个资料库中各有文本时，这部的文本领头，可重复 |

规则版本与作品范围构成每个资料库的 `Restriction`，限定查询、计数、`scope_total` 与导出读取的全集；它不是条件，计数不会放下它，`without` 也不会提议移除它。只读一个自己的版本组已汇集同名作品的资料库，或不去重时，提供者按原样回答，响应与单库时完全相同。读多个资料库，或只读一个自己只按重印分组的资料库并去重时，由资料库层回答：

- 各库先按自己能回答的方式理解条件：另一库的类型、作品、资料包或字段对本库不成立，本库不贡献结果；对本库不成立的排除条件不排除任何条目。所有资料库都不认识的类型返回 400。计数在放下该条件的字段上计入本库，`without` 据此给出移除它后的结果。
- 各库以 `ranked` 给出命中版本的轻量行，库层按同一排序规则合并为一个顺序，再分批以 `rows` 读取显示的行。合并顺序按查询缓存，翻页不重算。
- 作品对齐：`alignment.align` 在每两个已接入的资料库之间比较作品的来源代码、缩写与去掉版次括注、年份和虚词后的书名，优先采用较强的依据；一部作品有几个候选时以共有的同名条目（类型 + 英文名）数量取舍，只保留互为唯一最佳的配对，打平则都不对齐。对齐结果按各库快照缓存（`publications_of`），把同一出版物的作品归为一组，组的内容性质取组内最高者。
- 同名叠放：各库自己的版本组保留；可叠放类型的全部版本（出版物与家酿）另按“类型 + 英文名”跨作品、跨资料库合并。行首依次取内容性质（同一出版物取组内最高）、优先的规则版本、读者指定的领头作品、资料库顺序与排序位置。跨库组的状态为 `library_candidate`，行与版本都带 `collection`。
- 版本关系：每个版本带 `relation`，由它与所示版本之间最短的证据链决定（`relations`）。直接证据有三种：同一资料库的同一版本组或同一作品，另一资料库中的同一出版物，以及复制关系（复制自对方，或复制自对方所在出版物中同名的条目）。经由自己的复制关系到达的版本为 `derived`，只经同一出版物到达的为 `translation`，其他证据链为 `version`，没有证据链的为 `candidate`；所示版本为 null。
- 详情按 ID 在读者的资料库中依优先级寻找，再查其他已接入的资料库；`collection` 指出条目所属的库，版本对照并入读者各资料库中的同名版本（打开的版本及其所在版本组不受范围限制），按行首的顺序排列，`relation` 相对于打开的版本。导出每个版本一行，首列为资料库，文件名为 `5e-anna-library.csv`。

`/api/library` 列出全部资料库及其能力（`tour`、`reader`、`snapshot`）；带 `works=1` 时附上各已接入资料库的作品、来源性质、内容性质、规则版本、条目数、Stella 书根，以及同一出版物在其他资料库中的作品（`counterparts`），供资料库设置与 Stella 的“在本页打开本书”使用。`/api/meta` 另返回 `library`，写明生效的资料库、是否只读一库、显示方式与范围是否收窄；`collection` 是排在最前的资料库，`source_text` 表示不全书在读取范围内、可打开 `/source` 原文范围。

## 验证与后续修改

`tests/fixtures/presentation-*.json` 保存边界拆分前的只读数据、查询响应，以及原文排版和阅读绑定。`tests/test_presentation_contracts.py` 直接恢复冻结的 SQLite 行，不运行提取器，核对完整响应、顺序、CSV、HTTP 错误和下载、HTML 定位及拒绝情形；另检查内部字段隔离和服务导入不加载提取或引用裁定命令。`tests/test_library.py` 以两份同一夹具充当两个资料库，核对范围解析与拒绝、跨库叠放与优先级、分批顺序、范围收窄、不可回答的条件、详情归属、导出与 HTTP 路由；另以内存中的家酿资料库核对家酿与出版物同行、版本关系、领头作品、单库由资料库层叠放与作品对齐的取舍。夹具只固定消费行为，不代替全库审计与实际语料检验。

调整分析实现时，先在相同冻结输入上运行契约测试，再运行完整构建与 `make check` 验证全库覆盖与一致性。运行命令统一见 [USAGE](../../USAGE.md)。原文更新或有依据的引用修订可以改变结果，但应先解释差异，再修改对应预期；不要用本次输出直接重录所有夹具。浏览器回归仍需验证 Anna、Stella、共享状态、键盘焦点和不同窗口宽度。
