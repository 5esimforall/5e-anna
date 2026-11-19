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
# our5etools is an independent collection (5etools), kept separate from 5ETOOLS_DIR (5etools-cn).
OUR5ETOOLS_DIR ?=
OUR5ETOOLS_IMG_DIR ?=
OUR5ETOOLS_DATA ?= data/our5etools
OUR5ETOOLS_PATH := $(shell python3 -c 'import os, sys; from anna.paths import OUR5ETOOLS; print(os.path.abspath(sys.argv[1]) if sys.argv[1] else OUR5ETOOLS)' "$(OUR5ETOOLS_DIR)")
OUR5ETOOLS_DATA_PATH := $(shell python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$(OUR5ETOOLS_DATA)")
LAZY_MEDIA ?= 0
LAZY_MEDIA_FLAG := $(if $(filter 1 true yes on,$(LAZY_MEDIA)),--lazy-media,)
ANNA_PORT ?= 8000
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

.DEFAULT_GOAL := usage
.PHONY: usage clone update status clean build test audit audit-links check up upper linked our5etools-build our5etools-audit test-unit test-browser browser-setup icons

# Commands and variables are documented in USAGE.md; keep the list in one place.
usage:
	@echo "Commands and variables are documented in $(CURDIR)/USAGE.md"

clone:
	@if [ ! -e "$(WEB_PATH)" ]; then \
		git clone --depth 1 --branch "$(BRANCH)" "$(REPO)" "$(WEB_PATH)"; \
	elif [ ! -d "$(WEB_PATH)/topics" ]; then \
		echo "Source has no topics directory; refusing to overwrite: $(WEB_PATH)" >&2; exit 1; \
	fi
	@if [ ! -e "$(5ETOOLS_PATH)" ]; then \
		git clone --depth 1 --filter=blob:none --sparse --branch "$(5ETOOLS_BRANCH)" "$(5ETOOLS_REPO)" "$(5ETOOLS_PATH)" && \
		git -C "$(5ETOOLS_PATH)" sparse-checkout set data; \
	elif [ ! -d "$(5ETOOLS_PATH)/data" ]; then \
		echo "5etools-cn has no data directory; refusing to overwrite: $(5ETOOLS_PATH)" >&2; exit 1; \
	fi

update: clone
	@git -C "$(WEB_PATH)" pull --ff-only
	@git -C "$(WEB_PATH)" log -1 --format='source updated to %h  %ad  %s' --date=iso
	@git -C "$(5ETOOLS_PATH)" pull --ff-only
	@git -C "$(5ETOOLS_PATH)" log -1 --format='5etools-cn updated to %h  %ad  %s' --date=iso

status:
	@if [ -d "$(WEB_PATH)/.git" ] || [ -f "$(WEB_PATH)/.git" ]; then \
		git -C "$(WEB_PATH)" log -1 --format='commit %h  %ad  %s' --date=iso && \
		printf 'content version: ' && (grep -ho 'v2026[0-9.]*' "$(WEB_PATH)/topics/写在前面.html" 2>/dev/null | head -1) && \
		du -sh "$(WEB_PATH)"; \
	elif [ -d "$(WEB_PATH)/topics" ]; then \
		echo "source directory (no Git version): $(WEB_PATH)"; \
	else \
		echo "source: not downloaded yet; run make clone"; \
	fi
	@if [ -d "$(5ETOOLS_PATH)/.git" ] || [ -f "$(5ETOOLS_PATH)/.git" ]; then \
		git -C "$(5ETOOLS_PATH)" log -1 --format='5etools-cn: %h  %ad  %s' --date=iso; \
	elif [ -d "$(5ETOOLS_PATH)/data" ]; then \
		echo "5etools-cn (no Git version): $(5ETOOLS_PATH)"; \
	else \
		echo "5etools-cn: not downloaded yet; run make clone"; \
	fi
	@if [ -f data/extracted/catalog.sqlite ]; then \
		printf 'catalog: %s\n' "$$(du -h data/extracted/catalog.sqlite | cut -f1 | tr -d ' ')"; \
	else \
		echo "catalog: not built yet (run make build)"; \
	fi
	@if [ -d "$(OUR5ETOOLS_PATH)/.git" ] || [ -f "$(OUR5ETOOLS_PATH)/.git" ]; then \
		git -C "$(OUR5ETOOLS_PATH)" log -1 --format='our5etools: %h  %ad  %s' --date=iso; \
	elif [ -d "$(OUR5ETOOLS_PATH)/data" ]; then \
		echo "our5etools (no Git version): $(OUR5ETOOLS_PATH)"; \
	else \
		echo "our5etools: not present (set OUR5ETOOLS_DIR=...)"; \
	fi
	@if [ -f "$(OUR5ETOOLS_DATA_PATH)/manifest.json" ]; then \
		printf 'our5etools data: %s\n' "$$(du -sh "$(OUR5ETOOLS_DATA_PATH)" | cut -f1 | tr -d ' ')"; \
	else \
		echo "our5etools data: not built yet (run make our5etools-build)"; \
	fi

clean:
	@python3 scripts/clean.py

icons:
	@"$(PYTHON)" scripts/build_icons.py

build: clone icons
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.catalog.build --5etools "$(5ETOOLS_PATH)"
	@$(MAKE) --no-print-directory linked WEB_DIR="$(WEB_PATH)"
	@if [ -d "$(OUR5ETOOLS_PATH)/data" ] && [ -f "$(OUR5ETOOLS_PATH)/__api/books.json" ]; then \
		$(MAKE) --no-print-directory our5etools-build OUR5ETOOLS_DIR="$(OUR5ETOOLS_PATH)" OUR5ETOOLS_DATA="$(OUR5ETOOLS_DATA_PATH)"; \
	else \
		echo "our5etools: skipped (set OUR5ETOOLS_DIR to build 5etools collection)"; \
	fi

our5etools-build:
	@OUR5ETOOLS_DIR="$(OUR5ETOOLS_PATH)" "$(PYTHON)" -m anna.our5etools.build --source "$(OUR5ETOOLS_PATH)" --output "$(OUR5ETOOLS_DATA_PATH)" $(if $(OUR5ETOOLS_IMG_DIR),--img-dir "$(OUR5ETOOLS_IMG_DIR)",)

our5etools-audit:
	@"$(PYTHON)" -m anna.our5etools.audit --data "$(OUR5ETOOLS_DATA_PATH)" --output "$(OUR5ETOOLS_DATA_PATH)/audit.md"

test: test-unit test-browser

test-unit:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m unittest discover -s tests

audit:
	@"$(PYTHON)" -m anna.catalog.audit
	@if [ -f "$(OUR5ETOOLS_DATA_PATH)/manifest.json" ]; then \
		$(MAKE) --no-print-directory our5etools-audit OUR5ETOOLS_DATA="$(OUR5ETOOLS_DATA_PATH)"; \
	fi

audit-links:
	@"$(PYTHON)" -m anna.catalog.reference_coverage

check: test audit

linked:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.linked_book

up:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.app --port "$(ANNA_PORT)" --our5etools-data "$(OUR5ETOOLS_DATA_PATH)" $(LAZY_MEDIA_FLAG)

upper:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.app --port "$(ANNA_PORT)" --watch --our5etools-data "$(OUR5ETOOLS_DATA_PATH)" $(LAZY_MEDIA_FLAG)

# Browser tests use one dedicated localhost port and stop automatically.
browser-setup:
	@npm ci --ignore-scripts --cache .cache/npm
	@PLAYWRIGHT_BROWSERS_PATH="$(CURDIR)/.cache/ms-playwright" npx --no-install playwright install chromium

test-browser:
	@WEB_DIR="$(WEB_PATH)" PYTHON="$(PYTHON)" npm run test:browser
