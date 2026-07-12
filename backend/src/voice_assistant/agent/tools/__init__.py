"""Tool implementations, each registering itself with ``registry`` on import.

``session.py`` imports this package (which imports every tool module below) so
``registry.definitions()`` is fully populated before it is read."""
