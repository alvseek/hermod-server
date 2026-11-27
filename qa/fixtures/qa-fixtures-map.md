# Hermod fixtures · 2026-11-27

[Real-client tests](../../tests/test_end_to_end.py) start a temporary HTTP server. Their disposable-store [schema fixture](../../tests/valaskjalf_schema.py) drops and recreates the supplied `public` schema. It must not be pointed at either sibling bench.
