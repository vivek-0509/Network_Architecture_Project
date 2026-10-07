# bserve: Track 1 (the server)

**Vivek Singh Solanki (roll number 24bcs10338)**

`bserve` is a static file server that speaks **bhttp/1**, a binary framing of HTTP
designed for this project: an 8-byte fixed frame header, three frame types, and
header names looked up in a ten-entry static table. One TCP connection carries any
number of requests, one after another. A frame type the server does not know is
skipped cleanly, so a version 2 can add frames without breaking version 1 peers.

```
$ ./bserve ./www 9000
```

## What is handed in

| # | Deliverable | File | Notes |
|---|---|---|---|
| 1 | The spec | [SPEC.md](SPEC.md) ([SPEC.html](SPEC.html) for printing) | Two pages. Sections 1-6 are the contract; §7 defends every field width and says what version 2 may change. |
| 2 | The program | [bserve](bserve) | One file, Python 3.8+, standard library only. No build step. |
| 3 | The annotated hexdump | [HEXDUMP.md](HEXDUMP.md) | Every byte of `GET /index.html` in both directions, captured from a live run, one protocol field per row, with the length arithmetic checked. |

Supporting material: a 36-test end-to-end suite ([tests/](tests/)), an independent
test client and the hexdump generator ([tools/](tools/)), a sample document root
([www/](www/)) and a [Makefile](Makefile).

## Quick start

Requirements: Python 3.8 or newer. Nothing to install.

```
./bserve ./www 9000          # serve ./www on port 9000
./bserve ./www 9000 -v       # same, and hexdump every frame received and sent
./bserve --help              # also: --bind ADDR, --idle SECONDS; PORT 0 picks a free port and prints it
```

From a second terminal, with the test client (or the partner's `bcurl`):

```
python3 tools/bclient.py localhost 9000 /index.html /hello.txt /missing
```

That fetches three paths over **one** connection, writes each body to stdout, logs one
status line per path to stderr, and exits 1 because `/missing` is a 404.

```
make test       # run the 36 end-to-end tests against a live server
make hexdump    # regenerate HEXDUMP.md from a fresh exchange
make spec       # render SPEC.md to SPEC.html (needs pandoc); print to PDF from a browser
```

## The protocol in one screen

Full details are in [SPEC.md](SPEC.md). The shape:

```
client: "bhttp/1\n"                       8-byte preface, once per connection
then, both ways, frames:

 0                   1                   2                   3
+-------+-------+-------+-------+-------+-------+-------+-------+
|       Length (24)             | Type  | Flags |  Request ID (24)      |
+-------+-------+-------+-------+-------+-------+-------+-------+
| Payload, Length bytes (0 .. 16 384) ...

Type   0x01 REQUEST   u8 method, u16 path-len, path, header block        client -> server
       0x02 RESPONSE  u16 status, header block                           server -> client
       0x03 DATA      raw body bytes                                     either way
       anything else  unassigned: read Length bytes, throw them away, carry on

Flags  0x01 END_STREAM  "last frame of this request / response"

Header block   u8 count, then per field:
               u8 name-index   1..10 = static table  |  0 = u8 name-len + name follows
               u16 value-len, value

Static table   1 host  2 user-agent  3 accept  4 if-none-match  5 content-type
               6 content-length  7 server  8 date  9 last-modified  10 etag
```

A `GET /index.html` with four request headers is 76 bytes on the wire (84 with the
one-time preface); the 200 response head with six headers is 145 bytes; the 105-byte
file follows in one DATA frame. [HEXDUMP.md](HEXDUMP.md) walks through all of it.

## Assignment checklist

| The assignment says | bserve does | Proven by |
|---|---|---|
| accept a TCP connection | listens on all interfaces (IPv4 and IPv6), one thread per connection | `test_concurrent_connections` |
| read one binary request frame | reads the 8-byte header, bounds-checks Length, reads the payload, decodes method, path and headers | `test_get_file` |
| map the path to a file under a root | strips `?query`, percent-decodes, resolves `.`/`..`, checks the real path is still under ROOT, serves `index.html` for directories | `test_directory_serves_index`, `test_query_string_and_percent_encoding` |
| reply: status, headers, the bytes | RESPONSE frame (status + six headers) then DATA frames of at most 16 KiB, END_STREAM on the last | `test_get_file`, `test_large_file_is_chunked`, `test_empty_file` |
| 404 if it is not there | 404 with a text/plain body | `test_404` |
| 400 if the frame is malformed | 400 whose body names the problem; connection stays open when frame boundaries are intact, closes when they are not | the eight `test_400_*` tests |
| and keep the connection open | request after request on one socket, Request ID echoed on every reply frame; 30 s idle timeout | `test_keep_alive_many_requests_one_connection` |
| a receiver meeting an unknown frame type MUST skip it cleanly | types other than 1-3 are consumed and logged, nothing else happens | `test_unknown_frame_type_is_skipped` |
| number the ten names, length-prefix the rest | static table of 10, literal names with a length byte; index > 10 is a 400 | `test_literal_header_names_are_accepted`, `test_400_bad_header_index_and_bad_name` |
| a fixed-size header you defend | 8 bytes = 24/8/8/24; the defence, and HTTP/2's 24/8/8/31, are in SPEC.md §7 | HEXDUMP.md shows the fields |

Beyond the brief: `HEAD`, `304 Not Modified` via `if-none-match`/`etag`, `403` for
path-traversal and unreadable files (including symlinks that point out of the root),
`405` with an `allow` header for other methods (their request bodies are drained so the
connection stays usable), and a `-v` mode that hexdumps every frame for the partner
debugging a client.

## What the server does in detail

**Path mapping.** `?query` and `#fragment` are dropped, the path is percent-decoded
(invalid UTF-8 is a 400), `.` segments vanish, `..` pops a segment, and a `..` that would
climb above the root is a 403. The result is resolved with `realpath` and must still lie
under the root, so a symlink pointing outside is also a 403. A directory maps to its
`index.html`; no such file is a 404.

**Responses.** Every 200 carries `content-type` (from the file extension, `; charset=utf-8`
added for `text/*`), `content-length`, `server`, `date`, `last-modified` and `etag`
(`"<mtime-hex>-<size-hex>"`). A body is sent in DATA frames of at most 16 384 bytes;
END_STREAM goes on the last frame, whichever it is: an empty file, a HEAD, or a 304 is a
RESPONSE with END_STREAM set and no DATA at all. Error responses carry a one-line
`text/plain` body such as `400 Bad Request: method 0x99 is not defined`.

**Two tiers of 400** (SPEC.md §6). If the frame header was fine but the payload was not
(undefined method, path without `/`, a length field running past the payload, trailing
bytes, bad header index or name, Request ID 0, a DATA frame with no open request, a
RESPONSE arriving at the server), the server answers 400 with that Request ID and keeps
going. If the byte stream itself cannot be trusted (wrong preface, Length over the limit,
connection closed mid-frame), it answers 400 and then closes, using a shutdown-then-drain
so the 400 arrives before the FIN rather than being destroyed by a RST.

**Connections.** One thread per connection; an idle connection is closed after 30 s
(`--idle SECONDS` changes that). A
client that closes between frames is logged as a clean close. The listening socket is
dual-stack when the OS supports it, so `localhost` works whether it resolves to
`127.0.0.1` or `::1`.

**Limits.** Frame payload ≤ 16 384 bytes; path ≤ 65 535 bytes; ≤ 255 header fields per
block; header name ≤ 255 bytes; header value ≤ 65 535 bytes.

## What the log looks like

One line per request, plus one per frame with `-v`. This is a real run (hexdump lines
removed): two requests from the test client, then a connection that sends an unknown
frame type, a malformed REQUEST, and a good one.

```
bserve: serving /Users/vivek/Desktop/Network_Architecture_Project/www on port 9000 (bhttp/1) [verbose]
127.0.0.1:52742 << REQUEST len=46 flags=END_STREAM id=1
127.0.0.1:52742 >> RESPONSE len=137 flags=- id=1
127.0.0.1:52742 >> DATA len=16 flags=END_STREAM id=1
127.0.0.1:52742 #1 GET /hello.txt -> 200 (16 bytes)
127.0.0.1:52742 << REQUEST len=44 flags=END_STREAM id=2
127.0.0.1:52742 >> RESPONSE len=81 flags=- id=2
127.0.0.1:52742 >> DATA len=14 flags=END_STREAM id=2
127.0.0.1:52742 #2 GET /missing -> 404
127.0.0.1:52742 closed by client
127.0.0.1:52743 << UNKNOWN(0x42) len=3 flags=- id=1
127.0.0.1:52743 skipped unknown frame type 0x42 (3 bytes)
127.0.0.1:52743 << REQUEST len=5 flags=END_STREAM id=1
127.0.0.1:52743 >> RESPONSE len=81 flags=- id=1
127.0.0.1:52743 >> DATA len=44 flags=END_STREAM id=1
127.0.0.1:52743 #1 malformed REQUEST -> 400 (method 0x99 is not defined)
127.0.0.1:52743 << REQUEST len=47 flags=END_STREAM id=2
127.0.0.1:52743 >> RESPONSE len=137 flags=- id=2
127.0.0.1:52743 >> DATA len=105 flags=END_STREAM id=2
127.0.0.1:52743 #2 GET /index.html -> 200 (105 bytes)
127.0.0.1:52743 closed by client
```

## Repository layout

```
bserve                 the server (deliverable 2)
SPEC.md                the protocol (deliverable 1); SPEC.html is the printable render
HEXDUMP.md             annotated bytes of one request and response (deliverable 3)
README.md              this file
Makefile               run / test / hexdump / spec
www/                   sample document root: index.html, about.html, hello.txt, css/, docs/
tools/bclient.py       minimal client written from SPEC.md, independent of bserve;
                       used by the tests and the hexdump tool (not the Track 2 deliverable)
tools/make_hexdump.py  captures a live exchange and annotates every field -> HEXDUMP.md
tools/spec.css.html    print stylesheet for `make spec`
tests/test_bserve.py   36 end-to-end tests; each names the assignment line or spec rule it checks
```

## For the Track 2 partner

Everything you need is in [SPEC.md](SPEC.md). In short, `bcurl` must:

1. Open one TCP connection and send the 8 bytes `bhttp/1\n` first.
2. Send a REQUEST frame: 8-byte header (Length, Type `0x01`, Flags `0x01`, Request ID 1),
   then `u8` method, `u16` path length, the path, `u8` header count, the header fields.
3. Read frames until one with the same Request ID carries END_STREAM. The first is a
   RESPONSE (status, headers); the rest are DATA (body). Write the body to stdout; exit
   non-zero on 4xx/5xx.
4. Skip any frame whose Type is not 1, 2 or 3 by reading and discarding its payload.
5. Send the next request on the same connection; never open a second one.

Run `./bserve ./www 9000 -v` while you develop: it hexdumps exactly what it received
and what it sent back, and a 400 tells you in its body what was wrong with your frame.
`python3 tools/bclient.py localhost 9000 /index.html` shows a known-good exchange, and
[HEXDUMP.md](HEXDUMP.md) is the same exchange with every byte labelled. The tests in
[tests/test_bserve.py](tests/test_bserve.py) double as examples of valid and invalid frames.

## Design notes

The full argument is SPEC.md §7; the short version:

* **8 bytes, 24/8/8/24.** Length, Type and Flags are HTTP/2's widths for HTTP/2's
  reasons. The Request ID is 24 bits, not 31, because version 1 has no multiplexing and
  16 million requests per connection is plenty; dropping those bits lands the header on
  exactly two 32-bit words.
* **A preface instead of a version byte.** "Which version?" is answered once per
  connection, not once per frame. A plain-text HTTP client hitting the port is rejected
  at byte 0 instead of being misparsed as a 4 MB frame.
* **Framing ends a body, not `content-length`.** END_STREAM on the last frame means an
  empty file, a HEAD and a 304 all need no DATA frame, and the receiver never has to
  trust a header to know when to stop reading.
* **Unknown types are skipped; unknown header indexes are not.** A frame can be skipped
  because its length is in the header. A header index above 10 cannot, because the
  receiver has no way to know what name it stands for, so it is a 400 and version 2 must
  extend the table under a new preface.
* **Two tiers of 400.** Most malformed input leaves frame boundaries intact, so the
  server can say what was wrong and keep serving. Only when the byte stream itself is
  untrustworthy does it close.
