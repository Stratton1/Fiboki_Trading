"""Broker layer: adapters, the crash-survivable execution service, mode guard.

Adapters convert units and speak to venues. They MUST NOT size and MUST NOT
construct risk decisions -- both were V1 failure modes.
"""
