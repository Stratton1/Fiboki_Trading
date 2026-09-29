"""Composition roots: the reviewed places where a running process is assembled.

``fiboki worker run live`` refuses to build a market-facing worker from command
line flags, because "a live worker assembled from command line flags is a live
worker whose risk configuration nobody reviewed". This package is where such a
worker IS assembled: from a committed, versioned wiring file whose content hash
is stamped on every decision it makes.

It sits above ``workers`` and ``api`` in ``tests/unit/test_layering.py`` and is
imported only by ``cli.py``. It is also the only package (with ``cli.py``) that
may construct :class:`fiboki.broker.http_transport.HttpxTransport`, the one
object in ``src/`` that can reach the network on a venue's behalf.
"""
