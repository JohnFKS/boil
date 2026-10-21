.PHONY: run test check check-js browser packs docker

run:
	python3 run.py

test:
	python3 -m unittest discover -s tests -v

check:
	python3 -m compileall -q boil tests tools run.py

check-js:
	@find static/js -name '*.js' -exec sh -c 'for f do node --input-type=module --check < "$$f" || exit 1; done' sh {} +

browser:
	python3 tools/browser_check.py

packs:
	python3 tools/import_examgen.py \
		--input ../_work/examgen/output/模板库/第05章-二重积分-模板库-v0.14.md \
		--output content/packs/math-calculus-ch5.json --limit 6

docker:
	docker compose up --build
