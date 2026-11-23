# anna owns the workflow; the sibling mirrors remain independent repositories.
# Machine-specific paths and defaults live in the untracked local.mk; command-line variables still override them.
-include local.mk
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
OUR5ETOOLS_DIR ?= ../our5etools
OUR5ETOOLS_IMG_DIR ?=
OUR5ETOOLS_DATA ?= data/our5etools
5ETOOLS_DATA ?= data/5etools
OUR5ETOOLS_PATH := $(shell python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$(OUR5ETOOLS_DIR)")
OUR5ETOOLS_DATA_PATH := $(shell python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$(OUR5ETOOLS_DATA)")
5ETOOLS_DATA_PATH := $(shell python3 -c 'import os, sys; print(os.path.abspath(sys.argv[1]))' "$(5ETOOLS_DATA)")
TRUE_VALUES := 1 true yes on
LAZY_MEDIA ?= 0
LAZY_MEDIA_FLAG := $(if $(filter $(TRUE_VALUES),$(LAZY_MEDIA)),--lazy-media,)
ANNA_PORT ?= 8000
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

NO_COUNTERPARTS ?= 0
PAGES_BASE ?= /5e-anna/
PAGES_OUTPUT ?= output/pages
COUNTERPARTS_ENABLED := $(if $(filter $(TRUE_VALUES),$(NO_COUNTERPARTS)),,yes)
COUNTERPART_ARGS := $(if $(COUNTERPARTS_ENABLED),--5etools "$(5ETOOLS_PATH)",--no-counterparts)
# The mirror is an external input, so whether it can be built is known when make starts.
OUR5ETOOLS_MIRROR := $(shell test -d "$(OUR5ETOOLS_PATH)/data" && test -f "$(OUR5ETOOLS_PATH)/__api/books.json" && printf yes)
# Built libraries appear while make runs (make build pages), so pages looks for them when its recipe starts.
PAGES_OUR5ETOOLS = $(if $(shell test -f "$(OUR5ETOOLS_DATA_PATH)/manifest.json" && printf yes),--our5etools-data "$(OUR5ETOOLS_DATA_PATH)",)

.DEFAULT_GOAL := usage
.PHONY: usage clone _clone-web _clone-counterparts update status clean distclean build 5echm _catalog linked our5etools _skip-our5etools home pages test audit audit-links check up upper our5etools-audit test-unit test-browser browser-setup icons
# Stages run in the order they are named; each one reads what the earlier ones published.
.NOTPARALLEL:

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
		echo "our5etools: not present (set OUR5ETOOLS_DIR in local.mk or on the command line)"; \
	fi
	@if [ -f "$(OUR5ETOOLS_DATA_PATH)/manifest.json" ]; then \
		printf 'our5etools data: %s\n' "$$(du -sh "$(OUR5ETOOLS_DATA_PATH)" | cut -f1 | tr -d ' ')"; \
	else \
		echo "our5etools data: not built yet (run make our5etools)"; \
	fi

clean:
	@python3 scripts/clean.py

distclean:
	@python3 scripts/clean.py --dependencies

icons:
	@"$(PYTHON)" scripts/build_icons.py

# Build stages. `make build` runs every library and the home cache; name stages to run only those.
build: 5echm $(if $(OUR5ETOOLS_MIRROR),our5etools,_skip-our5etools) home

5echm: _catalog linked

_catalog: _clone-web $(if $(COUNTERPARTS_ENABLED),_clone-counterparts) icons
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.catalog.build $(COUNTERPART_ARGS)

linked:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.linked_book

ifeq ($(OUR5ETOOLS_MIRROR),)
our5etools:
	$(error our5etools: no mirror with data/ and __api/books.json at $(OUR5ETOOLS_PATH); set OUR5ETOOLS_DIR in local.mk or on the command line)
else
our5etools:
	@OUR5ETOOLS_DIR="$(OUR5ETOOLS_PATH)" "$(PYTHON)" -m anna.our5etools.build --source "$(OUR5ETOOLS_PATH)" --output "$(OUR5ETOOLS_DATA_PATH)" $(if $(OUR5ETOOLS_IMG_DIR),--img-dir "$(OUR5ETOOLS_IMG_DIR)",)
endif

_skip-our5etools:
	@echo "our5etools: skipped (set OUR5ETOOLS_DIR to build our5etools collection)"

home:
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.home --our5etools-data "$(OUR5ETOOLS_DATA_PATH)" --5etools-data "$(5ETOOLS_DATA_PATH)" $(LAZY_MEDIA_FLAG)

# Pages packages the libraries `make up` would serve, with a home cache encoded from the packaged site.
pages:
	@test -f data/extracted/catalog.sqlite && test -f data/5echm-anna/anna-manifest.json || \
		{ echo "pages: no built 5echm library in data/; run make build first" >&2; exit 1; }
	@WEB_DIR="$(WEB_PATH)" "$(PYTHON)" -m anna.server.pages_build --base "$(PAGES_BASE)" --output "$(PAGES_OUTPUT)" $(PAGES_OUR5ETOOLS) --home-cache

our5etools-audit:
	@"$(PYTHON)" -m anna.our5etools.audit --data "$(OUR5ETOOLS_DATA_PATH)" --output "$(OUR5ETOOLS_DATA_PATH)/audit.md"

test: test-unit test-browser

test-unit:
	@WEB_DIR="$(WEB_PATH)" OUR5ETOOLS_DIR="$(OUR5ETOOLS_PATH)" "$(PYTHON)" -m unittest discover -s tests

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
