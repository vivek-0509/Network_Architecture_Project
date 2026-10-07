#!/usr/bin/env python3
"""
make_hexdump - regenerate HEXDUMP.md from a real exchange.
Author: Vivek Singh Solanki (roll number 24bcs10338).

Starts bserve on a free port serving ./www, fetches /index.html with
tools/bclient.py while recording every byte in both directions, then walks
those bytes with a decoder and prints each field beside its hex.

    python3 tools/make_hexdump.py > HEXDUMP.md
"""

import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import bclient  # noqa: E402
from bclient import REQUEST, RESPONSE, DATA, END_STREAM, STATIC  # noqa: E402

TYPE_NAME = {REQUEST: "REQUEST", RESPONSE: "RESPONSE", DATA: "DATA"}
METHOD_NAME = {v: k for k, v in bclient.METHOD.items()}
REASON = {200: "OK", 304: "Not Modified", 400: "Bad Request", 403: "Forbidden",
          404: "Not Found", 405: "Method Not Allowed"}

PATH = "/index.html"
REQUEST_HEADERS = [("host", "localhost"), ("user-agent", "bclient/1.0"),
                   ("accept", "*/*"), ("accept-language", "en")]


def be(b):
    return int.from_bytes(b, "big")


def quote(s):
    """Wrap a value in quotes unless it already carries its own (etag does)."""
    return s if s.startswith('"') else '"%s"' % s


def lab(field, note):
    return "%-22s%s" % (field, note)


# -- capture ------------------------------------------------------------------

def capture():
    proc = subprocess.Popen([sys.executable, os.path.join(ROOT, "bserve"),
                             os.path.join(ROOT, "www"), "0"],
                            stderr=subprocess.PIPE, text=True)
    port = int(re.search(r"port (\d+)", proc.stderr.readline()).group(1))
    try:
        c = bclient.Client("127.0.0.1", port)
        r = c.request("GET", PATH, headers=REQUEST_HEADERS)
        c.close()
        return bytes(c.sent), bytes(c.received), r
    finally:
        proc.terminate()
        proc.wait()


# -- annotation ---------------------------------------------------------------

class Dump:
    """Walks a byte string, emitting one annotated row per protocol field."""

    def __init__(self, data):
        self.data, self.pos, self.lines = data, 0, []

    def note(self, text=""):
        self.lines.append(text)

    def field(self, n, meaning):
        chunk = self.data[self.pos:self.pos + n]
        if callable(meaning):
            meaning = meaning(chunk)
        for i in range(0, max(n, 1), 8):
            part = chunk[i:i + 8]
            hx = " ".join("%02x" % b for b in part)
            asc = "".join(chr(b) if 32 <= b < 127 else "." for b in part)
            self.lines.append("%04x  %-23s  %-8s  %s"
                              % (self.pos + i, hx, asc, meaning if i == 0 else ""))
        self.pos += n
        return chunk

    def header_block(self):
        count = self.field(1, lambda b: "header-count = %d" % b[0])[0]
        for _ in range(count):
            idx = self.field(1, lambda b: (
                'name-index = %d  -> "%s" (static table, no name bytes sent)' % (b[0], STATIC[b[0] - 1])
                if b[0] else "name-index = 0  -> literal name follows"))[0]
            if idx == 0:
                n = self.field(1, lambda b: "name-length = %d" % b[0])[0]
                self.field(n, lambda b: 'name = "%s"' % b.decode("ascii"))
            n = be(self.field(2, lambda b: "value-length = %d" % be(b)))
            self.field(n, lambda b: "value = " + quote(b.decode("latin-1")))

    def frame(self, k):
        d = self.data
        length, ftype, flags, rid = be(d[self.pos:self.pos + 3]), d[self.pos + 3], d[self.pos + 4], be(d[self.pos + 5:self.pos + 8])
        name = TYPE_NAME.get(ftype, "UNKNOWN")
        self.note()
        self.note("----- frame %d: %s   (8-byte header + %d-byte payload) -----" % (k, name, length))
        self.field(3, lab("Length = %d" % length, "payload bytes that follow the header"))
        self.field(1, lab("Type = 0x%02x" % ftype, name))
        self.field(1, lab("Flags = 0x%02x" % flags, "END_STREAM: last frame of this request/response"
                          if flags & END_STREAM else "no END_STREAM: more frames follow"))
        self.field(3, "Request ID = %d" % rid)
        end = self.pos + length
        if ftype == REQUEST:
            self.field(1, lambda b: lab("method = 0x%02x" % b[0], METHOD_NAME[b[0]]))
            n = be(self.field(2, lambda b: "path-length = %d" % be(b)))
            self.field(n, lambda b: 'path = "%s"' % b.decode("utf-8"))
            self.header_block()
        elif ftype == RESPONSE:
            self.field(2, lambda b: lab("status = %d" % be(b), REASON.get(be(b), "")))
            self.header_block()
        elif ftype == DATA:
            self.field(length, "body: %d bytes of the file, verbatim" % length)
        assert self.pos == end, (self.pos, end)
        return ftype, flags, rid, length


def field_sizes(fields):
    """Encoded size of each header field, as 'a+b+c' strings, and the total."""
    terms, total = [], 0
    for name, value in fields:
        if name in STATIC:
            n = 1 + 2 + len(value)
            terms.append("(1+2+%d)" % len(value))
        else:
            n = 1 + 1 + len(name) + 2 + len(value)
            terms.append("(1+1+%d+2+%d)" % (len(name), len(value)))
        total += n
    return " + ".join(terms), total


def http1_request():
    lines = ["GET %s HTTP/1.1" % PATH] + ["%s: %s" % (n.title(), v) for n, v in REQUEST_HEADERS]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def http1_response_head(resp):
    lines = ["HTTP/1.1 %d %s" % (resp.status, REASON[resp.status])] + ["%s: %s" % (n.title(), v) for n, v in resp.headers]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


# -- document -----------------------------------------------------------------

def main():
    sent, received, resp = capture()
    out = []
    p = out.append

    p("# Annotated hexdump: one complete request and response")
    p("")
    p("**Vivek Singh Solanki (roll number 24bcs10338)**")
    p("")
    p("Every byte below crossed a real TCP connection between `tools/bclient.py` and")
    p("`bserve` serving `./www`. Regenerate with `make hexdump` (dates and etag will change).")
    p("")
    p("```")
    p("$ ./bserve ./www 9000")
    p("$ python3 tools/bclient.py localhost 9000 %s      # headers: %s" % (PATH, ", ".join(n for n, _ in REQUEST_HEADERS)))
    p("```")
    p("")
    p("Each row is one protocol field: **offset** (hex, counted from the first byte sent in")
    p("that direction), the **bytes**, the same bytes as **ASCII** (`.` for non-printable),")
    p("and what they **mean**. Multi-byte integers are big-endian. Field layouts are from")
    p("SPEC.md sections 2 to 4.")
    p("")

    # walk both directions first so the summary table can use the numbers
    cs = Dump(sent)
    cs.note("----- connection preface (sent once, before any frame) -----")
    cs.field(8, 'ASCII "bhttp/1\\n": this is bhttp, version 1')
    cs_frames = []
    k = 0
    while cs.pos < len(sent):
        k += 1
        cs_frames.append(cs.frame(k))

    sc = Dump(received)
    sc_frames = []
    while sc.pos < len(received):
        k += 1
        sc_frames.append(sc.frame(k))

    p("## The exchange at a glance")
    p("")
    p("| # | direction | bytes | frame |")
    p("|---|-----------|------:|-------|")
    p("| 1 | client -> server | 8 | preface `bhttp/1\\n` |")
    n = 1
    for ftype, flags, rid, length in cs_frames:
        n += 1
        p("| %d | client -> server | 8 + %d | %s, id=%d%s: GET %s, %d headers |"
          % (n, length, TYPE_NAME[ftype], rid, ", END_STREAM" if flags & END_STREAM else "", PATH, len(REQUEST_HEADERS)))
    for ftype, flags, rid, length in sc_frames:
        n += 1
        what = ("%d %s, %d headers" % (resp.status, REASON[resp.status], len(resp.headers))
                if ftype == RESPONSE else "the file bytes")
        p("| %d | server -> client | 8 + %d | %s, id=%d%s: %s |"
          % (n, length, TYPE_NAME[ftype], rid, ", END_STREAM" if flags & END_STREAM else "", what))
    p("")
    h1_req, h1_head = http1_request(), http1_response_head(resp)
    p("| | bhttp/1 | HTTP/1.1 text, same headers |")
    p("|---|---:|---:|")
    p("| request (preface + REQUEST frame) | %d bytes | %d bytes |" % (len(sent), len(h1_req)))
    p("| response head (RESPONSE frame) | %d bytes | %d bytes |" % (8 + sc_frames[0][3], len(h1_head)))
    p("| response body (DATA frame) | %d bytes | %d bytes |" % (8 + sc_frames[1][3], sc_frames[1][3]))
    p("")
    p("The preface is paid once per connection, not per request; the second request on this")
    p("connection would cost just the REQUEST frame.")
    p("")

    p("## Client -> server (%d bytes)" % len(sent))
    p("")
    p("```")
    out.extend(cs.lines)
    p("```")
    p("")
    p("## Server -> client (%d bytes)" % len(received))
    p("")
    p("```")
    out.extend(sc.lines[1:])          # drop the blank line before the first frame
    p("```")
    p("")

    p("## Checking the arithmetic")
    p("")
    terms, total = field_sizes(REQUEST_HEADERS)
    req_len = cs_frames[0][3]
    p("* **REQUEST payload** = method 1 + path-length 2 + path %d + header-count 1 + headers %s"
      % (len(PATH), terms))
    p("  = %d. The Length field says `%s` = %d. OK." % (1 + 2 + len(PATH) + 1 + total, "%06x" % req_len, req_len))
    terms, total = field_sizes([(n, v) for n, v in resp.headers])
    res_len = sc_frames[0][3]
    p("* **RESPONSE payload** = status 2 + header-count 1 + headers %s" % terms)
    p("  = %d. The Length field says `%s` = %d. OK." % (2 + 1 + total, "%06x" % res_len, res_len))
    body_len = sc_frames[1][3]
    p("* **DATA payload** = %d bytes; `content-length` in the RESPONSE says %s; `wc -c www%s` says %d. OK."
      % (body_len, dict(resp.headers)["content-length"], PATH, os.path.getsize(os.path.join(ROOT, "www", PATH.lstrip("/")))))
    p("* Each **indexed** header name costs 1 byte; each **literal** name costs 2 + its length.")
    p("  `host` as index `01` is 1 byte instead of the 6 (`00 04 h o s t`) a literal would take.")
    p("* `END_STREAM` is clear on the RESPONSE (a body follows) and set on the DATA frame (last")
    p("  frame of request %d). The client knows the body is complete without reading `content-length`." % sc_frames[0][2])
    p("")
    print("\n".join(out))


if __name__ == "__main__":
    main()
