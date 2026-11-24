# 使用

本文说明 anna 的命令、构建与运行流程、本机配置、引用审核与发布，以及 Anna、导览与 Stella 的常用操作。数据结构与提取规则见[数据模型](MODEL.md)，界面的设计与实现见[界面说明](web/README.md)。

## 统一命令

以下命令在 `5e-anna` 目录运行。上层 DnD 的 Makefile 把全部目标与参数转调到本项目，只按上层目录换算 `WEB_DIR`；在上层使用其他路径参数时写绝对路径。

| 命令 | 用途 |
| --- | --- |
| `make clone` | 原文或 5etools-cn 对照镜像缺失时按 `sources.json` 的仓库与分支下载；已有镜像保持不变 |
| `make build` | 构建不全书数据库与链接版，有 our5etools 镜像时一并构建，最后生成首页缓存。可只运行其中的阶段，见[构建阶段](#构建阶段) |
| `make pages` | 把已构建的资料库打包为静态站点；`make build pages` 先重建再打包 |
| `make our5etools-audit` | 核查 `data/our5etools/` 的不变量并生成 `data/our5etools/audit.md` |
| `make up` | 启动单端口查询、导览、阅读与正文资源服务（存在 `data/our5etools/manifest.json` 时自动挂载 `our5etools` 资料库；`data/5etools/` 预留给独立中文版 `5etools`；可用 `LAZY_MEDIA=1` 开启远端配图按需懒加载缓存） |
| `make upper` | 开发用：同上，不全书或 our5etools 重建完成后自动校验并热重载 |
| `make icons` | 从原始人物图生成界面图标 |
| `make test` | 运行单元测试和浏览器回归（需先构建数据） |
| `make test-unit` / `make test-browser` | 分别运行单元测试 / 浏览器回归 |
| `make browser-setup` | 安装锁定的 Node 依赖与 Chromium，首次运行浏览器测试前执行 |
| `make audit` | 审计不全书数据库与生成统计；存在 `data/our5etools/manifest.json` 时同步执行 `our5etools-audit` |
| `make audit-links` | 从原文逐处复核字段、表列及骰值伤害的引用落点 |
| `make check` | 运行测试与审计 |
| `make update` | 显式拉取原文与 5etools-cn 更新，并把各镜像的新版本写入 `sources.json`；之后运行 `make build` 更新数据 |
| `make status` | 查看原文、5etools-cn、our5etools 镜像及构建产物状态，以及各镜像是否处于 `sources.json` 固定的版本 |
| `make clean` | 删除本地生成数据、报告和图标；保留依赖环境、源码、独立输入与共享镜像 |
| `make distclean` | 在 `make clean` 之外删除 Python 虚拟环境、Node 依赖和浏览器测试缓存 |

不带目标运行 `make` 会显示本页位置。下文的 `python -m anna.…` 开发维护命令在仓库根目录运行。

各命令用同一个参数名指代同一份输入。Makefile 把路径作为参数传给命令；直接运行命令且未传参数时，镜像路径取自同名环境变量，其余取项目内的默认位置：

| 参数 | 含义 | 未传时 |
| --- | --- | --- |
| `--web` | 5echm-web 原文镜像 | 环境变量 `WEB_DIR`，否则同级的 `5echm-web/` |
| `--counterparts` | 5etools-cn 对照镜像 | 环境变量 `COUNTERPARTS_DIR`，否则同级的 `5etools-cn/` |
| `--our5etools` | our5etools 镜像 | 环境变量 `OUR5ETOOLS_DIR`，否则同级的 `our5etools/` |
| `--db` | 不全书查询库 | `data/extracted/catalog.sqlite` |
| `--book` | 不全书链接版目录 | `data/5echm-anna/` |
| `--our5etools-data` | our5etools 资料库目录，由 `anna.our5etools.build` 写入，其他命令读取 | `data/our5etools/` |
| `--5etools-data` | 预留的独立 5etools 资料库目录 | `data/5etools/` |
| `--output` | 命令自身的报告或产物，其他命令不读取 | 见各命令的 `--help` |

`--source` 只表示书籍来源筛选，见于 `anna.catalog.query` 与 `anna.review.repository candidates`。

## 构建阶段

构建由几个阶段组成，每个阶段是一个 make 目标。`make build` 依次运行 `5echm`、`our5etools` 和 `home`；our5etools 镜像缺少 `data/` 或 `__api/books.json` 时跳过这一阶段并给出提示。只需更新部分产物时，在一条命令里按顺序写出所需阶段，例如 `make our5etools home`。阶段严格按书写顺序执行，后一阶段读取前一阶段发布的产物。

| 阶段 | 作用 |
| --- | --- |
| `make 5echm` | 下载缺失的原文与对照镜像，生成界面图标，重建不全书查询库与链接版 |
| `make linked` | 用已有不全书数据库重建链接版 |
| `make our5etools` | 重建 our5etools 查询库、章节和随包媒体；找不到镜像时报错停止 |
| `make home` | 重建本地首页缓存 `data/home.json`，内容为资料库摘要、元数据、筛选计数和首批 150 行 |
| `make pages` | 校验已构建的资料库快照并打包为静态站点；尚未构建不全书时报错停止 |

| 参数 | 用途 |
| --- | --- |
| `NO_COUNTERPARTS=1` | `5echm` 跳过 5etools-cn 来源对照 |
| `PAGES_BASE` | 静态站点的托管路径，默认 `/5e-anna/` |
| `PAGES_OUTPUT` | 静态站点的输出目录，须尚不存在，默认 `output/pages` |

`make pages` 打包 `make up` 会提供的资料库：以不全书为基础，存在 `data/our5etools/manifest.json` 时一并打包 our5etools。站点自带首页缓存，用与 `make home` 相同的编码从打包后的站点数据生成，因此本地与 Pages 的首屏来自同一套逻辑。重建后发布用 `make build pages`；只重建 our5etools 并复用已有的不全书产物用 `make our5etools home pages`。

## 路径与环境

默认原文位于项目同级的 `5echm-web/`。用 `WEB_DIR=/路径` 指定其他镜像，相对路径以执行 `make` 的目录为基准。

本机固定的路径与参数写入项目根目录的 `local.mk`。该文件不提交 Git，Makefile 启动时载入；用 `?=` 赋值时，环境变量和命令行参数仍然优先：

```make
OUR5ETOOLS_DIR ?= /路径/our5etools
OUR5ETOOLS_IMG_DIR ?= /路径/5etools-img
```

[来源对照](#来源对照5etools-cn)所用的 5etools-cn 镜像默认位于 anna 同级的 `5etools-cn/`，只检出 `data/`。`COUNTERPARTS_DIR=/路径`（以及 `COUNTERPARTS_REPO`、`COUNTERPARTS_BRANCH`）指定其他位置或来源，相对路径同样相对于执行 make 的目录；它们可以写在 make 参数、`local.mk` 或环境变量中。直接运行 `python -m anna.catalog.build` 时用 `--counterparts /路径` 或环境变量 `COUNTERPARTS_DIR`。用 `NO_COUNTERPARTS=1` 跳过对照时，构建摘要记为 `skipped`。

[our5etools 资料库](#our5etools-资料库)是独立的社区中文资料库（键名 `our5etools`），与默认不全书（`5echm`）、预留的独立中文版 `5etools` 接口（键名 `5etools`、`5ETOOLS_DATA ?= data/5etools`）以及仅稀疏检出 `data/` 作旁证的 `COUNTERPARTS_DIR`（`5etools-cn` 对照镜像）分开管理。本地镜像默认位于 anna 同级的 `our5etools/`，其他位置通过 `OUR5ETOOLS_DIR=/路径` 或 `local.mk` 指定，可选通过 `OUR5ETOOLS_IMG_DIR=/路径`（CLI `--img-dir`）打包离线 `5etools-img` 图像目录至 `data/our5etools/media/img/`，通过 `OUR5ETOOLS_DATA`（CLI `--our5etools-data`）指定输出目录（默认 `data/our5etools`）。构建产物完全自包含，运行时只读取 `data/our5etools/`，不再依赖或访问 `OUR5ETOOLS_DIR`。默认构建在镜像包含 `data/` 和 `__api/books.json` 时加入 our5etools。只构建这套资料库时使用 `make our5etools`。

构建时缺失的镜像会自动下载；已有镜像不会自动更新，由 `make update` 显式拉取，见[源码版本锁](#源码版本锁)。`make clean` 只清理本项目，不删除共享镜像。

默认优先使用 anna 的 `.venv/bin/python`，未创建时使用 `python3`；可用 `PYTHON=/绝对路径/解释器` 覆盖。`ANNA_PORT` 配置查询、阅读与正文资源共用的端口，仅本机访问。

新环境先在 anna 中创建虚拟环境并安装依赖：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make clone
make build
make browser-setup
make check
```

## 源码版本锁

本地构建与 GitHub Pages 需要读取同一份原文快照。仓库根目录的 `sources.json` 为每个镜像记录仓库地址、分支与固定版本，5etools-cn 另记稀疏检出的目录。Makefile 的 `REPO`、`BRANCH`、`COUNTERPARTS_REPO` 与 `COUNTERPARTS_BRANCH` 默认取自这里，Pages 工作流按其中的版本检出镜像：

```json
{
  "schema": 1,
  "mirrors": {
    "web": {"repo": "https://github.com/DND5eChm/5echm_web.git", "branch": "pages", "revision": "c1425c8e…"},
    "counterparts": {"repo": "https://github.com/tjliqy/5etools-cn.git", "branch": "main", "revision": "bdf81fee…", "sparse": ["data"]},
    "our5etools": {"repo": "https://github.com/KuroNeko233333/KuroNeko233333.github.io.git", "branch": "main", "revision": "e475d00b…"}
  }
}
```

键名对应镜像的路径参数：`web` 对应 `WEB_DIR`，`counterparts` 对应 `COUNTERPARTS_DIR`，`our5etools` 对应 `OUR5ETOOLS_DIR`。`revision` 写完整的 40 位提交号；文件缺失或格式无效时，读取它的命令报错停止。版本锁只记录和比较版本，不移动镜像：

- `make status` 逐个说明镜像 HEAD 是否与固定版本一致。`make 5echm` 与 `make our5etools` 遇到不一致时给出警告并照常构建，读取的是镜像的当前内容。
- `make update` 在各自分支上快进拉取原文与 5etools-cn，再把三个镜像的 HEAD 写入 `sources.json`；our5etools 镜像只读取 HEAD，不拉取。HEAD 不在镜像的 `origin/<分支>` 上时（例如尚未推送的本地提交），Pages 无法获取它，原固定版本保持不变。
- 更新后的 `sources.json` 是一项可审阅的改动：运行 `make build` 与 `make test-unit` 确认新快照，再提交并推送，Pages 随即构建同一快照。`make clone` 新下载的镜像停留在分支最新提交，可能领先于固定版本，`make status` 会如实显示。

`make update` 只更新镜像与版本锁，之后运行 `make build` 才更新数据库、库内全文索引和链接版。

## 清理与重建

清理前停止正在运行的 anna 服务。两级清理都直接删除，不备份，也不遍历指向项目外的符号链接；`assets/branding/` 原图、`annotations/` 结构化标注输入与 `local.mk` 保留。

- `make clean` 删除 `data/`、`output/`、中断构建目录、项目内的 Python 缓存和由人物原图导出的界面图标，之后执行 `make build` 恢复图标、查询库与链接版。
- `make distclean` 另外删除 `.venv/`、`node_modules/` 和 `.cache/`（npm 与 Chromium 缓存），之后按[路径与环境](#路径与环境)末尾重新安装依赖；只有运行浏览器测试才需要 `make browser-setup`。

生成文件和历史测试报告不提交 Git。

## 阅读服务与正文生成

为避免查询条目与阅读原文来自不同快照，`make build` 顺序生成 `data/extracted/catalog.sqlite` 和 `data/5echm-anna/`。随后运行：

```sh
make up
# 自定义端口
make up ANNA_PORT=8101
# 开发：重建数据后自动换包，无需重启
make upper
```

默认打开条目查询 [anna](http://127.0.0.1:8000/)、[导览](http://127.0.0.1:8000/tour)或阅读器 [stella](http://127.0.0.1:8000/read)。三页都读取[资料库设置](#资料库设置)中勾选的全部资料库。查询、阅读和 `/book/` 正文资源由同一个 HTTP 服务（Python 标准库，仅本机、只读）提供，`Ctrl-C` 或终止信号停止服务。启动会校验原文、数据库和生成文件的哈希，发现快照不一致、文件缺失或改动即拒绝启动，并提示重新构建。`make up` 不下载或更新原文，也不自动重建数据。阅读界面由 anna 提供，上游仓库只提供内容输入；`/book/` 下不提供上游 WebHelp 阅读界面。

开发时用 `make upper` 启动同一服务。构建发布新数据库后、换包完成前，查询、搜索和 `/book/` 正文返回 409；服务校验通过后一次性切换到新快照，不混用新旧数据。校验失败时保持 409 并给出原因，直到下一次成功构建；换包机制见[内容与导航边界](web/README.md#内容与导航边界)。`make upper` 同样不下载、不更新原文，也不自动重建；修改 `anna/` 下的 Python 代码仍需重启，修改 `web/` 前端文件刷新即可。包内实现参与构建指纹；目录迁移或提取实现变化后，执行审核写入前需重新 `make build`，不沿用旧实现指纹。

链接版在原文正文页上添加定位数据与点击层，注入方式见[内容与导航边界](web/README.md#内容与导航边界)；哪些名称获得链接见[引用发现与裁定](MODEL.md#引用发现与裁定)。生成结果中的 `anna-manifest.json` 记录输入、输出哈希与条目定位，`anna-reader.json` 保存目录、页面及其条目列表；`anna-report.json` 记录定义数量、可点击定义、引用解析及待核清单。原文空页或缺失内容范围的记录无法生成可靠的可点击定位，定义路由会明确报错，不跳到同名的其他记录。正文范围为空但标题仍可定位的记录也单独列入报告，供提取侧核对。

## 使用指南

### 条目查询

打开 [查询页](http://127.0.0.1:8000/)。查询读取顶栏「资料库」中选定的资料，默认只有不全书（`5echm`），设置方式见[资料库设置](#资料库设置)。

- **条件栏**：在条件栏输入名称即可查询；按 Enter 把搜索留作卡片后可继续输入下一个。每个条件和搜索都是独立的卡片，卡片之间默认以“∧”连接，表示都要满足；点击连接翻成“∨”，两张卡片互为替代。“∨”比“∧”结合得更紧：“类型：法术 ∧ 学派：塑能 ∨ 名称：火”是名称含“火”或属塑能学派的法术。输入 `(`、`)` 加括号改变分组，输入 `|`、`&` 设定光标处的连接（全角同样有效）；未配对的括号按行首或行尾补全，补上的括号淡色显示；“∨”连起的替代与“∧”相邻时，两侧显示品牌色的淡色括号，标出“∨”结合得更紧时的分组。光标所在分组的一对括号加深显示，指针停在括号上时它的配对括号也加深。
- **编辑与撤销**：卡片停在添加它的位置，像文字一样可以把光标移到任意两张卡片之间，在那里输入、添加或用 Backspace／Delete 删除前后的卡片或括号；删除一个括号会同时删除与它配对的括号。⌘Z、⇧⌘Z（Windows 与 Linux 为 Ctrl+Z、Ctrl+Shift+Z 或 Ctrl+Y）像编辑文字一样撤销与重做条件栏的每一步，包括筛选区的选择、清空与前进后退。
- **排除与提示**：点击卡片文字切换为“不是／不含”，即排除这些取值或词，筛选区的“排除”按钮切换整个字段。条件之间无法同时成立、或所选类型无法回答某个条件时，条件栏下方给出提示和修改按钮，页面不会自动删改条件；两张类型卡片都要满足时没有结果，提示提供“改为‘或’”与合并。
- **建议**：下拉的第二项“正文包含”改在条目原文中查找。输入“法术”“塑能”“PHB”“坐骑”等文字时，下拉建议对应的类型、字段取值、来源（`our5etools` 另有资料包）或预设，选择即在光标处加入一张卡片，数字是放在那里之后的结果数；字段已有卡片时，“并入”一项把取值加进那张卡片。规则版本与来源性质由资料库设置决定，不再出现在建议与筛选区。
- **筛选**：筛选区默认收起，已选条件（类型在最前）以标签显示在条件栏中，可逐个移除；“清空”去掉全部条件；点击“筛选”展开常用条件，“更多筛选条件”展开其余领域条件。展开状态在本标签页刷新和前进后退时保留。“筛选”中的第一个字段是条目类型，与其他字段同样操作；所选类型决定表格的列与筛选字段。法术按环阶、学派、职业、施法时间及特性筛选；生物按 CR 范围、体型、类型及传奇动作筛选；物品按稀有度、类别及同调筛选；技能按所用属性筛选，并按力量到魅力的顺序排列，2014 与 2024 的同名属性和技能叠放为版本。环境危害按类型“环境危害”查询，类别导言与查表页保持未分类段落，识别规则见[自动提取](MODEL.md#自动提取)。字段未选即不限制，点击取值即加入；同组多选取并集、组间取交集，支持清除与反选。每个取值旁的数字是选中后的结果数。
- **结果**：切换类型或条件时，旧结果变淡，直到新结果到达。结果表固定表头与名称列，其他属性可横向滚动，中文与英文名称同时保留；法术的成分与仪式／专注以字形条显示，导出仍逐项保留。手机宽度（640px 及以下）结果以列表显示。表头可切换升降序；环阶、CR、稀有度按实际顺序排序，缺失或冲突数值置后。结果随滚动每批加载 150 行，底部也可点击“继续加载”。在某些类型中搜索时，结果栏显示其他类型中的匹配数；结果为空时，空状态列出去掉后能得到结果的条件。未分类段落可以通过查询参数的 `sections=1` 纳入结果，界面不再提供单独的补查按钮。结果底栏的“导出 CSV”导出全部匹配行及所选类型的属性。
- **状态**：查询与排序保存在地址栏，资料库设置保存在本浏览器。刷新或前进后退恢复已加载的行数和位置。
- **同名版本**：同名的法术、生物、物品等版本默认合为一行，出版物与家酿都在其中：名称下列出各版本标签，来自排在首位以外的资料库的版本标签带资料库名，版本间不同的取值列在显示值下方。标签写出它与所示版本的关系：「同一作品」是同一部作品在另一资料库中的文本，「派生」是复制同名条目后修改的家酿，「同名候选」只有名称相同。是否合为一行、优先显示 2024 还是 2014 规则在资料库设置中选择；结果栏“显示”菜单有“紧凑行”，并注明资料库设置是否收窄或不去重。导出始终一个版本一行，读多个资料库时首列写出资料库。
- **坐骑与动物购置**：“坐骑”列出带用途证据的生物数据卡；“动物购置”另列价格、载重和用途，通过详情中的“使用数据卡／购置条目”双向关联。

点击结果行打开条目阅读，双击则以全宽阅读打开：1200px 及以上时阅读栏在结果右侧展开，标题与结果的每一行都留在原处，可以继续点击其他结果；拖动阅读栏左缘或聚焦后用左右方向键调整宽度，双击恢复默认。更窄时为模态阅读层。阅读栏右上角的全宽阅读按钮让条目铺满工作区，宽于阅读栏的原文表格上方也提供同一入口；全宽阅读持续到关闭阅读栏。阅读栏宽度保存在本浏览器，二者都不进入共享链接。右上角箭头浏览相邻结果，结果列表随之滚动到当前行，到达已加载的末尾时自动加载下一批；条目不在已加载结果中时禁用。筛选与类型变化关闭旧详情；关闭详情不改变列表位置。刷新、浏览器前进后退恢复查询状态、筛选展开状态、详情栏目和滚动位置。读取失败可在阅读层内重试。

阅读栏有正文、属性、版本对照和关系四个栏目。“正文”按原书排版显示条目，数据卡等表格保持表格；提取的字段以批注列出，悬停或点击批注会高亮其依据的原文文字，正文中已解析的引用可直接打开目标条目。原文镜像缺失或与数据库快照不一致时，显示提取文本并说明原因。“版本对照”逐字段比较同组全部版本，读多个资料库时包括其他资料库中的同名版本。“正文”上方可选“标出与 … 的不同”，在所示版本的排版中高亮仅本版有的文字、以删除线插入另一版本独有的文字；只对比同一资料库的版本，派生版本打开时自动对照它复制的条目，差异过多时改为建议看“版本对照”。

键盘支持 `/` 聚焦条件栏、条件栏中上下方向键与 Enter 选择建议、条件栏中 ←／→／Home／End 在卡片之间移动光标、Backspace／Delete 删除光标前后的卡片或括号、`(`／`)`／`|`／`&` 输入括号与连接、⌘Z／⇧⌘Z（或 Ctrl+Z／Ctrl+Y）撤销与重做条件栏的编辑、结果名称上的上下方向键移动焦点、Enter 打开条目、Esc 关闭详情；宽屏 F6 切换结果与详情（全宽阅读时先退出全宽阅读）。详情栏目可用左右方向键及 Home／End 切换；窄屏 Tab 焦点保留在阅读层内。中文输入法合成期间不提交中间文本。视觉与实现约定见[查询界面设计](web/README.md)。

### 资料库设置

每个页面顶栏都有「资料库」按钮，旁边写出正在读的资料库；设置收窄了规则版本或作品、不再去重或指定了领头译本时，按钮带一个圆点。设置保存在本浏览器，同源标签页同步，查询、导览与 Stella 共用，不进入共享链接。对话框中各项即时生效，「恢复默认」回到只读不全书、全部规则版本与全部作品。

| 设置 | 作用 |
| --- | --- |
| 资料库与优先级 | 勾选要读的资料库（至少一个），拖动或用箭头排序。Anna 把勾选的资料库合成一张表，导览与 Stella 把它们排成一个书架，排在最前的资料库给出书架的分组与次序；未接入的资料库列出但不可选。 |
| 同名条目 | 「去重」把同名条目合为一行，出版物与家酿、跨资料库同样合并；一行先显示官方与合作出版物，再按「优先显示」的规则版本、领头译本与资料库顺序，其余版本列为标签并标出关系。同一部作品在两库中性质不同（our5etools 的社区家酿、不全书的合作出版物）时按较高的性质排序。取消去重后每个版本一行。 |
| 同一作品的领头译本 | 列出在所读的几个资料库中都有的作品（依来源代码、缩写或书名对齐），为每部作品选择由哪个资料库的文本领头：查询中同名条目的行首，以及书架上默认打开的文本；默认按资料库顺序。 |
| 规则版本 | 2024 版（5.5e）、2014 版（5e）与规则版本待核的条目分别显示或隐藏，至少保留一种。 |
| 内容范围 | 「官方内容」「第三方内容」（合作出版物、本站精选与社区家酿）与「性质待核」三张卡片分别开关；「按作品挑选」按资料库与来源性质列出全部作品，可筛选、逐部勾选。只勾选一部本已关闭的作品时，该类内容打开、其余作品隐藏。 |

查询页始终读取资料库设置中的全部资料库；旧链接中的 `collection=` 参数在载入时移除，其余条件保留。条目由所属资料库回答：地址中的 `entry` 是条目自身的标识，它所在的资料库未勾选时仍可打开。结果为空且资料库设置收窄了范围时，空状态提供「资料库设置…」入口。未构建对应数据时，`our5etools` 与 `5etools` 显示为「未接入」，设置对照用的 `COUNTERPARTS_DIR` 不会启用它们。

### 阅读操作

Stella 把资料库设置中勾选的资料库排成一个书架：排在最前的资料库给出书架的分组与次序，其后的资料库只补上前面没有的作品，接在后面并沿用自己的分组，目录中标出资料库名。几个资料库都有的同一部作品只列一次，放在最早出现的位置，默认打开「同一作品的领头译本」指定的文本，未指定时按资料库顺序。阅读这样的作品时，阅读栏右端出现文本切换（如「不全书｜our5etools」），选择另一份文本即在那份文本中打开对应位置：先找标题相同的章节或小节（忽略「第二章」「第 2 章」这类编号写法），再看本页定义的条目在哪一页，都找不到时打开那份文本的开头；目录随之显示正在读的文本。

阅读地址中的目录节点、页面与条目都带资料库前缀，如 `/read?root=our5etools:n_book_xphb&page=our5etools:books/xphb/2.html`、`/read?entry=5echm:ver_…`。打开的地址属于资料库设置中未勾选的资料库时，Stella 说明原因并提供「资料库设置…」。在对话框中调整资料库或领头文本后，书架当场重排，正在读的页面保留。

侧栏底部显示原文「写在前面」中标注的不全书版本（如 `v20260913`），不是 anna 版本或构建哈希；原文没有唯一有效的版本声明时不显示。

- **目录**：栏目下方一行图标工具。左端放大镜展开筛选框，可查找书籍或章节；筛选框为空时，离开或按 Esc 即收回；筛选期间右端只保留“定位当前章节”。右端依次为“定位当前章节”“展开当前章节”（展开当前阅读范围的全部子节点，并把它排到目录顶部）和“全部收起”。点击目录名称，以该节点及其递归子节点建立阅读范围；同一页面在目录中的多个入口分别保留，阅读栏的位置路径从范围的上级一直写到当前页。清空筛选后恢复原来的展开状态。
- **目录跟随**：默认跟随当前正文页，必要时展开祖先并仅滚动到能看见当前项的位置。手动筛选、展开／收起或滚动目录后暂停跟随；“定位当前章节”图标跟随时为品牌色，暂停时变为普通图标色；点击即清空筛选并恢复跟随。目录关闭或切到“本页”“搜索”时不会自动打开或切换栏目。
- **连续阅读**：阅读范围按目录顺序包含选中节点自身与所有后代的可用正文。没有正文的分组从首个可读后代开始；叶子节点只包含自身。滚动进入下一页不会改变范围，也不会自动延伸到兄弟章节。同一文档的多个目录锚点共享一份正文。
- **加载与分页**：初次加载目标页，接近正文末尾时自动接续下一页，也可点击“加载下一页”手动接续；阅读栏左端为目录开关和位置路径，右端分页器显示当前页／总页数，左右箭头逐页移动。点开路径中范围名称之后的任一层，可在它的同级节点间跳转；末尾的“›”列出当前节点的下级；只是分组的节点打开其第一个可读页面。路径中的跳转不改变阅读范围；点击范围名称回到范围第一页，点击更上层的链接则建立新的范围。窄屏放不下的层级收进“…”。跳转时直接加载目标页，不逐页加载中间文档；缺口处显示未加载区间及接续按钮，远处正文按下述规则回收。自动或手动加载失败后保留已有正文，可原位重试，不自动反复请求。到最后一页显示“已到本章节末尾”。
- **远页回收**：阅读器仅保留当前位置附近的正文，远页释放后以等高占位保留位置，回滚接近时重新载入；也可点击占位上的“重新载入”。阅读范围、总页数与最后阅读位置不随回收改变。
- **本页**：按原文顺序列出可定位条目；点击名称进入定义，旁边的箭头在新标签页打开 anna。尚未提取成条目的页面仍可整页阅读，空列表不表示原文缺失。
- **搜索**：搜索书架上全部资料库的完整原文，可限定页面标题；各库结果按同一评分规则合成一列，结果旁写出所属资料库。只要某个资料库有全部词语都命中的页面，其他资料库只命中部分词语的页面不列出；全部词语同时命中的页面都没有时，明确提示改为任一词语命中；“在条目查询中筛选”进入 anna 的结构化查询。结果分批翻页，失败后可重新提交。
- **正文**：沿用原文文字、表格、图片、顺序与样式，浅色／深色模式使用原版阅读器的正文配色，并与查询页共用主题偏好。正文铺满顶栏和阅读栏下方的阅读区，不重复显示页面标题；收起目录可获得完整窗口宽度。文字先于尚未下载完成的图片显示。
- **标出链接**：阅读栏右端的高亮图标。开启后，Anna 识别出的链接显示淡色底：蓝底是正文引用，点击在当前阅读器跳到定义；红底是定义标题，点击在新标签页打开 Anna 条目。默认关闭，正文保持原貌；选择保存在本浏览器，并同步到其他标签页。
- **继续阅读**：自动记住本浏览器最后的阅读范围、当前页面、目录入口和页内位置。关闭后重新打开阅读器，或从查询页转向 Stella，会回到该位置；恢复时只加载当前页面，其余页面按需接续。明确指定范围、页面、条目或锚点的链接优先。刷新和前进后退仍使用当前历史记录。正文内跳到范围内目标时保留范围，范围外目标建立新的阅读范围，后退恢复原处。某个资料库重建后，不再套用该库的旧位置；禁用或清除浏览器存储后不保留跨次阅读记录。
- **窄屏**：800px 及以下将导航收为抽屉，选中页面或条目后回到正文。目录支持方向键、Home／End 和 Enter；路径菜单支持 ↑↓、Home／End、首字跳转、← → 切换层级与 Esc；`/` 打开搜索，F6 在正文与工具栏之间切换，Esc 先清空目录筛选，再关闭抽屉。

刷新与前进后退会恢复正文位置、目录展开、当前导航栏目和搜索状态。分享地址携带阅读范围、页面／条目、原锚点与目录入口；主题与链接标记是本浏览器偏好。快照变化后不套用旧滚动位置，已消失或无法定位的条目明确报错，不按名称寻找替代目标。

### 在 Anna 与 Stella 之间

两页顶栏右侧、主题按钮左边是另一位向导：她的头像和一句去向。

- **转向她**：点击头像一栏，在当前标签页打开她。Anna 刚展示过、Stella 还没接过的条目，Stella 会“翻到”它；Stella 正在读的定义，Anna 会去“查”它。没有新话题时，她带你“接着读”或“回到”上次停下的地方；没有记录时打开她的首页。
- **最近打开的位置**：有多个选择时旁边出现“▾”，列出当前话题的条目，以及最近打开的最多 5 个位置，每项带另一位向导的头像（Anna 保存查询条件，Stella 保存阅读范围）。选中后恢复当时的列表滚动、已加载的行或页内位置。
- **多个标签页**：同一浏览器的标签页共享这些记录，一处打开条目，其他标签页的向导随即更新。记录属于整个资料库，每个位置与条目绑定所属资料库的构建；某个资料库重建后，只丢弃它的位置、话题和查询中打开的条目，查询条件与其他资料库的记录保留。浏览器禁止存储时只能转到对方首页。

### 导览

[导览](http://127.0.0.1:8000/tour)是 Anna 的另一页。Anna 两页的顶栏品牌旁都有“导览／查询”切换，各自回到上次停下的地方。

- **从目的出发**：第一列按开始游戏、角色选项、法术与灵能、城主资料、魔法物品与奇珍、规则参考、资料包与书架分组，覆盖资料库设置中的全部资料库。入口旁的年份标明规则代际，数字是下一层的行数或 Anna 中的条目版本数；查询类入口的数字、分组与条目列表与 Anna 合表的结果一致。章节类入口在领头文本所在的资料库中打开。只属于某个资料库的入口（如 our5etools 的资料包）在该库勾选时出现。书架与 Stella 的书架相同，按分隔标题（核心规则、规则扩展……）分组，后续资料库补上的分组写出资料库名。
- **逐层深入**：点击一行，在右侧打开下一层；所选的行高亮。屏幕上至多并排三层（窄于 1280px 时两层），更早的层级折叠成左侧的竖条，竖条写着层名，点击即展开回来（关闭其后刚好足够的层级）。每层先引用原文导语，接着列出本页的标题与有名表格（标“表”），再列出“这一步要选择”的选项集合与目录中的下级；职业另列子职与可用法术，背景列出授予的专长。子职按所属职业逐层下钻，先选职业，再按内容性质浏览该职业的子职并直接点进子职与其各级特性，同名多版本可原地切换；较长的职业或子职列表顶部提供即时筛选框（支持中英文名）。原书写成步骤的内容按顺序编号，层末的“上一项／下一项”在同级之间移动。
- **索引与详情**：只是列出去处的层（入口、章节目录、选项集合）始终是窄列。正文比导语更长或没有下级的层是详情：作为最深一层时展开占据其余宽度，按原书排版显示正文，其中的引用在右侧打开定义；宽度足够时，正文保持适合阅读的行宽，去处（阅读按钮、下级各组、上一项／下一项）列在右侧一栏；进入它的下一层后收回窄列，只留导语与去处。
- **交给两位向导**：“在 Stella 中阅读”以该层为阅读范围打开阅读器；“在 Anna 中查看”打开条目或该集合的筛选结果；带 Anna 头像的行（如按环阶列出的法术）直接进入查询表。最深一层是条目时，它成为 Stella 可以“翻到”的话题。
- **键盘**：层级从左到右，行从上到下。像文件管理器的分栏，焦点所在的行就是在右侧打开的那一行。每层只有一行进入 Tab 顺序。h、j、k、l 分别等同于 ←、↓、↑、→；输入法正在组字时不响应。

  | 按键 | 作用 |
  | --- | --- |
  | ↑↓（k j）、Home／End | 在一层内移动，并在右侧打开移到的行；移到通向 Anna 的行时收起右侧各层。按住按键时经过的行不打开，松开或停留片刻后打开停下的那一行。手机上一次只显示一层，移动不打开 |
  | →（l）、Enter | 进入所在行打开的下一层，尚未打开时先打开：详情落在正文上，可用 ↑↓ 或 k j 滚动阅读，再按 → 进入它的行；索引落在打开过的行或首行，并打开这一行 |
  | ←（h） | 回到上一层中打开它的那一行；上一层已折叠时把它展开，关闭其后刚好足够的层级；手机上返回上一层 |
  | 空格、Shift+空格、PageDown／PageUp | 焦点在左侧各层时，翻动最深一层的正文 |
  | Esc | 关闭当前层右侧的各层，焦点不动 |
  | 详情首行上的 ↑（k） | 回到正文；详情右侧已打开的层级随之关闭，详情重新展开 |
- **位置**：地址的 `at` 参数记录从第一列到当前层的路径，可以分享，刷新和前进后退回到同一层级。用键盘移动时，打开更深的一层记一步历史，在已打开的层级之间换行只替换当前这一步。某一层在当前快照中已不存在时，导览回到它的上一层并提示。760px 及以下一次显示一层，顶部的“‹ 上一层”返回。

`tour/tour.json`（`schema: 2`）是导览唯一的手工数据，所有资料库共用：`sections` 列出第一列的分组与主题；`choices` 先按资料库键分组，再以该库的目录标签路径为键，列出该节点（如“第一步：选择职业”）通向哪些主题。主题的字段如下：

| 字段 | 含义 |
| --- | --- |
| `key` | 地址中的段名，由小写字母、数字与连字符组成；不能形如目录节点 `n123` 或条目 `ver_…` |
| `label` | 中英文名称，`zh`、`en` 均必填 |
| `ruleset` | 可选，`5e` 或 `5r`，在入口旁标出规则代际 |
| `contents` | 可选，按资料库键给出各库目录节点的标签路径，以 ` / ` 连接，如 `{"5echm": "玩家手册2024 / 第二章：创建角色 / 创建你的角色", "our5etools": "玩家手册（2024） / 第 2 章：创建角色"}` |
| `sequence` | 可选，为 `true` 时把该节点的下级作为步骤编号 |
| `query` | 可选，Anna 的查询参数，如 `{"type": ["spell"]}`；入口显示条目数并提供“在 Anna 中查看” |
| `facet` | 可选，配合 `query` 按一个筛选字段（`type` 或 `spell_level` 等领域字段）列出各取值的条目数，点击进入查询表 |
| `browse` | 可选，目前为 `subclasses`，按所属职业在导览内下钻并列出各资料库中的子职条目（不能与 `facet` 同时使用） |
| `dynamic` | 可选，按资料库键指定该库自己的列表，目前为 our5etools 的资料包列表 `packs_ours`、`packs_community`、`packs_official` |

每个主题至少有 `contents`、`query`、`browse` 或 `dynamic`。`query` 向整个资料库提问，`contents` 在每个列出的资料库中各自解析。标签路径必须唯一对应一个目录节点：资料库设置中的各库都无法唯一解析时，入口标为“未找到对应原文”或“原文对应不唯一”，`choices` 中的该项不出现，不按近似名称替代；只在未勾选的资料库中有章节的入口不显示。文件格式无效时 `/api/tour` 返回 500 并说明原因。修改后刷新页面即生效，无需重建；原文更新后运行 `make test-unit`，`tests/test_tour.py` 会核对每条路径仍唯一解析。

### 条目与原文联动

- **定义 → anna**：点击原文定义标题，在新标签页打开对应 anna 条目；没有独立标题的段落使用首段文字作为入口。
- **anna → 定义**：详情中的“在 Stella 中阅读”在新标签页的阅读器中定位对应条目。“查看提取文本与条目范围”仍用于核对数据库中的提取结果。
- **引用 → 定义**：原有链接、已识别的格式标记、有充分证据的普通正文提及，以及有字段或表列依据的法术参数、武器词条和职业能力，都可在当前页面定位到适用定义；在阅读器中使用时继续留在当前内容框架。无法唯一确定目标的原链接保留原样，待核提及不添加链接。识别与消歧规则见[引用发现与裁定](MODEL.md#引用发现与裁定)。

## our5etools 资料库

`our5etools` 作为独立资料库（键名 `our5etools`）接入 Anna、导览与 Stella，与默认不全书（`5echm`）及预留的纯中文版 `5etools` 接口（`5etools`，默认数据目录 `data/5etools/`）各自保存条目、证据与身份，不按名称互相回退，也不会改写或替代 `COUNTERPARTS_DIR` 指向、仅用于不全书旁证的 `5etools-cn` 对照镜像。读者在[资料库设置](#资料库设置)中勾选 `our5etools` 后，Anna 把它与不全书合成一张表，同名的出版物与家酿合为一行；导览与 Stella 把它的书目排进同一个书架，两库都有的作品只列一次并可切换文本。

### 构建、增量更新与回滚

```sh
# 使用 Makefile（可用 OUR5ETOOLS_DIR / OUR5ETOOLS_IMG_DIR / OUR5ETOOLS_DATA / 5ETOOLS_DATA 覆盖默认路径）
make our5etools
make our5etools-audit

# 直接调用模块命令行
.venv/bin/python -m anna.our5etools.build --our5etools /绝对路径/our5etools --our5etools-data data/our5etools [--img-dir /绝对路径/5etools-img] [--collection our5etools] [--quiet]
.venv/bin/python -m anna.our5etools.audit --our5etools-data data/our5etools --output data/our5etools/audit.md [--json]

# 启动服务（默认完全离线自包含；加 LAZY_MEDIA=1 / --lazy-media 或 ANNA_LAZY_MEDIA=1 开启远端配图按需懒加载与本地落盘缓存）
make up [LAZY_MEDIA=1]
.venv/bin/python -m anna.server.app --port 8000 --web ../5echm-web --our5etools-data data/our5etools [--5etools-data data/5etools] [--lazy-media] [--watch]
```

构建过程先校验源镜像（`data/`、`__api/books.json`、`homebrew/_recipes/all.json`），再写入同级临时目录 `<output>.tmp.<pid>`；其中随包打包源镜像全部本地媒体至 `media/`，并保留此前已懒加载缓存的 `media/img/` 与 `media/ext/` 图像。全部表、阅读章节、媒体清单与文件校验通过后，经 `.prev` 原子替换目标目录并在成功后清理备份。任意阶段失败时删除临时目录，保留既有 `data/our5etools/` 快照不受破坏。相同源镜像重复构建保持幂等（相同 `snapshot`，`delta` 零增删）。`make upper`（`--watch`）在 `data/our5etools/manifest.json` 或预留 `data/5etools/manifest.json` 更新并稳定后自动热重载对应资料库。

当前实测全量构建（321 个配方自制包 + 1 个站点科普漫画 + 官方数据，盘点全部 2,048 个镜像文件）耗时约 42 秒，产物 `data/our5etools/` 总计约 719 MiB（`catalog.sqlite` 约 544 MiB，`reader/` 约 132 MiB，`media/` 约 38 MiB 收录全部 816 个本地媒体文件），包含 61,480 条条目（29 类）、541 个来源、323 个资料包、50,009 条关系、2,589 个同包重印叠放组、378 册书目与冒险（2,436 章、18,013 个目录节点）；常规 SQLite 筛选计数与分页查询耗时在 50 ms 以内。

### 只读产物与诊断对账

构建产物集中输出于 `data/our5etools/`，只读服务与审计命令共同消费，运行时完全自包含、不触碰源镜像：

| 文件／目录 | 用途 |
| --- | --- |
| `catalog.sqlite` | 只读查询库，包含 `entries`、`entry_sources`、`entry_facets`、`sources`、`packages`、`identity_groups`、`identity_members`、`relations`、`book_pages`、`book_search` 与 `metadata`（含 `collection='our5etools'`） |
| `reader/` | 2,436 页 Stella 章节 HTML（`books/`、`adventures/`、`brew-books/`、`brew-adventures/`）、`anna-assets/` 样式与脚本及 `anna-reader.json`（`schema: 6`） |
| `media/` | 快照随包的全部 816 个本地媒体文件（715 个音频、68 个字体、33 个根图像）以及 `--img-dir` 打包图像与按需懒加载落盘缓存的 `img/`／`ext/` 媒体；由 `/our5etools-media/` 只读提供 |
| `media-remotes.json` | 22,271 项未离线打包远端媒体路由与允许上游 URL 映射，供 `--lazy-media` 开启时按需回源获取与校验 |
| `manifest.json`、`summary.json` | 本次快照摘要、分类／来源／包统计与相对上一快照的 `delta` |
| `inventory.json` | 源镜像全部 2,048 个文件的分类处置（导入位置或排除原因）与分片完整性校验 |
| `reconciliation.json` | 与 `__api/stats.json` 和 `__api/books.json` 的输入输出对账、`_copy`／变体／子种族／孤立背景文本诊断、`{@...}` 链接解析与未解析样例、未知标签／结构块及本地与远端媒体统计 |
| `attribution.json` | 全部资料包与来源署名、许可注记及媒体说明 |
| `audit.md` | `make our5etools-audit` 输出的中文审计报告，校验 19 项数据、自包含媒体包、来源标识与阅读器不变量（全部通过退出 0，任一失败退出 1） |

### 导览与 Stella 整书阅读

- **查询与筛选**：在顶栏「资料库」中勾选 `our5etools` 与其他资料库一同查询（只勾选它即只看这一库），支持中文与英文名称、正文检索、`source`／`package`／`origin`／`ruleset` 与领域分面筛选（含排除条件 `not_*`）、同包 5e／5r 重印叠放与展开、以及带 UTF-8 BOM 的 `5e-anna-our5etools.csv` 导出（含 `资料库` 列 `our5etools`）。详情页来源卡片展示 `资料库：our5etools`、来源性质、内容包、作者、译者、分档评分、`_copy` 继承来源、本地音频播放与远端配图提示，所有 `{@...}` 与 `{@filter ...}` 链接指向 our5etools 自己的条目。
- **导览**：our5etools 的章节、书目、资料包与条目作为统一导览的下层出现（地址段形如 `our5etools:n_book_xphb`、`our5etools:book.…`、`our5etools:pack.…`）；`tour/tour.json` 为它给出各主题的章节路径与目录节点上的选择，并以 `dynamic` 列出资料包。书目与资料包的“在 Anna 中查看”按其来源或资料包限定条目；超过 24 项的分组内置即时筛选框。
- **整书阅读与媒体**：Stella 从 `/our5etools-book/` 加载 our5etools 的章节，与其他资料库同在一个书架上，支持目录导航、章节分页、全文搜索、定义与引用定位以及刷新后位置恢复；`/our5etools-media/` 完全从 `data/our5etools/media/` 提供音频、字体与图像。默认运行严格离线，未离线收录的配图显示单行「远端配图（未离线收录）」提示；加入 `LAZY_MEDIA=1`、`--lazy-media` 或环境变量 `ANNA_LAZY_MEDIA=1` 后，配图按需从白名单上游取回并缓存到 `data/our5etools/media/{img,ext}/`，之后恢复默认离线模式也能直接复用。离线策略与缓存机制见[内容与导航边界](web/README.md#内容与导航边界)。
- **预留独立 `5etools` 接口**：`CollectionRegistry`、`load_fivetools_collection()`（读取 `--5etools-data`，默认 `data/5etools/`）、`/5etools-book/` 与 `/5etools-media/` 仍保留给独立的纯中文版 `5etools` 资料库，构建后与其他资料库一样进入合表、导览与书架；未单独构建时界面仅显示 `5etools（未接入）` 占位，与 `our5etools` 严格分离。

### 来源署名与媒体

`data/our5etools/attribution.json` 记录官方、合作和社区作品的来源及署名。社区与站点自制包保留原作者、译者、来源链接和 `_meta` 许可注记。GitHub Pages 发布全部已构建的作品和随包媒体，并附上 [our5etools 署名清单](https://5esimforall.github.io/5e-anna/our5etools-attribution.json)及上游字体许可文件。

Pages 优先显示随包图片。其余配图由浏览器按需从上游 CDN 或来源图床加载，取不到时保留配图提示。使用本机服务时仍可启用 `--lazy-media` 按需缓存，或在构建时通过 `--img-dir` 打包离线配图。

## 来源对照（5etools-cn）

[5etools-cn](https://github.com/tjliqy/5etools-cn) 独立整理了 WotC 官方内容，中文正文大量取自不全书。`make build` 在生成数据库后把 anna 条目与它对照，回答两个问题：这个条目在另一份资料里是谁；两边说法哪里不一致。对照结果只作旁证，不改写原文、条目分类或已提取的值，也不参与查询与链接版。镜像位置与跳过方式见[路径与环境](#路径与环境)；来源对齐、条目匹配、核对线索与结果表见[外部对照](MODEL.md#外部对照)。

对照结果写入 `catalog.sqlite` 与 `summary.json`。`make audit` 校验结果与候选、外键及计数一致，并在[提取审计](data/extracted/audit.md)中按类型列出对照结果与线索数量。

5etools-cn 的中文数据采用 CC BY-NC-SA 4.0。对照结果用于本机核对；若对外发布包含其中文内容的派生数据，需署名、非商用并以相同许可共享。

## 引用审核：保存、重建、撤销

具体引用裁定（`confirm`、`non_reference`、`missing_definition`）追加保存在仓库内的 `annotations/annotations.jsonl`，不属于可删除重建的 `data/`；通过 `python -m anna.review.repository` 读取与写入，Anna 与 Stella 只读消费有效结果。事件字段、有效引用的状态与点击层规则见[存储与有效结果](MODEL.md#存储与有效结果)。

先运行 `make build`，然后查询并检查一处引用：

```sh
# 列出普通正文中的待核引用；也可使用 --signal explicit / index / format / field / table
.venv/bin/python -m anna.review.repository references --signal plain --status review --limit 10

# 查看引用上下文、可迁移锚点、目标选择器、候选与双哈希依据
.venv/bin/python -m anna.review.repository ref-inspect 引用ID --target 目标条目ID
```

`ref-inspect` 返回引用锚点（`within`、`quote`）、目标条目选择器以及 `content_sha256`、`context_sha256` 双哈希。核对后用这组指纹写入确认裁定：

```sh
.venv/bin/python -m anna.review.repository annotate 引用ID \
  --kind reference --ruling confirm --target 目标条目ID \
  --expected-content-sha256 查看得到的content_sha256 \
  --expected-context-sha256 查看得到的context_sha256 \
  --reviewer 审核者 --reason '说明此处引用该定义的原文与上下文依据'
make build
# 停止旧服务，再重新运行
make up
```

一次裁定锚定于该引用所在上下文及文本片段；目标可以是候选外的现有可定位条目，但须提供核对理由。保存只写入 `annotations/annotations.jsonl`，须重建才生效；同一固定输入快照允许连续保存多项裁定，发布前由 `require_current_annotations` 校验日志已完整重放。

若确认此处不是规则引用或记录缺少独立定义，先不带 `--target` 运行 `ref-inspect 引用ID`（登记缺失定义时加 `--missing-definition`），再写入 `non_reference` 或 `missing_definition` 裁定：

```sh
.venv/bin/python -m anna.review.repository annotate 引用ID \
  --kind reference --ruling non_reference \
  --expected-content-sha256 查看得到的content_sha256 \
  --expected-context-sha256 查看得到的context_sha256 \
  --reviewer 审核者 --reason '说明此处的非规则语境'
make build
```

撤销时追加一条 `revoke` 事件，保留历史并在重建后恢复自动结果：

```sh
.venv/bin/python -m anna.review.repository revoke ann_ref_事件ID \
  --reviewer 审核者 --reason '说明撤销原因'
make build
```

重新裁定同一引用时，重新 `ref-inspect` 并在 `annotate` 命令增加 `--supersedes 旧事件ID`。同一引用范围内存在多个竞争决策时，不按后写覆盖前写，必须显式替代或撤销。

### 候选搜索与缺失定义（命令行）

条目详情不再提供核对模式或内联候选抽屉；候选检索和缺失定义队列通过命令行查看。候选排序只表示证据匹配程度，不自动批准关系：

```sh
.venv/bin/python -m anna.review.repository candidates 引用ID
.venv/bin/python -m anna.review.repository candidates 引用ID --query '名称或路径关键词' --limit 20 --offset 0
.venv/bin/python -m anna.review.repository missing --status open
```

登记缺失定义时先 `ref-inspect 引用ID --missing-definition`，再用 `--kind reference --ruling missing_definition` 写入（可通过 `--query` 保留搜索关键词）；队列保存在 `catalog.sqlite` 的 `missing_definitions` 表。定义补齐后重新 `ref-inspect --target` 并用 `annotate --kind reference --ruling confirm --supersedes 缺失登记事件ID` 显式解决，问题状态随之变为 `resolved`。

### 依据变化后的迁移与重新待核

原文更新后，裁定按内容／上下文双哈希依据迁移：无关改动保持 `applied`，被标注语境或目标变化时进入 `stale`、`missing` 或 `conflict` 等状态，判定规则见[双哈希依据](MODEL.md#双哈希依据)。按状态列出需要重新核对的裁定：

```sh
.venv/bin/python -m anna.review.repository annotations --kind reference --status stale
.venv/bin/python -m anna.review.repository annotations --kind reference --status missing
.venv/bin/python -m anna.review.repository annotations --kind reference --status conflict
```

修改了 `annotations/annotations.jsonl` 但尚未 `make build` 时，构建门禁会拒绝以未重放日志生成链接版或启动服务。修改名称解析规则（见[名称与括号结构](MODEL.md#名称与括号结构)）后需要重建数据库和链接版；既有审核决策仍按上述依据变化规则重新判定。

隔离试验时可传入 `--annotations 临时日志路径` 与 `--db 临时数据库路径`；不要把实验裁定写入仓库的 `annotations/annotations.jsonl`。

## 命令行查询与审计

提取与核对规则记录在[数据模型](MODEL.md)：条目分类与名称解析见[自动提取](MODEL.md#自动提取)，概念发现与引用见[引用发现与裁定](MODEL.md#引用发现与裁定)，5etools-cn 对照见[外部对照](MODEL.md#外部对照)。本节列出查看与核对这些结果的命令。构建产物集中在 [catalog.sqlite](data/extracted/catalog.sqlite)、[summary.json](data/extracted/summary.json)，审计后另生成 [audit.md](data/extracted/audit.md) 与 [audit.json](data/extracted/audit.json)；当前构建的覆盖、分类变化和待核项见[提取审计](data/extracted/audit.md)。

### 命令行查询

查询（复用 `anna.presentation.catalog` 只读消费层）：

```sh
python3 -m anna.catalog.query --type feat --source PHB24
python3 -m anna.catalog.query --preset 魔法武器 --ruleset 5r
python3 -m anna.catalog.query --type spell --field school=塑能 --csv > evocation.csv
```

`--preset` 支持 `NPC`、`伙伴`、`坐骑`、`魔法武器`、`战斗风格` 与 `传奇恩惠`；`--csv` 输出 UTF-8 CSV，`--show 版本ID` 查看正文、字段、关系与有效引用，`--stats` 查看覆盖与异常统计。来源多选取并集，其余条件取交集。

### 结构审计

结构审计只读数据库，校验表结构、外键与统计一致性，`make audit` 会运行它，需要传参时直接调用：

```sh
.venv/bin/python -m anna.catalog.audit
```

可用 `--baseline /绝对路径/旧版catalog.sqlite --output /报告目录` 生成逐条增删及分类／字段差异。审计不等于全库人工核验；未支持表格和无法唯一关联的动物保留在审核队列。

审计同时汇总分类缺口与类型冲突，供补充识别规则时决定先读哪里。缺口涵盖没有任何已分类条目的正文页、位于已分类条目之外且自身正文出现豁免 DC／骰子伤害／骰值表的未分类段落，以及每个信号的原文位置；数据卡内的“动作”等段落属于该数据卡，不计入。缺口与冲突均为线索，不作为审计失败条件；明细保存在 [audit.json](data/extracted/audit.json) 与 `catalog.sqlite`，带 `--baseline` 时[提取审计](data/extracted/audit.md)对照修改前后的变化。类型冲突判定顺序见[自动提取](MODEL.md#自动提取)。

### 概念发现与覆盖核对

`make build` 同时生成概念发现线索与有效引用，覆盖汇总写入 `metadata.concept_coverage` 与 `summary.json`。命令行可通过 `anna.review.repository references` 与 `candidates` 查看有效引用和候选；`references --signal` 按发现信号筛选：法术成分和持续时间的标签及取值用 `--signal field`，武器表、法术列表的学派和本表图例用 `--signal table`，正文骰值后的伤害类型用 `--signal plain`。回归样例由 `tests/browser/links.spec.cjs` 覆盖。

`make audit` 检查发现记录的范围、候选身份、状态与目标一致性和覆盖计数，不能替代语义审核。跨来源等价关系、缩写与别名的人工审核以及审核后的迭代扩展尚未实现。修改样式约定或导航目录后需要重新执行 `make build`；仅执行 `make linked` 会拒绝沿用过期的概念发现依据。

### 逐处核对原文引用

`make build` 后运行 `make audit-links`。命令从原文字段、表列及骰值伤害句式枚举出现处，而非只统计已经生成的链接；它核对原文偏移、Stella 点击范围、Anna 目标条目、适用来源代际及定义落点。报告写入 `output/reference-coverage/occurrences.csv` 和 `summary.json`。核对范围包括法术字段及取值、武器表、法术列表学派与图例、正文骰值伤害，不代表全文所有规则用法。

需要检验一次实现变更是否影响既有点击时，先保留旧目录数据库，再从**同一原文快照**重建并运行：

```sh
.venv/bin/python -m anna.catalog.reference_coverage --baseline /旧构建/catalog.sqlite --output /报告目录
```

缺少入口以 `missing` 及原因记录；落点不符标为 `wrong_target`。落点不符、同快照旧点击被移除或改指、以及快照不一致都会使命令以非零状态退出。换用新原文时不传 `--baseline`，对新快照重新盘点。

## 自动化验证

`make test` 同时运行单元测试和 Playwright 浏览器回归；`make check` 再加数据审计。首次需要 Node.js 20+ 与 npm，并执行 `make browser-setup` 安装 `package-lock.json` 锁定的开发依赖及 Chromium；运行 anna 本身仍只需 Python 依赖。单独运行某层检查：

```sh
make test-unit
make test-browser
make check
```

浏览器测试在单一本机端口启动 anna，原文排版对照仅通过测试专用的 `/__original/` 路径提供，结束后停止，不复用或关闭正在开发的站点。默认使用 18380；冲突时可用 `ANNA_E2E_PORT=18480 make test-browser` 更换测试端口。测试读取已构建的数据；快照不一致时应先 `make build`。也可以用 `ANNA_E2E_DB=/绝对路径/catalog.sqlite` 和 `ANNA_E2E_BOOK=/绝对路径/链接版目录` 指定独立构建的配套产物；测试仍校验整个发布快照，不跳过一致性检查。

测试涵盖定义／引用跳转、阅读器刷新恢复、键盘焦点、滚动、窗口变窄和表格节点搬移。用例默认在减少动态效果下运行，界面状态不等动效结束；`tests/browser/motion.spec.cjs` 允许动效，检查各处动效的方向、恢复时静止与状态立即生效（见[动效](web/README.md#动效)）。排版测试在同一 Chromium、视口和原文快照下同时渲染原版与链接版，对比可见文字、元素几何位置和截图像素，不以人工接受的历史截图作为基准。法术页、装备表、怪物数据卡和引用表分别在 1280px／390px 宽度检查页首与中段。

报告保存到 `output/playwright/report/index.html`；截图、失败差异图和失败 trace 保存到 `output/playwright/results/`，均不入库。浏览器异常、定位绑定失败或任一像素差异都会令测试失败。可以用 `npm run test:browser:report` 查看报告。浏览器用例位于 `tests/browser/`。

## GitHub Pages 发布

不全书和 our5etools 是当前两套可查阅的资料库，两套均完整发布。Anna 可查询全部条目，导览与 Stella 可查阅全部已构建章节。our5etools 包含官方、合作及社区内容，随站提供已打包的音频、字体、图片、署名和许可注记。远端配图的加载方式见[来源署名与媒体](#来源署名与媒体)。

5etools-cn 镜像用于构建时对照来源，独立中文版 5etools 资料库仍为预留接口。公开站点包含查询、导览与阅读所需产物。构建审计和原文输入镜像用于维护流程。

[GitHub Pages 工作流](.github/workflows/pages.yml)按 `sources.json` 固定的镜像版本构建两库的查询与阅读产物，每次推送 `main` 或手动运行时部署。站点将全部条目的详情与预渲染排版打包为 `pages-entries/` 哈希压缩分桶，点击条目时由浏览器主线程直接异步读取，并在多资料库需要跨库叠放版本时异步补拉 `/api/entry-versions`；列表查询、筛选计数、导览与 Stella 阅读继续通过 Web Worker 执行同一套逻辑，按需读取 `pages-data/` 的 256 KiB 压缩数据分片；不全书的条目与导览排版在构建时预渲染，工作线程不携带也不运行浏览器端 HTML 解析器，目录与整页阅读清单只在打开 Stella 或导览时按需读取。工作流预先计算跨库作品对应关系，按两套资料库的快照绑定并随站点发布。Pages 读取这份结果，本机服务仍按当前加载的资料库计算。

工作流按[源码版本锁](#源码版本锁)检出三个镜像，镜像路径通过环境变量 `WEB_DIR`、`OUR5ETOOLS_DIR` 与 `COUNTERPARTS_DIR` 传入，再依次运行 `make build`、`make test-unit` 与 `make pages`：单元测试读取刚构建的数据，通过后才打包上传。已有构建产物时，`make pages` 校验并复用它们生成静态预览包：

```sh
make pages PAGES_BASE=/5e-anna/ PAGES_OUTPUT=output/pages
```

本地和 Pages 的默认首页使用同一套代码读取首屏缓存。缓存与当前资料库不匹配时，本地服务回到实时查询。搜索、分页、条目链接和资料库设置等按各自的请求参数查询。本地通过 HTTP API 查询，Pages 在首屏显示后趁浏览器空闲在后台预热工作线程（直接打开深层链接或跳过缓存时按需立即启动）。

不全书原文来自 [DND5eChm/DND5e_chm](https://github.com/DND5eChm/DND5e_chm) 与 [5echm_web](https://github.com/DND5eChm/5echm_web)，发布包包含上游许可证。链接版在原文上添加引用和定位层，供 Anna 与 Stella 互通。
