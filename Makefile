.DEFAULT_GOAL := help
.PHONY: help run diagnose test
PYTHON ?= /home/usr/venvs/midi/bin/python
NODE ?= /mnt/c/Program Files/nodejs/node.exe
export PYTHONDONTWRITEBYTECODE := 1
export PYTHONPATH := $(CURDIR)/emotion/vendor:$(CURDIR)/emotion_r2/vendor:$(PYTHONPATH)
export NUMBA_CACHE_DIR := $(CURDIR)/emotion_r2/scratch/task_g_numba
export XDG_CACHE_HOME := $(CURDIR)/emotion_r2/scratch/task_g_xdg

help:
	@echo "run ARGS='--song aespa-attitude'; diagnose ARGS='--song aespa-attitude --start 0 --end 20'; test"

run:
	$(PYTHON) -m pipeline run $(ARGS)

diagnose:
	$(PYTHON) -m pipeline diagnose $(ARGS)

test:
	@mkdir -p "$(NUMBA_CACHE_DIR)" "$(XDG_CACHE_HOME)"
	$(PYTHON) -B -m unittest discover -s tests -v
	$(PYTHON) -B -m unittest discover -s emotion -p 'test_*.py' -v
	$(PYTHON) -B -m unittest discover -s emotion_r2 -p 'test_*.py' -v
	$(PYTHON) -B -m unittest discover -s frontend -p 'test_*.py' -v
	"$(NODE)" --test frontend/test.mjs frontend/test_summaries.mjs frontend/test_emotion.mjs frontend/test_music.mjs
