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
# our5etools (collection=our5etools) and reserved standalone 5etools (collection=5etools) are kept separate from comparison-only 5ETOOLS_DIR (5etools-cn).
OUR5ETOOLS_DIR ?=
OUR5ETOOLS_IMG_DIR ?=
OUR5ETOOLS_DATA ?= data/our5etools
5ETOOLS_DATA ?= data/5etools
OUR5ETOOLS_PATH := $(shell python3 -c 'import os, sys; from anna.paths import OUR5ETOOLS; print(os.path.abspath(sys.argv[1]) if sys.argv[1] else OUR5ETOOLS)' "$(OUR5ETOOLS_DIR)")
OUR5ETOOLS_DATA_PATH := $(shell python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$(OUR5ETOOLS_DATA)")
5ETOOLS_DATA_PATH := $(shell python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$(5ETOOLS_DATA)")
TRUE_VALUES := 1 true yes on
LAZY_MEDIA ?= 0
LAZY_MEDIA_FLAG := $(if $(filter $(TRUE_VALUES),$(LAZY_MEDIA)),--lazy-media,)
ANNA_PORT ?= 8000
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

COLLECTION ?= all
NO_COUNTERPARTS ?= 0
PAGES ?= 0
PAGES_BASE ?= /5e-anna/
PAGES_OUTPUT ?= output/pages
HOME_CACHE ?= 0
REUSE ?= 0
PAGES_ENABLED := $(filter $(TRUE_VALUES),$(PAGES))
REUSE_ENABLED := $(filter $(TRUE_VALUES),$(REUSE))
HOME_CACHE_FLAG := $(if $(filter $(TRUE_VALUES),$(HOME_CACHE)),--home-cache,)
COUNTERPARTS_ENABLED := $(if $(filter $(TRUE_VALUES),$(NO_COUNTERPARTS)),,yes)
COUNTERPART_ARGS := $(if $(COUNTERPARTS_ENABLED),--5etools "$(5ETOOLS_PATH)",--no-counterparts)
BUILD_CATALOG := $(filter all 5echm,$(COLLECTION))

ifeq ($(and $(filter 1,$(words $(COLLECTION))),$(filter all 5echm our5etools linked,$(COLLECTION))),)
BUILD_ERROR := COLLECTION must be all, 5echm, our5etools, or linked
else ifeq ($(PAGES_ENABLED),)
ifneq ($(REUSE_ENABLED),)
BUILD_ERROR := REUSE requires PAGES=1
else ifneq ($(HOME_CACHE_FLAG),)
BUILD_ERROR := HOME_CACHE requires PAGES=1
endif
endif

ifeq ($(COLLECTION),our5etools)
BUILD_COMPANION := yes
else ifeq ($(COLLECTION),all)
ifeq ($(REUSE_ENABLED),)
BUILD_COMPANION := $(shell test -d "$(OUR5ETOOLS_PATH)/data" && test -f "$(OUR5ETOOLS_PATH)/__api/books.json" && printf yes)
else
BUILD_COMPANION := $(shell test -f "$(OUR5ETOOLS_DATA_PATH)/manifest.json" && printf yes)
endif
endif

.DEFAULT_GOAL := usage
.PHONY: usage clone _clone-web _clone-counterparts update status clean build test audit audit-links check up upper our5etools-audit test-unit test-browser browser-setup icons

# Commands and variables are documented in USAGE.md; keep the list in one place.
usage:
	@echo "Commands and variables are documented in $(CURDIR)/USAGE.md"

clone: _clone-web _clone-counterparts

_clone-web:
	@if [ ! -e "$(WEB_PATH)" ]; then \
		git clone --depth 1 --branch "$(BRANCH)" "$(REPO)" "$(WEB_PATH)"; \
	elif [ ! -d "$(WEB_PATH)/topics" ]; then \
		echo "Source has no topics directory; refusing to overwrite: $(WEB_PATH)" >&2; exit 1; \
	fi

_clone-counterparts:
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
		echo "our5etools data: not built yet (run make build COLLECTION=our5etools)"; \
	fi

clean:
	@python3 scripts/clean.py

icons:
	@"$(PYTHON)" scripts/build_icons.py

ifneq ($(BUILD_ERROR),)
build:
	$(error $(BUILD_ERROR))
else
ifeq ($(REUSE_ENABLED),)
ifneq ($(BUILD_CATALOG),)
build: _clone-web $(if $(COUNTERPARTS_ENABLED),_clone-counterparts)
endif
ifneq ($(BUILD_CATALOG)$(PAGES_ENABLED),)
build: icons
endif
endif

build:
ifeq ($(REUSE_ENABLED),)
ifneq ($(BUILD_CATALOG),)
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.catalog.build $(COUNTERPART_ARGS)
endif
ifneq ($(filter all 5echm linked,$(COLLECTION)),)
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.linked_book
endif
ifneq ($(BUILD_COMPANION),)
	@OUR5ETOOLS_DIR="$(OUR5ETOOLS_PATH)" "$(PYTHON)" -m anna.our5etools.build --source "$(OUR5ETOOLS_PATH)" --output "$(OUR5ETOOLS_DATA_PATH)" $(if $(OUR5ETOOLS_IMG_DIR),--img-dir "$(OUR5ETOOLS_IMG_DIR)",)
else ifeq ($(COLLECTION),all)
	@echo "our5etools: skipped (set OUR5ETOOLS_DIR to build our5etools collection)"
endif
endif
ifneq ($(PAGES_ENABLED),)
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.pages_build --base "$(PAGES_BASE)" --output "$(PAGES_OUTPUT)" $(if $(BUILD_COMPANION),--our5etools-data "$(OUR5ETOOLS_DATA_PATH)",) $(HOME_CACHE_FLAG)
endif
endif

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

up:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.app --port "$(ANNA_PORT)" --our5etools-data "$(OUR5ETOOLS_DATA_PATH)" --5etools-data "$(5ETOOLS_DATA_PATH)" $(LAZY_MEDIA_FLAG)

upper:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.app --port "$(ANNA_PORT)" --watch --our5etools-data "$(OUR5ETOOLS_DATA_PATH)" --5etools-data "$(5ETOOLS_DATA_PATH)" $(LAZY_MEDIA_FLAG)

# Browser tests use one dedicated localhost port and stop automatically.
browser-setup:
	@npm ci --ignore-scripts --cache .cache/npm
	@PLAYWRIGHT_BROWSERS_PATH="$(CURDIR)/.cache/ms-playwright" npx --no-install playwright install chromium

test-browser:
	@WEB_DIR="$(WEB_PATH)" PYTHON="$(PYTHON)" npm run test:browser
