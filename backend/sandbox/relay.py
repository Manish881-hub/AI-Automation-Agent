"""TCP relay 0.0.0.0:RELAY_PORT -> 127.0.0.1:SERVER_PORT (stdlib only).

The fixture server binds 127.0.0.1, unreachable outside its own network
namespace. This relay (baked into the sandbox image, NOT the fixture)
exposes the app on all container interfaces so the host browser can
reach it, without touching the fixture's bind behavior.

Single-threaded selectors loop: no thread-per-connection growth, safe
under tight PID limits.
"""
import selectors
import socket
import sys

RELAY_PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
SERVER_PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8921

sel = selectors.DefaultSelector()
peers: dict[socket.socket, socket.socket] = {}


def close_pair(sock):
    peer = peers.pop(sock, None)
    if peer is not None:
        peers.pop(peer, None)
        for s in (sock, peer):
            try:
                sel.unregister(s)
            except (KeyError, ValueError):
                pass
            try:
                s.close()
            except OSError:
                pass


def accept(srv):
    try:
        client, _ = srv.accept()
    except OSError:
        return
    client.setblocking(False)
    try:
        upstream = socket.create_connection(("127.0.0.1", SERVER_PORT), timeout=10)
        upstream.setblocking(False)
    except OSError:
        client.close()
        return
    peers[client] = upstream
    peers[upstream] = client
    sel.register(client, selectors.EVENT_READ)
    sel.register(upstream, selectors.EVENT_READ)


srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("0.0.0.0", RELAY_PORT))
srv.listen(64)
srv.setblocking(False)
sel.register(srv, selectors.EVENT_READ)

while True:
    for key, _ in sel.select(timeout=30):
        if key.fileobj is srv:
            accept(srv)
            continue
        sock = key.fileobj
        peer = peers.get(sock)
        if peer is None:
            continue
        try:
            chunk = sock.recv(65536)
        except OSError:
            chunk = b""
        if not chunk:
            close_pair(sock)
            continue
        try:
            peer.sendall(chunk)
        except OSError:
            close_pair(sock)
