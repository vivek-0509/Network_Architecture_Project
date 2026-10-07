#!/usr/bin/env python3
"""
bclient - a minimal bhttp/1 client written from SPEC.md, independently of bserve.
Author: Vivek Singh Solanki (roll number 24bcs10338).

It exists so the server can be tested against a *second* implementation of the
spec, and so tools/make_hexdump.py can capture real bytes.  It is not the
Track 2 deliverable (bcurl): no -v, no URL parsing, only what the tests need.

    python3 tools/bclient.py HOST PORT PATH [PATH ...]

Fetches every PATH over ONE connection, writes each body to stdout, prints one
status line per path to stderr, and exits non-zero if any status was >= 400.
"""

import collections
import socket
import struct
import sys

PREFACE = b"bhttp/1\n"
MAX_FRAME_SIZE = 16384
REQUEST, RESPONSE, DATA = 0x01, 0x02, 0x03
END_STREAM = 0x01
METHOD = {"GET": 1, "HEAD": 2, "POST": 3, "PUT": 4, "DELETE": 5, "OPTIONS": 6}
STATIC = ["host", "user-agent", "accept", "if-none-match", "content-type",
          "content-length", "server", "date", "last-modified", "etag"]

DEFAULT_HEADERS = [("host", "localhost"), ("user-agent", "bclient/1.0"), ("accept", "*/*")]

Frame = collections.namedtuple("Frame", "type flags rid payload")
Response = collections.namedtuple("Response", "status headers body frames")


# -- encoding -----------------------------------------------------------------

def frame(ftype, flags, rid, payload=b""):
    """8-byte header: Length(24) Type(8) | Flags(8) RequestID(24), big-endian."""
    return struct.pack("!II", (len(payload) << 8) | ftype, (flags << 24) | rid) + payload


def header_block(fields):
    out = bytearray([len(fields)])
    for name, value in fields:
        name = name.lower()
        if name in STATIC:
            out.append(STATIC.index(name) + 1)          # indexed name
        else:
            nb = name.encode("ascii")                   # literal name
            out.append(0)
            out.append(len(nb))
            out += nb
        vb = value.encode("utf-8") if isinstance(value, str) else value
        out += struct.pack("!H", len(vb)) + vb
    return bytes(out)


def request_payload(method, path, fields):
    pb = path.encode("utf-8")
    return bytes([METHOD[method]]) + struct.pack("!H", len(pb)) + pb + header_block(fields)


# -- decoding -----------------------------------------------------------------

def parse_header_block(buf, pos):
    count = buf[pos]
    pos += 1
    fields = []
    for _ in range(count):
        idx = buf[pos]
        pos += 1
        if idx == 0:
            n = buf[pos]
            pos += 1
            name = buf[pos:pos + n].decode("ascii")
            pos += n
        else:
            name = STATIC[idx - 1]
        n = struct.unpack("!H", buf[pos:pos + 2])[0]
        pos += 2
        fields.append((name, buf[pos:pos + n].decode("latin-1")))
        pos += n
    return fields, pos


def recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return bytes(buf)


def read_frame(sock, sink=None):
    """Read one frame from a socket.  Returns a Frame, or None if the peer
    closed.  `sink`, if given, receives the raw bytes (used for capture)."""
    hdr = recv_exact(sock, 8)
    if len(hdr) < 8:
        return None
    w0, w1 = struct.unpack("!II", hdr)
    length, ftype, flags, rid = w0 >> 8, w0 & 0xFF, w1 >> 24, w1 & 0xFFFFFF
    payload = recv_exact(sock, length)
    if sink is not None:
        sink += hdr + payload
    if len(payload) < length:
        return None
    return Frame(ftype, flags, rid, payload)


# -- client -------------------------------------------------------------------

class Client:
    def __init__(self, host, port, timeout=5.0, preface=True):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.next_id = 1
        self.sent = bytearray()         # every byte we wrote   (for the hexdump)
        self.received = bytearray()     # every byte we read
        if preface:
            self.send(PREFACE)

    def send(self, data):
        self.sock.sendall(data)
        self.sent += data

    def read_frame(self):
        return read_frame(self.sock, self.received)

    def request(self, method="GET", path="/", headers=None, body=None, rid=None):
        """Send one request, then read its whole response.
        Returns a Response, or None if the server closed first."""
        if headers is None:
            headers = DEFAULT_HEADERS
        if rid is None:
            rid = self.next_id
            self.next_id += 1
        self.send(frame(REQUEST, 0 if body is not None else END_STREAM, rid,
                        request_payload(method, path, headers)))
        if body is not None:
            chunks = [body[i:i + MAX_FRAME_SIZE] for i in range(0, len(body), MAX_FRAME_SIZE)] or [b""]
            for i, c in enumerate(chunks):
                self.send(frame(DATA, END_STREAM if i == len(chunks) - 1 else 0, rid, c))
        return self.read_response(rid)

    def read_response(self, rid):
        frames, status, headers, body = [], None, None, bytearray()
        while True:
            f = self.read_frame()
            if f is None:
                return None
            frames.append(f)
            if f.type == RESPONSE and f.rid == rid:
                status = struct.unpack("!H", f.payload[:2])[0]
                headers, _ = parse_header_block(f.payload, 2)
                if f.flags & END_STREAM:
                    break
            elif f.type == DATA and f.rid == rid and status is not None:
                body += f.payload
                if f.flags & END_STREAM:
                    break
            # anything else (unknown type, other id) is skipped: SPEC.md 2
        return Response(status, headers, bytes(body), frames)

    def close(self):
        self.sock.close()


def main(argv):
    if len(argv) < 4:
        sys.stderr.write("usage: bclient.py HOST PORT PATH [PATH ...]\n")
        return 2
    host, port, paths = argv[1], int(argv[2]), argv[3:]
    c = Client(host, port)
    worst = 0
    for p in paths:
        r = c.request("GET", p)
        if r is None:
            sys.stderr.write("%s: connection closed by server\n" % p)
            return 2
        sys.stderr.write("%s -> %d (%d bytes)\n" % (p, r.status, len(r.body)))
        sys.stdout.buffer.write(r.body)
        sys.stdout.flush()
        worst = max(worst, r.status)
    c.close()
    return 1 if worst >= 400 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
