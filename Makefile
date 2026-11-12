# anna owns the workflow; the sibling mirror remains an independent repository.
WEB_DIR ?= ../5echm-web
WEB_PATH := $(shell python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$(WEB_DIR)")
REPO ?= https://github.com/DND5eChm/5echm_web.git
BRANCH ?= pages
# 5etools-cn is compared with anna's entries; only its data/ directory is checked out.
5ETOOLS_DIR ?= ../5etools-cn
5ETOOLS_PATH := $(shell python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$(5ETOOLS_DIR)")
5ETOOLS_REPO ?= https://github.com/tjliqy/5etools-cn.git
5ETOOLS_BRANCH ?= main
ANNA_PORT ?= 8000
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.DEFAULT_GOAL := help
.PHONY: help clone update status index clean anna build inventory test audit check up linked test-unit test-browser browser-setup

help:
	@echo "anna 与共享不全书镜像（DnD 上层可使用相同命令）"
	@echo "  make clone     原文或 5etools-cn 对照镜像缺失时下载；已有镜像不更新"
	@echo "  make index     重建原文全文搜索索引"
	@echo "  make anna      分析查询页 http://127.0.0.1:$(ANNA_PORT)"
	@echo "  make build     从同一原文生成数据库与 5echm-anna"
	@echo "  make up        启动 anna 查询与阅读（单一端口）"
	@echo "  make linked    由现有数据库生成链接版（校验原文快照）"
	@echo "  make inventory 盘点原文页面与目录"
	@echo "  make test      运行单元测试和浏览器回归（需先构建数据）"
	@echo "  make test-unit / test-browser  分别运行单元 / 浏览器测试"
	@echo "  make browser-setup  安装锁定的浏览器测试依赖（首次运行）"
	@echo "  make audit     审计数据库和导出文件"
	@echo "  make check     运行测试与审计"
	@echo "  make update    显式拉取原文与 5etools-cn 更新并刷新搜索索引；分析数据需 make build"
	@echo "  make status    查看原文、5etools-cn 版本和搜索索引状态"
	@echo "  make clean     删除共享原文镜像及搜索缓存（影响其他引用方）"
	@echo "  变量: WEB_DIR=原文路径 5ETOOLS_DIR=5etools-cn路径 ANNA_PORT=8000 PYTHON=解释器"
	@echo "  WEB_DIR、5ETOOLS_DIR 相对执行 make 的目录解析；默认为 anna 的同级 5echm-web、5etools-cn"

clone:
	@if [ ! -e "$(WEB_PATH)" ]; then \
		git clone --depth 1 --branch "$(BRANCH)" "$(REPO)" "$(WEB_PATH)"; \
	elif [ ! -d "$(WEB_PATH)/topics" ]; then \
		echo "原文目录无 topics，拒绝覆盖：$(WEB_PATH)" >&2; exit 1; \
	fi
	@if [ ! -e "$(5ETOOLS_PATH)" ]; then \
		git clone --depth 1 --filter=blob:none --sparse --branch "$(5ETOOLS_BRANCH)" "$(5ETOOLS_REPO)" "$(5ETOOLS_PATH)" && \
		git -C "$(5ETOOLS_PATH)" sparse-checkout set data; \
	elif [ ! -d "$(5ETOOLS_PATH)/data" ]; then \
		echo "5etools-cn 目录无 data，拒绝覆盖：$(5ETOOLS_PATH)" >&2; exit 1; \
	fi

update: clone
	@git -C "$(WEB_PATH)" pull --ff-only
	@git -C "$(WEB_PATH)" log -1 --format='已更新到 %h  %ad  %s' --date=iso
	@git -C "$(5ETOOLS_PATH)" pull --ff-only
	@git -C "$(5ETOOLS_PATH)" log -1 --format='5etools-cn 已更新到 %h  %ad  %s' --date=iso
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" scripts/book_search.py --rebuild

status:
	@if [ -d "$(WEB_PATH)/.git" ] || [ -f "$(WEB_PATH)/.git" ]; then \
		git -C "$(WEB_PATH)" log -1 --format='commit %h  %ad  %s' --date=iso && \
		printf '内容版本: ' && (grep -ho 'v2026[0-9.]*' "$(WEB_PATH)/topics/写在前面.html" 2>/dev/null | head -1) && \
		du -sh "$(WEB_PATH)"; \
	elif [ -d "$(WEB_PATH)/topics" ]; then \
		echo "原文目录（无 Git 版本）：$(WEB_PATH)"; \
	else \
		echo "尚未下载，运行 make clone"; \
	fi
	@if [ -d "$(5ETOOLS_PATH)/.git" ] || [ -f "$(5ETOOLS_PATH)/.git" ]; then \
		git -C "$(5ETOOLS_PATH)" log -1 --format='5etools-cn: %h  %ad  %s' --date=iso; \
	elif [ -d "$(5ETOOLS_PATH)/data" ]; then \
		echo "5etools-cn（无 Git 版本）：$(5ETOOLS_PATH)"; \
	else \
		echo "5etools-cn: 尚未下载，运行 make clone"; \
	fi
	@if [ -f .cache/5echm-search.pkl ]; then \
		printf '搜索索引: %s（启动时校验来源与内容指纹）\n' "$$(du -h .cache/5echm-search.pkl | cut -f1 | tr -d ' ')"; \
	else \
		echo "搜索索引: 尚未构建（make up 时自动构建）"; \
	fi

index: clone
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" scripts/book_search.py --rebuild

clean:
	@if [ -e "$(WEB_PATH)" ]; then \
		if [ "$(WEB_PATH)" = "$(CURDIR)" ] || [ "$(WEB_PATH)" = "$(abspath ..)" ] || [ ! -d "$(WEB_PATH)/.git" ] || [ ! -d "$(WEB_PATH)/topics" ]; then \
			echo "拒绝删除：目标必须是独立的原文 Git 镜像目录" >&2; exit 1; \
		fi; \
		rm -rf -- "$(WEB_PATH)" || exit 1; \
	fi
	@rm -f .cache/5echm-search.pkl
	@echo "已删除 $(WEB_PATH) 及搜索缓存；make clone 可重新下载"

anna:
	@"$(PYTHON)" scripts/serve.py --port "$(ANNA_PORT)"

build: clone
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" scripts/build.py --5etools "$(5ETOOLS_PATH)"
	@$(MAKE) --no-print-directory linked WEB_DIR="$(WEB_PATH)"

inventory: clone
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" scripts/inventory.py

test: test-unit test-browser

test-unit:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m unittest discover -s tests

audit:
	@"$(PYTHON)" scripts/audit.py

check: test audit

linked:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" scripts/linked_book.py

up:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" scripts/up.py --port "$(ANNA_PORT)"

# Browser tests use one dedicated localhost port and stop automatically.
browser-setup:
	@npm ci --ignore-scripts --cache .cache/npm
	@PLAYWRIGHT_BROWSERS_PATH="$(CURDIR)/.cache/ms-playwright" npx --no-install playwright install chromium

test-browser:
	@WEB_DIR="$(WEB_PATH)" PYTHON="$(PYTHON)" npm run test:browser
