# 5e anna

anna 把 5e 不全书整理为可查询的细粒度条目，并生成与原版排版一致、可沿引用跳转的链接版不全书。产品愿景与工作流程见 [DESIGN.md](DESIGN.md)。

## 运行

GitHub Pages 提供 [Anna 查询](https://5esimforall.github.io/5e-anna/)、[导览](https://5esimforall.github.io/5e-anna/tour/)和 [Stella 阅读器](https://5esimforall.github.io/5e-anna/read/)，使用默认的不全书资料库。首次打开会载入浏览器查询环境，并按需下载目录和数据分片。发布流程见[使用说明](USAGE.md#github-pages-发布)。

```sh
make build             # 从原文快照生成数据库与链接版（存在 our5etools 镜像时同步构建）
make our5etools-build  # 单独构建 our5etools 独立资料库与阅读产物
make up                # 在 8000 启动查询、导览与阅读服务，Ctrl-C 停止
```

打开条目查询 [anna](http://127.0.0.1:8000/) 或阅读器 [stella](http://127.0.0.1:8000/read)；不知从何查起时，Anna 的[导览](http://127.0.0.1:8000/tour)按目的列出入口，沿原书的章节、步骤与条目逐层深入，例如从“创建角色”的五个步骤到职业、子职与 1 级特性。阅读器提供目录、本页条目、全文搜索，以及目录子树内的连续阅读、自动接续、远页回收和分页跳转，正文保留原版排版；定义标题打开 anna 条目，条目可返回原文，已解析的引用在阅读器内跳到定义。两页顶栏可互相转向，接住正在看的条目，或回到各自记得的位置。每个页面顶栏的「资料库」选择要读的资料、它们的优先级、是否把同名条目合为一行，以及显示哪些规则版本与作品，设置保存在本浏览器、各页共用。构建好 `data/our5etools/` 后可在其中勾选独立的 `our5etools`：Anna 把它与不全书合成一张表，导览与 Stella 把两库排成一个书架，两库都有的作品只列一次、可在阅读栏切换文本，各库仍保存自己的条目与证据。另为独立中文版 `5etools` 预留接口（`data/5etools/`），未单独构建时保持未接入状态。应用与正文资源共用 8000 端口，不全书正文位于 `/book/`，our5etools 章节与本地媒体位于 `/our5etools-book/` 和 `/our5etools-media/`（`5etools` 预留为 `/5etools-book/` 与 `/5etools-media/`）。

新环境的依赖安装、端口与路径配置、our5etools 构建审计、引用维护和测试命令见[使用说明](USAGE.md)；链接生成范围与待核项见[阅读服务与正文生成](USAGE.md#阅读服务与正文生成)。

## 原文镜像

- 原文镜像位于同级的 `5echm-web/`，是独立的 Git 仓库，也供其他冒险项目引用；anna 只读取，不修改其内容。
- 镜像缺失时自动下载。已有镜像不会在启动或构建时更新：先运行 `make update` 拉取原文，再运行 `make build` 重建数据。
- 对照镜像位于同级的 `5etools-cn/`，取自 [5etools-cn](https://github.com/tjliqy/5etools-cn)，只检出 `data/`。构建时用它核对不全书条目的身份、字段与重印关系，结论只作旁证，见[来源对照](USAGE.md#来源对照5etools-cn)；它与原文一同由 `make clone` 下载、`make update` 更新，既不混入 `our5etools`，也不直接作为独立的 `5etools` 资料库对外服务。
- our5etools 本地镜像由 `OUR5ETOOLS_DIR` 指定（默认同级 `our5etools/` 或本机仓库镜像），仅在构建阶段读取；`make our5etools-build` 将查询库、Stella 章节与全部本地媒体统一打包进完全自包含的 `data/our5etools/`（含 `data/our5etools/media/`），运行时不再访问源镜像，并可用 `LAZY_MEDIA=1`（`--lazy-media`）按需开启远端配图懒加载与本地落盘缓存。各库构建与切换方式见[our5etools 资料库](USAGE.md#our5etools-资料库)。
- `Makefile` 是工作流程的唯一实现。上层 DnD 目录的 `Makefile` 只转调本项目，上层提供常用命令入口，完整命令在本项目中执行。

## 源码组织

Python 实现集中在 `anna/`，用包导入表达依赖；`scripts/` 只保存离线开发维护工具。这样规则机制、来源适配与服务代码可以被测试和其他模块复用，不必把脚本目录加入 `sys.path`。

| 位置 | 职责 |
| --- | --- |
| `anna/source/` | 原文解码、HTML 与表格结构、来源名录与库内全文搜索索引 |
| `anna/catalog/` | 条目分类与构建、概念与引用、只读命令行查询及审计 |
| `anna/review/` | 引用裁定事件日志、可迁移锚点与候选检索 |
| `anna/compare/` | 5etools 对照索引与字段核对 |
| `anna/our5etools/` | our5etools 镜像盘点、类型与语义解析、块与标签渲染、整书章节构建、只读库发布及审计 |
| `anna/presentation/` | 只读查询、读者资料库的合表（`library.py`）、书架（`shelf.py`）与统一导览（`library_tour.py`）、原文排版、阅读模型与显式响应投影（含 `our5etools.py`）；[消费契约](anna/presentation/README.md)独立于提取实现 |
| `anna/server/` | 统一 HTTP 传输、多资料库路由、快照发布校验与链接版生成 |
| `anna/paths.py`、`anna/serialization.py` | 项目路径、确定性序列化与导出格式 |
| `tour/` | 导览的精选入口（`tour.json`，各资料库共用）：第一列的主题、它们在各库中的章节，以及各步骤通向的选项集合 |
| `annotations/` | 内容与上下文双哈希版本化的引用裁定日志（`annotations.jsonl`）；历史 `grant`／`procedure` 事件保留但不进入当前构建 |
| `scripts/` | 图标导出、生成产物清理等离线维护脚本，不承载应用入口或领域模块 |
| `web/`、`tests/` | 前端资源与项目测试 |

日常入口仍是 Makefile。需要直接传递参数时，在仓库根目录用 `python -m anna.…` 运行对应命令，具体示例见 [USAGE.md](USAGE.md)。旧 `scripts/*.py` 应用入口已移除，不保留转发层；HTTP 服务只有 `make up`／`make upper` 对应的统一服务入口。命令行查询仍保留独立的只读数据分析用途。
