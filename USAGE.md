# 使用

## 统一命令

以下命令在 `5e-anna` 目录运行。上层 DnD 的 Makefile 只转调其已列出的常用命令，不维护另一套实现；`icons` 等维护目标在本项目中执行。

| 命令 | 用途 |
| --- | --- |
| `make clone` | 原文或 5etools-cn 对照镜像缺失时下载；已有镜像保持不变 |
| `make build` | 从同一原文快照重建不全书数据库（含 `book_search`）与链接版；存在 our5etools 镜像时同步执行 `our5etools-build` |
| `make our5etools-build` | 从 `OUR5ETOOLS_DIR` 构建完全自包含的 `data/our5etools/` 查询库、Stella 整书章节、随包媒体（`media/`，可选 `OUR5ETOOLS_IMG_DIR`）与对账清单 |
| `make our5etools-audit` | 核查 `data/our5etools/` 的不变量并生成 `data/our5etools/audit.md` |
| `make up` | 启动单端口查询、导览、阅读与正文资源服务（存在 `data/our5etools/manifest.json` 时自动挂载 `our5etools` 资料库；`data/5etools/` 预留给独立中文版 `5etools`；可用 `LAZY_MEDIA=1` 开启远端配图按需懒加载缓存） |
| `make upper` | 开发用：同上，不全书或 our5etools 重建完成后自动校验并热重载 |
| `make icons` | 从原始人物图生成界面图标 |
| `make linked` | 由现有不全书数据库生成链接版，并校验原文快照 |
| `make test` | 运行单元测试和浏览器回归（需先构建数据） |
| `make test-unit` / `make test-browser` | 分别运行单元测试 / 浏览器回归 |
| `make browser-setup` | 安装锁定的 Node 依赖与 Chromium，首次运行浏览器测试前执行 |
| `make audit` | 审计不全书数据库与生成统计；存在 `data/our5etools/manifest.json` 时同步执行 `our5etools-audit` |
| `make audit-links` | 从原文逐处复核字段、表列及骰值伤害的引用落点 |
| `make check` | 运行测试与审计 |
| `make update` | 显式拉取原文与 5etools-cn 更新；之后运行 `make build` 更新数据 |
| `make status` | 查看原文、5etools-cn、our5etools 镜像及构建产物状态 |
| `make clean` | 删除本地生成数据、报告、图标和依赖环境；保留源码、独立输入与共享镜像 |

命令清单集中在本页；不带目标运行 `make` 只输出本页位置。下文的 `python -m anna.…` 模块命令在 anna 仓库根目录运行；应用代码不再通过 `scripts/` 路径启动。

默认原文位于 anna 同级的 `5echm-web/`。`WEB_DIR=/绝对路径/镜像` 可指定其他位置，也支持相对路径（相对于执行 make 的目录）。两层 Makefile 之间传递绝对路径，避免切换工作目录后指向另一处。直接运行脚本也可通过环境变量 `WEB_DIR` 配置原文；`python -m anna.catalog.build --source` 可进一步覆盖该默认值。

[来源对照](#来源对照5etools-cn)所用的 5etools-cn 镜像默认位于 anna 同级的 `5etools-cn/`，只检出 `data/`。make 参数 `5ETOOLS_DIR=/路径`（以及 `5ETOOLS_REPO`、`5ETOOLS_BRANCH`）指定其他位置或来源，相对路径同样相对于执行 make 的目录；上层 Makefile 只换算 `WEB_DIR`，在上层运行时 `5ETOOLS_DIR` 请写绝对路径。以数字开头的名称不能用 shell 直接赋值或 `export`，请写在 make 参数中，或用 `env 5ETOOLS_DIR=… python3 …` 传给脚本。直接运行 `python -m anna.catalog.build` 时可用 `--5etools` 指定镜像，镜像缺失时构建失败并提示下载；`--no-counterparts` 跳过对照，摘要记为 `skipped`。

[our5etools 资料库](#our5etools-资料库)是独立的社区中文资料库（`collection=our5etools`），与默认不全书（`5echm`）、预留的独立中文版 `5etools` 接口（`collection=5etools`、`5ETOOLS_DATA ?= data/5etools`）以及仅稀疏检出 `data/` 作旁证的 `5ETOOLS_DIR`（`5etools-cn` 对照镜像）分开管理。通过 `OUR5ETOOLS_DIR=/路径` 指定本地镜像（未指定时由 `anna.paths.OUR5ETOOLS` 依次探测同级 `our5etools/` 与本机镜像），可选通过 `OUR5ETOOLS_IMG_DIR=/路径`（CLI `--img-dir`）打包离线 `5etools-img` 图像目录至 `data/our5etools/media/img/`，通过 `OUR5ETOOLS_DATA` 指定输出目录（默认 `data/our5etools`）。构建产物完全自包含，运行时只读取 `data/our5etools/`，不再依赖或访问 `OUR5ETOOLS_DIR`。`make build` 仅在目标目录存在 `data/` 和 `__api/books.json` 时自动触发 `make our5etools-build`，镜像缺席时安静跳过，不影响不全书构建。

构建时缺失的镜像会自动下载；已有镜像不会自动更新。`make update` 只更新两个镜像，之后运行 `make build` 才更新数据库、库内全文索引和链接版。`make clean` 只清理本项目，不删除两个共享镜像。

Stella 全文搜索索引作为 `book_search` 表直接随 `make build` 写入 `catalog.sqlite`，并按原文路径、目录与 HTML 内容指纹（`book_search_signature`）校验；若启动服务后原文发生未重建修改，会自动退回目录即时扫描。默认优先使用 anna 的 `.venv/bin/python`，未创建时使用 `python3`；可用 `PYTHON=/绝对路径/解释器` 覆盖。`ANNA_PORT` 配置查询、阅读与正文资源共用的端口，仅本机访问。

新环境先在 anna 中创建虚拟环境并安装依赖：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
make clone
make build
make browser-setup
make check
```

## 清理与重建

清理前停止正在运行的 anna 服务，然后执行 `make clean`。命令直接删除 `data/`、`output/`、`.cache/`、`.venv/`、`node_modules/`、中断构建目录、Python 缓存和由人物原图导出的界面图标，不备份，也不遍历指向项目外的符号链接。`assets/branding/` 原图与 `annotations/` 结构化标注输入保留。

清理后按上节重新安装依赖，执行 `make build` 恢复图标、查询库与链接版。只有运行浏览器测试才需要 `make browser-setup`。生成文件和历史测试报告不提交 Git。

## 阅读服务与正文生成

为避免查询条目与阅读原文来自不同快照，`make build` 顺序生成 `data/extracted/catalog.sqlite` 和 `data/5echm-anna/`。随后运行：

```sh
make up
# 自定义端口
make up ANNA_PORT=8101
# 开发：重建数据后自动换包，无需重启
make upper
```

默认打开条目查询 [anna](http://127.0.0.1:8000/)、[导览](http://127.0.0.1:8000/tour)或不全书阅读器 [stella](http://127.0.0.1:8000/read)。查询、阅读和 `/book/` 正文资源由同一个 HTTP 服务提供，`Ctrl-C` 或终止信号停止服务。启动会校验原文、数据库和生成文件的哈希，发现快照不一致、文件缺失或改动即拒绝启动，并提示重新构建。`make up` 不下载或更新原文，也不自动重建数据。

开发时用 `make upper` 启动同一服务。构建先发布数据库、再发布链接版清单，服务在清单变化并稳定后校验数据库、原文快照与全部产物，再一次性切换到新快照；构建发布新数据库后、换包完成前，查询、搜索和 `/book/` 正文返回 409，完成后恢复，不混用新旧数据。校验失败时保持 409 并给出原因，直到下一次成功构建。`make upper` 同样不下载、不更新原文，也不自动重建；修改 `anna/` 下的 Python 代码仍需重启，修改 `web/` 前端文件刷新即可。包内实现参与构建指纹；目录迁移或提取实现变化后，执行审核写入前需重新 `make build`，不沿用旧实现指纹。

阅读界面由 anna 提供，上游仓库只提供内容输入。生成目录不包含上游 WebHelp 入口；`/book/` 下的根路径、`index.htm`、`indexh.htm` 及 `webhelp*.htm` 均不提供阅读界面。使用 `make up` 或 `make upper` 启动。

### 阅读操作

侧栏底部显示原文「写在前面」中标注的不全书版本（如 `v20260913`），不是 anna 版本或构建哈希；原文没有唯一有效的版本声明时不显示。

- **目录**：栏目下方一行图标工具。左端放大镜展开筛选框，可查找书籍或章节；筛选框为空时，离开或按 Esc 即收回；筛选期间右端只保留“定位当前章节”。右端依次为“定位当前章节”“展开当前章节”（展开当前阅读范围的全部子节点，并把它排到目录顶部）和“全部收起”。点击目录名称，以该节点及其递归子节点建立阅读范围；同一页面在目录中的多个入口分别保留，阅读栏的位置路径从范围的上级一直写到当前页。清空筛选后恢复原来的展开状态。
- **目录跟随**：默认跟随当前正文页，必要时展开祖先并仅滚动到能看见当前项的位置。手动筛选、展开／收起或滚动目录后暂停跟随；“定位当前章节”图标跟随时为品牌色，暂停时变为普通图标色；点击即清空筛选并恢复跟随。目录关闭或切到“本页”“搜索”时不会自动打开或切换栏目。
- **连续阅读**：阅读范围按目录顺序包含选中节点自身与所有后代的可用正文。没有正文的分组从首个可读后代开始；叶子节点只包含自身。滚动进入下一页不会改变范围，也不会自动延伸到兄弟章节。同一文档的多个目录锚点共享一份正文。
- **加载与分页**：初次加载目标页，接近正文末尾时自动接续下一页，也可点击“加载下一页”手动接续；阅读栏左端为目录开关和位置路径，右端分页器显示当前页／总页数，左右箭头逐页移动。点开路径中范围名称之后的任一层，可在它的同级节点间跳转；末尾的“›”列出当前节点的下级；只是分组的节点打开其第一个可读页面。路径中的跳转不改变阅读范围；点击范围名称回到范围第一页，点击更上层的链接则建立新的范围。窄屏放不下的层级收进“…”。跳转时直接加载目标页，不逐页加载中间文档；缺口处显示未加载区间及接续按钮，远处正文按下述规则回收。自动或手动加载失败后保留已有正文，可原位重试，不自动反复请求。到最后一页显示“已到本章节末尾”。
- **远页回收**：阅读器仅保留当前位置附近的正文，远页释放后以等高占位保留位置，回滚接近时重新载入；也可点击占位上的“重新载入”。阅读范围、总页数与最后阅读位置不随回收改变。
- **本页**：按原文顺序列出可定位条目；点击名称进入定义，旁边的箭头在新标签页打开 anna。尚未提取成条目的页面仍可整页阅读，空列表不表示原文缺失。
- **搜索**：搜索本机完整原文，可限定页面标题。多词全部命中为空时，明确提示改为任一词语命中；“在条目查询中筛选”进入 anna 的结构化查询。结果分批翻页，失败后可重新提交。
- **正文**：沿用原文文字、表格、图片、顺序与样式，浅色／深色模式使用原版阅读器的正文配色，并与查询页共用主题偏好。正文铺满顶栏和阅读栏下方的阅读区，不重复显示页面标题；收起目录可获得完整窗口宽度。文字先于尚未下载完成的图片显示。
- **标出链接**：阅读栏右端的高亮图标。开启后，Anna 识别出的链接显示淡色底：蓝底是正文引用，点击在当前阅读器跳到定义；红底是定义标题，点击在新标签页打开 Anna 条目。默认关闭，正文保持原貌；选择保存在本浏览器，并同步到其他标签页。
- **继续阅读**：自动记住本浏览器最后的阅读范围、当前页面、目录入口和页内位置。关闭后重新打开阅读器，或从查询页转向 Stella，会回到该位置；恢复时只加载当前页面，其余页面按需接续。明确指定范围、页面、条目或锚点的链接优先。刷新和前进后退仍使用当前历史记录。正文内跳到范围内目标时保留范围，范围外目标建立新的阅读范围，后退恢复原处。构建快照变化后不套用旧位置；禁用或清除浏览器存储后不保留跨次阅读记录。
- **窄屏**：800px 及以下将导航收为抽屉，选中页面或条目后回到正文。目录支持方向键、Home／End 和 Enter；路径菜单支持 ↑↓、Home／End、首字跳转、← → 切换层级与 Esc；`/` 打开搜索，F6 在正文与工具栏之间切换，Esc 先清空目录筛选，再关闭抽屉。

刷新与前进后退会恢复正文位置、目录展开、当前导航栏目和搜索状态。分享地址携带阅读范围、页面／条目、原锚点与目录入口；主题与链接标记是本浏览器偏好。快照变化后不套用旧滚动位置，已消失或无法定位的条目明确报错，不按名称寻找替代目标。

### 在 Anna 与 Stella 之间

两页顶栏右侧、主题按钮左边是另一位向导：她的头像和一句去向。

- **转向她**：点击头像一栏，在当前标签页打开她。Anna 刚展示过、Stella 还没接过的条目，Stella 会“翻到”它；Stella 正在读的定义，Anna 会去“查”它。没有新话题时，她带你“接着读”或“回到”上次停下的地方；没有记录时打开她的首页。
- **最近打开的位置**：有多个选择时旁边出现“▾”，列出当前话题的条目，以及最近打开的最多 5 个位置，每项带另一位向导的头像（Anna 保存查询条件，Stella 保存阅读范围）。选中后恢复当时的列表滚动、已加载的行或页内位置。
- **多个标签页**：同一浏览器的标签页共享这些记录，一处打开条目，其他标签页的向导随即更新。重新构建数据后，Anna 只保留查询条件，Stella 的位置和正在谈论的条目重新开始。浏览器禁止存储时只能转到对方首页。

### 导览

[导览](http://127.0.0.1:8000/tour)是 Anna 的另一页。Anna 两页的顶栏品牌旁都有“导览／查询”切换，各自回到上次停下的地方。

- **从目的出发**：第一列按开始游戏、角色选项、城主资料、规则参考与书架分组。入口旁的年份标明规则代际，数字是下一层的行数或 Anna 中的条目数。书架按原书目录的分隔标题（核心规则、规则扩展……）分组。
- **逐层深入**：点击一行，在右侧打开下一层；所选的行高亮。屏幕上至多并排三层（窄于 1280px 时两层），更早的层级折叠成左侧的竖条，竖条写着层名，点击即展开回来（关闭其后刚好足够的层级）。每层先引用原文导语，接着列出本页的标题与有名表格（标“表”），再列出“这一步要选择”的选项集合与目录中的下级；职业另列子职与可用法术，背景列出授予的专长。原书写成步骤的内容按顺序编号，层末的“上一项／下一项”在同级之间移动。
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

`tour/tour.json` 是导览唯一的手工数据：`sections` 列出第一列的分组与主题，`choices` 以目录标签路径为键，列出该节点（如“第一步：选择职业”）通向哪些主题。主题的字段如下：

| 字段 | 含义 |
| --- | --- |
| `key` | 地址中的段名，由小写字母、数字与连字符组成；不能形如目录节点 `n123` 或条目 `ver_…` |
| `label` | 中英文名称，`zh`、`en` 均必填 |
| `ruleset` | 可选，`5e` 或 `5r`，在入口旁标出规则代际 |
| `contents` | 可选，目录节点的标签路径，以 ` / ` 连接，如 `玩家手册2024 / 第二章：创建角色 / 创建你的角色` |
| `sequence` | 可选，为 `true` 时把该节点的下级作为步骤编号 |
| `query` | 可选，Anna 的查询参数，如 `{"type": ["spell"]}`；入口显示条目数并提供“在 Anna 中查看” |
| `facet` | 可选，配合 `query` 按一个筛选字段（`type` 或 `spell_level` 等领域字段）列出各取值的条目数 |

每个主题至少有 `contents` 或 `query`。标签路径必须唯一对应一个目录节点：找不到或多处同名时，入口标为“未找到对应原文”或“原文对应不唯一”，`choices` 中的该项不出现，不按近似名称替代。文件格式无效时 `/api/tour` 返回 500 并说明原因。修改后刷新页面即生效，无需重建；原文更新后运行 `make test-unit`，`tests/test_tour.py` 会核对每条路径仍唯一解析。

### 条目与原文联动

- **定义 → anna**：点击原文定义标题，在新标签页打开对应 anna 条目；没有独立标题的段落使用首段文字作为入口。
- **anna → 定义**：详情中的“在 Stella 中阅读”在新标签页的阅读器中定位对应条目。“查看提取文本与条目范围”仍用于核对数据库中的提取结果。
- **引用 → 定义**：原有链接、已识别的格式标记、有充分证据的普通正文提及，以及有字段或表列依据的法术参数、武器词条和职业能力，都可在当前页面定位到适用定义；在阅读器中使用时继续留在当前内容框架。无法唯一确定目标的原链接保留原样，待核提及不添加链接。

链接版复制原文资源，在内容页末尾追加定位数据、隔离的点击层与阅读通信脚本，不重新序列化 HTML。定位使用原文文本节点及 UTF-16 偏移，点击层随滚动、窗口尺寸和原网页表格增强更新。目录从同一快照提取，正文与查询共用已有条目和有效引用结果；实现边界见[界面说明](web/README.md#整页阅读器)。

生成结果中的 `anna-manifest.json` 记录输入、输出哈希与条目定位，`anna-reader.json` 保存目录、页面及其条目列表；`anna-report.json` 记录定义数量、可点击定义、引用解析及待核清单。原文空页或缺失内容范围的记录无法生成可靠的可点击定位，定义路由会明确报错，不跳到同名的其他记录。正文范围为空但标题仍可定位的记录也单独列入报告，供提取侧核对。

格式标记依据页面实际引用的样式表解析，不按显示颜色推断类型。普通正文须有规则用语和来源依据才自动关联核心规则；其他词面匹配、未知格式、混合标记、示例语境和未确认的跨来源关系保留待核。逐处审核也可以确认普通正文的目标。适用范围见[概念发现与覆盖核对](#概念发现与覆盖核对)。

## 中英文名称与括号结构

条目名称共用 `parse_name` 解析；`split_name` 仅返回它的中英文两个显示字段。系统先核对成对括号和引号，再辨认双语边界：`中文名 (English Name)` 的外层括号视为英文对照名的包裹符，而 `English Name (Alias)`、中文别名及数值限定保留在对应名称内。大小写和拼写沿用输入，不因名称匹配而改写原名或合并条目身份。

一个名称可能并列几个双语名，例如价格表的一行写作 `1 磅丁香 cloves 或 1 头猪 pig`。若只取末尾英文，整行会被当成 `pig`。因此，当英文词后紧跟“或”、`/` 或“、”，下一段又以中文开始时，解析器逐段配对中英文（规则 `bilingual_alternatives`）：中文显示名保留原文的连接词，英文显示名以 ` / ` 连接各段。任何一段不能单独配对时整体不拆；中文名内部的“或”（`微型或小型`）和夹在中文里的拉丁词（`AHA成员`）都不触发拆分。

没有连接词时，前一个译名也可能直接接上下一个中文名，如 `蜥蜴之王Lizard King蜥蜴女王Lizard Queen`。前接中文、含小写字母的英文词串与随后的中文之间同样视为分界，中文显示名在此处补 `/`。首字母缩写和代号（`AHA成员`、`X射线戒指`）以及开头的拉丁词（`krig符文之旗`）属于它所在的中文名，不构成分界。含句读（，。？！；）的标题是在句中夹注术语，不按并列名称拆分；若这样的英文词串留在中文字段里，名称标为 `mixed_name_unresolved` 进入待核。

条目的 `name_parse` 保存原始候选名称、显示分段、分组角色、编辑标记及解析依据。`parts`、`groups` 和 `annotations` 的 `spans` 均为原始候选名称字符串中的 Unicode 码点范围，右端不含；它们不是 HTML 字节位置，也不是页面全文偏移。候选名可能来自正文或索引，页面定位与索引证据仍由条目自身的边界和发现记录提供。

职业能力标题常以等级开头（`3级：绝望打击 Despairing Strikes`）。开头的“N级：”是限定信息，解析器把它记为 `level_label` 注记并从显示名中移出，构建时据此写入 `level` 字段（规则 `name_level_label`，原文明示），职业能力视图按等级排序和筛选；只有“N级：”而没有后文的标题保持原样，名称内部的等级词（“一级额外专长”）不受影响。紧贴英文词的数字属于该词：`2级禁令恩惠 2nd-Level Interdict Boons` 的英文名是 `2nd-Level Interdict Boons`。

职业与子职的主条目由页面结构识别，不要求目录名出现职业名：生命值骰写明“每X等级”且页面同时有核心特质或熟练项时，主条目为职业；页面只列出从3级起的等级特性、没有更低等级的行时，主条目为子职。主条目确定后，第二遍分类把同页的“N级：”标题归为职业能力，因此 `血族职业`、`灵能` 这类目录也能继承类型。状态页同理：主条目标题为“状态”时，页内并列的双语定义归为状态；导语中的“持续时间”“状态不叠加”等通则仍保留为规则段落。

不配对的括号、无法确定的混合语言结构及空名称保留原始候选和待核原因；界面不展示被强行拆开的半个括号。`review_queue` 中的 `name_structure_unresolved` 提供核对入口，名称解析状态本身不代表已人工核准。修改解析规则后需要重建数据库和链接版；既有审核决策仍按审核闭环的依据变化规则重新判定。

## 引用审核：保存、重建、撤销

具体引用裁定（`confirm`、`non_reference`、`missing_definition`）追加保存在仓库内的 `annotations/annotations.jsonl`，不属于可删除重建的 `data/`。日志保留作者、理由、时间、内容／上下文双哈希依据以及 `supersedes`／`revoke` 历史。历史 `grant`／`procedure` 事件仍保留在日志中作为原始输入，但当前构建只重放 `reference`；通过 `python -m anna.review.repository` 读取与写入，Anna 与 Stella 只读消费有效结果。

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

`catalog.sqlite` 用同一张 `effective_references` 表保存全部发现记录（`is_effective=0` 为纯发现或被显式链接覆盖的观察，`is_effective=1` 为有效引用与点击层输入）；Stella 链接标记、导出与 CLI 检查使用同一组有效引用 ID。

- `status` 表示有效语义结果；`review_status` 区分未经人工审核、决策已采用、失效、冲突和已撤销。
- `can_link` 与 `presentation` 表示能否添加点击层及不能添加的原因。定义标题优先，原有链接范围不被其他提及覆盖，竞争的引用范围不按顺序取胜。
- 普通正文确认后可以生成定义跳转；“已关联”仍可能是自动推断，不等于人工核准。
- 对原有 `<a>` 的非引用结论、撤销或失效只影响 anna 新增的跳转层，原 HTML 链接保持原样；这类结果显示“保留原文链接，不添加跳转”。

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

登记缺失定义时先 `ref-inspect 引用ID --missing-definition`，再用 `--kind reference --ruling missing_definition` 写入（可通过 `--query` 保留搜索关键词）；队列结果保存在 `catalog.sqlite` 的 `missing_definitions` 表，状态变化由统一评估写入 `knowledge_annotations` 与 `review_queue`。定义补齐后重新 `ref-inspect --target` 并用 `annotate --kind reference --ruling confirm --supersedes 缺失登记事件ID` 显式解决，问题状态随之变为 `resolved`。

### 依据变化后的迁移与重新待核

引用裁定使用内容／上下文双哈希模型：保存锚点文段、最小上下文窗口、目标条目选择器、竞争定义状态、作用域与适用局部定义，不绑定易变的字节偏移和 `ver_...` 临时 ID。

- 原文仅发生无关邻段增删、注释变化或条目 ID 重新生成时，只要被标注引用语句、目标语义和局部竞争集合不变，裁定保持 `applied`。
- 引用语句或目标语义改写、目标无法定位或出现新增同名竞争时，裁定进入 `stale`、`missing` 或 `conflict`，结构化差异记入 `changes`，并在修正前阻止新增错误跳转。

```sh
.venv/bin/python -m anna.review.repository annotations --kind reference --status stale
.venv/bin/python -m anna.review.repository annotations --kind reference --status missing
.venv/bin/python -m anna.review.repository annotations --kind reference --status conflict
```

重放评估统一写入 `catalog.sqlite` 的 `knowledge_annotations`（`kind='reference'`）与 `review_queue`；修改了 `annotations/annotations.jsonl` 但尚未 `make build` 时，构建门禁会拒绝以未重放日志生成链接版或启动服务。

隔离试验时可传入 `--annotations 临时日志路径` 与 `--db 临时数据库路径`；不要把实验裁定写入仓库的 `annotations/annotations.jsonl`。

## 概念发现与覆盖核对

`make build` 同时生成概念发现线索与有效引用。发现信号、内容角色和处理状态保存在 `catalog.sqlite`，由链接版、导出与审计共同消费；命令行可通过 `anna.review.repository references` 与 `candidates` 查看有效引用和候选。

- `catalog.sqlite` 的 `effective_references` 表统一保存所有发现信号与有效引用：`is_effective=0` 保留未转化为提及链接的标题／定义句候选或被显式链接合并的观察，`is_effective=1` 保存可消费有效引用（其中 `automatic` 字段保留重放审核前的发现结果）。
- `resolved` 表示当前解析规则下唯一关联；`ambiguous` 表示适用范围内有多个目标；`unresolved` 表示未找到目标；`review` 表示需核对语义或上下文；`excluded` 表示本次不自动关联。
- `metadata.concept_coverage`（同时写入 `summary.json`）汇总按书籍、发现信号和状态的结果，以及未关联索引、未关联格式标记、未分类定义边界与歧义清单。

“本书速查”按导航子树收集，覆盖不同目录中的页面；已有术语索引继续作为入口。正文扫描同时登记下划线组合、未分类标题、定义句候选，并用已提取规则定义的名称反扫普通正文。格式证据保存标签嵌套结构、原始标记及约定来源；定义标题与已有引用不重复生成引用入口。同来源同名多目标保留歧义，不因某条进入速查就任意排除其他定义。

以下通用规则已接入构建：

- **核心规则子定义**：在来源名录已识别的核心规则中，从已识别的规则段落提取两类结构：明确引出的并列术语及各自的括号内释义；成组的双语行首小标题或并列规则句中的具名变体。后者要求子名能辨认出所属规则，通用的“攻击影响”等说明标签不独立成条目。子定义只保存自身原文范围，通过父条目保留规则、来源与规则代际；同名不合并，不从另一版补写原文没有的译名。
- **法术字段与武器表列**：用 `--signal field` 查法术成分和持续时间的标签及取值；用 `--signal table` 查武器表、法术列表的学派和本表图例。正文骰值后的伤害类型使用 `--signal plain`。已有格式标记承载同一引用时仍记为 `format`。记录保留字段原文、表头或定义族依据，名称与目标的约束见[数据模型](MODEL.md#引用发现与裁定)。
- **普通正文引用**：名称须处在可辨认的规则用语中，例如“获得〔规则术语〕”或其紧随的并列项；动作条目还要求执行动作的语境。检定写法本身也是规则用语，其中每个部分分别引用，并且只关联自己那一种定义：以“检定”“豁免”结尾的名称是检定本身（力量检定、死亡豁免检定）；紧接“检定／豁免”之前、可用顿号或“或”并列的短名称是受检的属性或技能（体质豁免、力量或敏捷豁免、隐匿检定）；括号写法之前是属性（感知（察觉）检定），括号里的各项是技能（魅力（欺瞒或游说）检定）。写法本身标明了种类，因此同名的表格或其他类型不与之竞争；名称没有该种定义时（“死亡豁免”中的“死亡”），写法不适用，仍按它所在的规则用语处理。较长的名称只在另一规则代际有定义时（2014 的“力量检定”出现在 2024 正文中），改用其中本代际有定义的最长名称，证据保留原先的较长名称。系统用所在最小条目的来源和规则代际约束候选，仅在核心规则目标唯一且无相关同名异类候选时自动关联。裸词匹配、定义自称、表格与有标记的示例仍待核。证据保留触发用语的范围、所属条目与解析依据；已有链接和格式标记的范围不重复反扫。
- **跨书引用**：来源已有明确规则代际时，在同来源没有目标的情况下查找该代际的核心规则来源；核心来源由资源名录的核心出版物及出版物族证据识别：英文出版物名与缩写词干共同对应，归档后的旧版仍保留对应代际的核心身份；不配置具体书名或条目名。类型与名称匹配后仍须唯一。结果标记为“按来源规则代际引用核心规则（推断）”，保留来源代际证据，不冒充原文明确指定了某一版书。
- **本地定义**：在已识别的子能力段落中，结合“名为／称为／称作”、格式类型及后续对象描述建立局部命名定义。提及指向承载规则的子能力条目，作用域为所属特性；`local_definitions` 表保存命名范围、定义条目与作用域，不将被比较的外部物品合并为别名。
- **特性边界**：职业上下文中，段落起始的“等级＋双语名称”识别为特性标题；该段内行首的加粗双语小标题识别为子能力，止于下一子能力或段落末尾。特性及子能力通过既有条目模型生成定义入口。

- **表格标题**：识别表内明确的 `caption`，以及紧邻数据表格的独立下划线、加粗或标题行；要求表格至少两行且具有多列内容，跳过嵌套表、数据卡容器、列内标记和纯骰点／数字标题。已有同位置标题复用原条目；新表题条目范围止于表格末尾，以“资料索引”分类保存，`table_span` 记录原表范围。表内商品或其他独立条目仍按既有规则提取，不与表格索引混为一条；表格排版本身不说明表内内容属于法术、装备或其他类型。表题可以打开 anna，已唯一解析的高亮表名引用可以在本页定位；同名表格依来源与上下文消歧，不按显示名建立全局唯一目标。

- **样式来源**：逐页检查实际链接的本地样式表，保存受支持约定的选择器、注释及文件哈希。`metadata.concept_coverage` 记录相关样式表和发现输入；仅重建链接版时会校验这些输入是否过期。页面仍有未知格式时继续待核。
- **高亮内多个引用**：对整个名称先做匹配，保留名称内的标点和限定；其余片段按顶层列表标点及双语名称间的空白边界拆分，在至少两个片段能匹配已有名称时才采用拆分。各片段分别消歧、保留未解析结果，共享原始格式标记的 ID 和范围；不把整段链接到最后一个名称。括号内内容、加值等限定不单独剥离。
- **职业能力作用域**：依据条目父子关系确定所属职业／子职范围，同名职业能力只有在该范围内才进入未限定术语引用的候选池。范围外的能力仍可检索和点击标题，但不会与全书通用术语竞争；原有显式局部命名定义继续优先。

`concept_coverage` 的 `format_marks` 统计原始格式标记，`outcomes.format` 统计逐个引用片段，两者在拆分后不再相等。没有可点击内容的空格式标记与真实的未关联高亮分开统计。阻塞原因记录的是当前解析阶段的结果，不是永久定性：解除一种阻塞后仍可能遇到版本或目标问题，不将新增可识别格式数视为新增正确链接数，各类阻塞数量也不能相加作为预计新增链接数。示例中的具体提及同样逐处判断，虚构人物不妨碍其中的真实法术或动作拥有引用目标。

上述规则应用于满足证据条件的页面，不依赖样本名称或路径白名单。回归样例由 `tests/browser/links.spec.cjs` 覆盖。

未命中结构规则的边界、定义句和缺少规则语境的词面匹配仍保留待核；跨来源等价关系、缩写与别名的人工审核以及审核后的迭代扩展尚未实现。扫描全部页面不等于语义覆盖完整。`make audit` 检查发现记录的范围、候选身份、状态与目标一致性和覆盖计数，不能替代语义审核。修改样式约定或导航目录后需要重新执行 `make build`；仅执行 `make linked` 会拒绝沿用过期的概念发现依据。

## 来源对照（5etools-cn）

[5etools-cn](https://github.com/tjliqy/5etools-cn) 独立整理了 WotC 官方内容：每条记录同时给出中文名、英文名（`ENG_name`）与 5etools 来源代码，并带有法术环阶、生物 CR、物品稀有度等机器字段和重印关系。它的中文正文大量取自不全书（`translator` 记为「不全书」）。`make build` 在生成数据库后把 anna 条目与它对照，回答两个问题：这个条目在另一份资料里是谁；两边说法哪里不一致。对照结果只作旁证，不改写原文、条目分类或已提取的值，也不参与查询与链接版。

**来源对齐。** anna 来源与 5etools 来源代码的对应有两种证据：资源名录中来源自身的缩写与代码相同；条目英文名在 5etools-cn 中只出现于一个来源，且票数至少 3、占该来源票数至少五分之一。缩写可能撞车（不全书的 CM 是第三方《歪曲之月》，5etools 的 CM 是另一本书），没有任何名称支持的缩写对应保持 `待核`；缩写指向的代码只得到零星支持、名称却集中指向另一代码时，该来源记为 `冲突`。只有 `上下文推得` 的对应参与条目匹配。

**条目匹配。** 每个已分类条目按类型限定可比的记录种类（如职业能力对应 classFeature、subclassFeature 与 optionalfeature），先以英文名、再以中文名在已对齐的来源中查找；名称比较忽略大小写、空白与标点。所属职业不同的记录被排除；同名记录多于一条时，再以所属子职、获得等级和所属职业的规则代际筛选，子职名比较时忽略 the／of。结果分为：

| 结果 | 含义 |
| --- | --- |
| `resolved` | 已对齐来源中唯一对应，`rule` 记录依据（`english_name`、`chinese_name`，经归属筛选时带 `+owner_context`） |
| `ambiguous` | 已对齐来源中有多个候选，或候选均属其他职业（`context_conflict`） |
| `other_source` | 同名记录只出现在未与本条目来源对齐的代码中，例如另一版本 |
| `unaligned` | 条目来源没有已对齐的代码，同名记录仅列为候选 |
| `unmatched` | 没有同名记录，多为 5etools 不收录的第三方、UA 与玩家包内容 |

**核对线索。** 唯一对应的条目逐项比较，只报告不一致：

- `field_differs`：法术环阶、学派、仪式、专注，生物 CR、体型，魔法物品稀有度、同调；不全书旧译「稀有」「罕见」按 rare 比较。继承字段的 `_copy` 记录不参与比较。
- `name_en_differs`／`name_en_missing`：英文查找名拼写不同，或 anna 未解析出英文名。
- `name_zh_differs`：记录标明译自不全书而中文名不同，通常是 5etools-cn 的术语统一或校对改动。
- `reprinted_as`：5etools-cn 的 `reprintedAs` 两端都唯一对应 anna 条目时，列出这对条目，作为重印或修订关系候选。
- `unclaimed_record`：已对齐来源中标明译自不全书、却没有任何 anna 条目对应或列为候选的记录，提示可能漏提或名称解析差异。

**产物。** `catalog.sqlite` 保存 `counterpart_records`、`counterpart_sources`、`counterparts` 与 `counterpart_findings` 表（其中包含 5etools-cn 记录在 JSON 中的位置），元数据 `counterpart_coverage` 汇总镜像提交、输入指纹与各类计数；`summary.json` 的 `counterparts` 记录同一快照。`make audit` 校验结果与候选、外键及计数一致，并在[提取审计](data/extracted/audit.md)中按类型列出对照结果与线索数量。

5etools-cn 的中文数据采用 CC BY-NC-SA 4.0。对照结果用于本机核对；若对外发布包含其中文内容的派生数据，需署名、非商用并以相同许可共享。

## our5etools 资料库

`our5etools` 作为独立资料库（`collection=our5etools`）接入 Anna、导览与 Stella，与默认不全书（`collection=5echm`）及预留的纯中文版 `5etools` 接口（`collection=5etools`，默认数据目录 `data/5etools/`）严格隔离：各库不合并条目、不按名称互相回退，也不会改写或替代 `5ETOOLS_DIR` 指向、仅用于不全书旁证的 `5etools-cn` 对照镜像。

### 构建、增量更新与回滚

```sh
# 使用 Makefile（可用 OUR5ETOOLS_DIR / OUR5ETOOLS_IMG_DIR / OUR5ETOOLS_DATA / 5ETOOLS_DATA 覆盖默认路径）
make our5etools-build
make our5etools-audit

# 直接调用模块命令行
.venv/bin/python -m anna.our5etools.build --source /绝对路径/our5etools --output data/our5etools [--img-dir /绝对路径/5etools-img] [--collection our5etools] [--quiet]
.venv/bin/python -m anna.our5etools.audit --data data/our5etools --output data/our5etools/audit.md [--json]

# 启动服务（默认完全离线自包含；加 LAZY_MEDIA=1 / --lazy-media 或 ANNA_LAZY_MEDIA=1 开启远端配图按需懒加载与本地落盘缓存）
make up [LAZY_MEDIA=1]
.venv/bin/python -m anna.server.app --port 8000 --our5etools-data data/our5etools [--5etools-data data/5etools] [--lazy-media] [--watch]
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
| `attribution.json` | 全部资料包与来源署名，以及不可随包二次分发资源清单 |
| `audit.md` | `make our5etools-audit` 输出的中文审计报告，校验 19 项数据、自包含媒体包、来源标识与阅读器不变量（全部通过退出 0，任一失败退出 1） |

### 导览与 Stella 整书阅读

- **查询与筛选**：在顶栏「资料库」切换至 `our5etools`（`/?collection=our5etools`），支持中文与英文名称、正文检索、`source`／`package`／`origin`／`ruleset` 与领域分面筛选（含排除条件 `not_*`）、同包 5e／5r 重印叠放与展开、以及带 UTF-8 BOM 的 `5e-anna-our5etools.csv` 导出（含 `资料库` 列 `our5etools`）。详情页来源卡片展示 `资料库：our5etools`、来源性质、内容包、作者、译者、分档评分、`_copy` 继承来源、本地音频播放与远端配图提示，所有 `{@...}` 与 `{@filter ...}` 链接保持在 `collection=our5etools` 内。
- **范围导览**：`/tour?collection=our5etools` 由 `tour/our5etools.json` 与库内书架、资料包动态组合，覆盖建角入门、施法与灵能、城主资料与遭遇、装备魔法物品与奇珍、规则参考、书架（官方规则书 65 册、官方冒险 100 册、社区扩展书籍 137 册、社区扩展冒险 75 册、站点科普漫画 1 册）与资料包（站点自有精选包与社区扩展包）。进入具体书目或资料包后，子项与查询出口默认继承该范围，页面顶部显示当前范围横幅与显式「放宽到全库」按钮；超过 24 项的书架与资料包分组内置即时筛选框。
- **整书阅读与媒体策略**：`/read?collection=our5etools` 从 `/our5etools-book/` 加载章节 HTML 与 DOM 链接层，支持目录导航、章节分页、库内全文搜索、定义与引用定位以及刷新后位置恢复；`/our5etools-media/` 完全从 `data/our5etools/media/` 提供音频（支持 `Range` 分段播放）、字体与图像，不依赖源镜像目录。由于上游 `our5etools` 仓库本身未打包 `img/` 图包（原站点在浏览器端通过 JS 将 `img/...` 动态指向 `5e.kiwee.top/img/` 等外链），默认运行保持严格离线（`CSP img-src 'self' data:`）与安静的单行「远端配图（未离线收录）」提示；加入 `LAZY_MEDIA=1`、`--lazy-media` 或环境变量 `ANNA_LAZY_MEDIA=1` 后，界面按需把配图提升为延迟加载图像，由服务端按白名单镜像（主站 `5e.kiwee.top/img/`、备站 `raw.githubusercontent.com/5etools-mirror-3/5etools-img/main/` 或 `media-remotes.json`）做校验后原子缓存到 `data/our5etools/media/{img,ext}/`，既保持同源 CSP 策略，后续即使恢复默认离线模式也能直接复用已缓存配图。
- **预留独立 `5etools` 接口**：`CollectionRegistry`、`load_fivetools_collection()`（读取 `--5etools-data`，默认 `data/5etools/`）、`/tour?collection=5etools`、`/read?collection=5etools`、`/5etools-book/` 与 `/5etools-media/` 仍保留给独立的纯中文版 `5etools` 资料库；未单独构建时界面仅显示 `5etools（未接入）` 占位，与 `our5etools` 严格分离。

### 署名与分发边界

`data/our5etools/attribution.json` 记录全部 323 个资料包、541 个来源及不可随项目二次分发的资源：

- **官方与合作出版物（16,190 条）**：Wizards of the Coast 及合作出版内容受版权保护，仅在本机个人查阅使用。
- **社区与站点自制包（322 个）**：保留原作者、译者、来源链接与 `_meta` 许可注记。
- **未随包远端媒体（10,914 处引用／22,271 条映射键）**：源镜像仓库未离线打包 `img/` 与外部图床媒体；默认显示为静默占位提示，仅在显式启用 `--lazy-media` 时按需本地缓存，或在构建时通过 `--img-dir` 纳入本机离线图包。
- **随包本地字体（68 个，已打入 `data/our5etools/media/`）**：构建自上游仓库 `fonts/` 与 `__ours/fonts/`，二次分发前需核对 `__ours/fonts/OFL.txt` 等字体许可。

## 自动化验证

`make test` 同时运行单元测试和 Playwright 浏览器回归；`make check` 再加数据审计。首次需要 Node.js 20+ 与 npm，并执行 `make browser-setup` 安装 `package-lock.json` 锁定的开发依赖及 Chromium；运行 anna 本身仍只需 Python 依赖。单独运行某层检查：

```sh
make test-unit
make test-browser
make check
```

浏览器测试在单一本机端口启动 anna，原文排版对照仅通过测试专用的 `/__original/` 路径提供，结束后停止，不复用或关闭正在开发的站点。默认使用 18380；冲突时可用 `ANNA_E2E_PORT=18480 make test-browser` 更换测试端口。测试读取已构建的数据；快照不一致时应先 `make build`。也可以用 `ANNA_E2E_DB=/绝对路径/catalog.sqlite` 和 `ANNA_E2E_BOOK=/绝对路径/链接版目录` 指定独立构建的配套产物；测试仍校验整个发布快照，不跳过一致性检查。

测试涵盖定义／引用跳转、阅读器刷新恢复、键盘焦点、滚动、窗口变窄和表格节点搬移。排版测试在同一 Chromium、视口和原文快照下同时渲染原版与链接版，对比可见文字、元素几何位置和截图像素，不以人工接受的历史截图作为基准。法术页、装备表、怪物数据卡和引用表分别在 1280px／390px 宽度检查页首与中段。

报告保存到 `output/playwright/report/index.html`；截图、失败差异图和失败 trace 保存到 `output/playwright/results/`，均不入库。浏览器异常、定位绑定失败或任一像素差异都会令测试失败。可以用 `npm run test:browser:report` 查看报告。浏览器用例位于 `tests/browser/`。

## 查询与分析

网页查询（Python 标准库，仅本机、只读），在 anna 目录运行：

```sh
make up
```

查询页标题旁的「资料库」标明当前查询范围，默认是不全书（`5echm`）。筛选面板的「来源」仍用来选当前库中的书籍（在 `our5etools` 中另提供「资料包」筛选）。构建好 `data/our5etools/` 后，可在「资料库」切换至 `our5etools`（`collection=our5etools`），其查询、导览（`/tour?collection=our5etools`）与 Stella 阅读（`/read?collection=our5etools`）独立于不全书与预留的 `5etools`（`collection=5etools`）；未构建对应数据时，`our5etools` 与 `5etools` 均显示为「未接入」，设置对照用的 `5ETOOLS_DIR` 不会启用它们。切换资料库后从新库的全部条目开始，后退可返回上一个查询；在同一库中「清空」只清筛选，不切库。

打开 [查询页](http://127.0.0.1:8000/)。在条件栏输入名称即可查询；按 Enter 把搜索留作标签后可继续输入下一个。每个条件和搜索都是独立的标签，标签之间以“∧”连接，表示都要满足；点击标签文字切换为“不是／不含”，即排除这些取值或词，筛选区的“排除”按钮切换整个字段。下拉的第二项“正文包含”改在条目原文中查找。输入“法术”“塑能”“PHB”“坐骑”等文字时，下拉建议对应的类型、字段取值、来源或预设，选择即加入条件。“筛选”中的第一个字段是条目类型，与其他字段同样操作；所选类型决定表格的列与筛选字段。法术按环阶、学派、职业、施法时间及特性筛选；生物按 CR 范围、体型、类型及传奇动作筛选；物品按稀有度、类别及同调筛选；技能按所用属性筛选，并按力量到魅力的顺序排列，2014 与 2024 的同名属性和技能叠放为版本。环境危害按类型“环境危害”查询，包括名称旁标注【危害】的术语、2024 城主指南标有危害度的危害物，以及“危害物”“环境灾害”“环境影响”等类别下在正文中应用了具体规则（DC、骰子、优势劣势、状态等）的条目；类别导言与查表页保持未分类段落。字段未选即不限制，点击取值即加入；同组多选取并集、组间取交集，支持清除与反选。每个取值旁的数字是选中后的结果数。表头可切换升降序；环阶、CR、稀有度按实际顺序排序，缺失或冲突数值置后。来源可多选；查询、排序与版本显示方式保存在地址栏。结果随滚动每批加载 150 行，底部也可点击“继续加载”；刷新或前进后退恢复已加载的行数和位置。结果底栏的“导出 CSV”导出全部匹配行及所选类型的属性。`Ctrl-C` 停止；用 `ANNA_PORT` 配置查询与阅读共用的端口。

筛选区默认收起，已选条件（类型在最前）以标签显示在条件栏中，可逐个移除；“清空”去掉全部条件；点击“筛选”展开常用条件，“更多筛选条件”展开其余领域条件，来源条件单独展开。展开状态在本标签页刷新和前进后退时保留。切换类型或条件时，旧结果变淡，直到新结果到达。结果表固定表头与名称列，其他属性可横向滚动，中文与英文名称同时保留；法术的成分与仪式／专注以字形条显示，导出仍逐项保留。手机宽度（640px 及以下）结果以列表显示。在某些类型中搜索时，结果栏显示其他类型中的匹配数；结果为空时，空状态列出去掉后能得到结果的条件。未分类段落可以通过查询参数的 `sections=1` 纳入结果，界面不再提供单独的补查按钮。

阅读栏“正文”按原书排版显示条目，数据卡等表格保持表格；提取的字段以批注列出，悬停或点击批注会高亮其依据的原文文字，正文中已解析的引用可直接打开目标条目。原文镜像缺失或与数据库快照不一致时，显示提取文本并说明原因。

同名的法术、生物、物品等版本默认合为一行：名称下列出各版本标签，版本间不同的取值列在显示值下方；结果栏“显示”菜单中的“优先显示”选择 2024 或 2014 规则，“合并同名版本”可取消，二者保存在地址栏；菜单中还有“紧凑行”。阅读栏“版本对照”逐字段比较同组全部版本。导出始终一个版本一行。

阅读栏只保留正文、属性、版本对照和关系四个栏目；核对模式、证据、概念线索、有效引用和待核栏目不再出现在界面中。

点击结果行打开条目阅读，双击则以全宽阅读打开：1200px 及以上时阅读栏在结果右侧展开，标题与结果的每一行都留在原处，可以继续点击其他结果；拖动阅读栏左缘或聚焦后用左右方向键调整宽度，双击恢复默认。更窄时为模态阅读层。阅读栏右上角的全宽阅读按钮让条目铺满工作区，宽于阅读栏的原文表格上方也提供同一入口；全宽阅读持续到关闭阅读栏。阅读栏宽度保存在本浏览器，二者都不进入共享链接。右上角箭头浏览相邻结果，结果列表随之滚动到当前行，到达已加载的末尾时自动加载下一批；条目不在已加载结果中时禁用。筛选与类型变化关闭旧详情；关闭详情不改变列表位置。刷新、浏览器前进后退恢复查询状态、筛选展开状态、详情栏目和滚动位置。读取失败可在阅读层内重试。

键盘支持 `/` 聚焦条件栏、条件栏中上下方向键与 Enter 选择建议、空输入框中 Backspace 移除最后一个条件、结果名称上的上下方向键移动焦点、Enter 打开条目、Esc 关闭详情；宽屏 F6 切换结果与详情（全宽阅读时先退出全宽阅读）。详情栏目可用左右方向键及 Home／End 切换；窄屏 Tab 焦点保留在阅读层内。中文输入法合成期间不提交中间文本。视觉与实现约定见[查询界面设计](web/README.md)。

“坐骑”列出带用途证据的生物数据卡；“动物购置”另列价格、载重和用途，通过详情中的“使用数据卡／购置条目”双向关联。购置条目不冒充数据卡，不将价格复制到怪物属性，也不跨版本合并。表列坐骑用途标为上下文推得；宠物／役畜不自动当作坐骑。

当前构建的覆盖、分类变化和待核项见[提取审计](data/extracted/audit.md)。

全量重建（只读镜像）：

```sh
/Users/arctic/Loom/Streamer/DnD/5e-anna/.venv/bin/python -m anna.catalog.build
```

查询（复用 `anna.presentation.catalog` 只读消费层）：

```sh
python3 -m anna.catalog.query --type feat --source PHB24
python3 -m anna.catalog.query --preset 魔法武器 --ruleset 5r
python3 -m anna.catalog.query --type spell --field school=塑能 --csv > evocation.csv
```

`--preset` 支持 `NPC`、`伙伴`、`坐骑`、`魔法武器`、`战斗风格` 与 `传奇恩惠`；`--csv` 输出 UTF-8 CSV，`--show 版本ID` 查看正文、字段、关系与有效引用，`--stats` 查看覆盖与异常统计。来源多选取并集，其余条件取交集。

构建产物集中在 [catalog.sqlite](data/extracted/catalog.sqlite)、[summary.json](data/extracted/summary.json)，审计后另生成 [audit.md](data/extracted/audit.md) 与 [audit.json](data/extracted/audit.json)。

`entry_marked`＝条目标旧；`source_archived`＝整书归档；`unmarked`＝未见标记，不代表新版。实体身份暂按定位区分，同名与同文匹配仅生成候选，不自动合并。未分类段落和原始表格保留在数据库中；恢复的断链仍保留原链接。

迁移环境：创建 `.venv`，安装 [requirements.txt](/Users/arctic/Loom/Streamer/DnD/5e-anna/requirements.txt)。测试：

```sh
/Users/arctic/Loom/Streamer/DnD/5e-anna/.venv/bin/python -m unittest discover -s /Users/arctic/Loom/Streamer/DnD/5e-anna/tests
```

结构审计（只读数据库，校验表结构、外键与统计一致性）：

```sh
/Users/arctic/Loom/Streamer/DnD/5e-anna/.venv/bin/python -m anna.catalog.audit
```

可用 `--baseline /绝对路径/旧版catalog.sqlite --output /报告目录` 生成逐条增删及分类／字段差异。审计不等于全库人工核验；未支持表格和无法唯一关联的动物保留在审核队列。

审计同时汇总分类缺口与类型冲突，供补充识别规则时决定先读哪里。缺口涵盖没有任何已分类条目的正文页、位于已分类条目之外且自身正文出现豁免 DC／骰子伤害／骰值表的未分类段落，以及每个信号的原文位置；数据卡内的“动作”等段落属于该数据卡，不计入。缺口与冲突均为线索，不作为审计失败条件；明细保存在 [audit.json](data/extracted/audit.json) 与 `catalog.sqlite`，带 `--baseline` 时[提取审计](data/extracted/audit.md)对照修改前后的变化。类型冲突判定顺序见[数据结构与提取规则](MODEL.md)。

### 逐处核对原文引用

`make build` 后运行 `make audit-links`。命令从原文字段、表列及骰值伤害句式枚举出现处，而非只统计已经生成的链接；它核对原文偏移、Stella 点击范围、Anna 目标条目、适用来源代际及定义落点。报告写入 `output/reference-coverage/occurrences.csv` 和 `summary.json`。核对范围包括法术字段及取值、武器表、法术列表学派与图例、正文骰值伤害，不代表全文所有规则用法。

需要检验一次实现变更是否影响既有点击时，先保留旧目录数据库，再从**同一原文快照**重建并运行：

```sh
.venv/bin/python -m anna.catalog.reference_coverage --baseline /旧构建/catalog.sqlite --output /报告目录
```

缺少入口以 `missing` 及原因记录；落点不符标为 `wrong_target`。落点不符、同快照旧点击被移除或改指、以及快照不一致都会使命令以非零状态退出。换用新原文时不传 `--baseline`，对新快照重新盘点。
