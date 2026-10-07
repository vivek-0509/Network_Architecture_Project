# bhttp/1 server (Track 1) -- see README.md
PY   ?= python3
ROOT ?= ./www
PORT ?= 9000

.PHONY: run test hexdump spec clean

run:                      ## start the server on $(PORT) serving $(ROOT)
	./bserve $(ROOT) $(PORT) -v

test:                     ## run the end-to-end test-suite
	$(PY) -m unittest discover -s tests -v

hexdump:                  ## regenerate HEXDUMP.md from a live exchange
	$(PY) tools/make_hexdump.py > HEXDUMP.md
	@echo "wrote HEXDUMP.md"

spec:                     ## render SPEC.md to SPEC.html (print to PDF from a browser)
	pandoc SPEC.md -s --metadata title="bhttp/1 specification" \
	    -H tools/spec.css.html -o SPEC.html
	@echo "wrote SPEC.html"

clean:
	rm -rf __pycache__ tests/__pycache__ tools/__pycache__ SPEC.html
