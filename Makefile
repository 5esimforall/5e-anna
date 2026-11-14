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

.DEFAULT_GOAL := usage
.PHONY: usage clone update status index clean build inventory test audit check up upper linked test-unit test-browser browser-setup

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
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" scripts/book_search.py --rebuild

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
	@if [ -f .cache/5echm-search.pkl ]; then \
		printf 'search index: %s (source and content fingerprint checked at start)\n' "$$(du -h .cache/5echm-search.pkl | cut -f1 | tr -d ' ')"; \
	else \
		echo "search index: not built yet (make up builds it)"; \
	fi

index: clone
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" scripts/book_search.py --rebuild

clean:
	@if [ -e "$(WEB_PATH)" ]; then \
		if [ "$(WEB_PATH)" = "$(CURDIR)" ] || [ "$(WEB_PATH)" = "$(abspath ..)" ] || [ ! -d "$(WEB_PATH)/.git" ] || [ ! -d "$(WEB_PATH)/topics" ]; then \
			echo "Refusing to delete: target must be a standalone source Git mirror" >&2; exit 1; \
		fi; \
		rm -rf -- "$(WEB_PATH)" || exit 1; \
	fi
	@rm -f .cache/5echm-search.pkl
	@echo "Deleted $(WEB_PATH) and the search cache; run make clone to download again"

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

upper:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" scripts/up.py --port "$(ANNA_PORT)" --watch

# Browser tests use one dedicated localhost port and stop automatically.
browser-setup:
	@npm ci --ignore-scripts --cache .cache/npm
	@PLAYWRIGHT_BROWSERS_PATH="$(CURDIR)/.cache/ms-playwright" npx --no-install playwright install chromium

test-browser:
	@WEB_DIR="$(WEB_PATH)" PYTHON="$(PYTHON)" npm run test:browser
