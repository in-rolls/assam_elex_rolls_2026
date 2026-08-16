.PHONY: install lint fmt test ci ci-docker render extract validate review elector-release clean

PY := .venv/bin/python
PIP := uv pip install --python .venv/bin/python

.venv:
	uv venv --python 3.12 .venv

install: .venv
	$(PIP) -e ".[dev]"

fmt:
	$(PY) -m black assam_rolls romanize electors tests
	$(PY) -m isort assam_rolls romanize electors tests

lint:
	$(PY) -m black --check assam_rolls romanize electors tests
	$(PY) -m isort --check-only assam_rolls romanize electors tests
	$(PY) -m flake8 assam_rolls romanize electors tests

test:
	$(PY) -m pytest -q

ci: lint test

# Standard image, no custom Dockerfile.
ci-docker:
	docker run --rm -v "$(CURDIR)":/w -w /w python:3.12 bash -c "\
		apt-get update -qq && apt-get install -y -qq poppler-utils >/dev/null && \
		pip install -q -e '.[dev]' && \
		black --check assam_rolls romanize electors tests && \
		isort --check-only assam_rolls romanize electors tests && \
		flake8 assam_rolls romanize electors tests && \
		pytest -q"

render:
	$(PY) -m assam_rolls.cli render --zip-dir data/ac_info --out out/pages

extract:
	$(PY) -m assam_rolls.cli extract --pages out/pages --out out/raw

validate:
	$(PY) -m assam_rolls.cli validate --raw out/raw --out out

review:
	$(PY) -m assam_rolls.cli review --parts out/parts.csv --pages out/pages --out out/review.html

elector-release:
	$(PY) -m electors release

clean:
	rm -rf out .pytest_cache
	find . -name __pycache__ -type d -exec rm -rf {} +
