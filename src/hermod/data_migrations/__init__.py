"""One-shot data migrations for Hermod's store.

Tools that move data between the overlay's storage shapes, kept beside the store they write
rather than shipped as part of the server: the server reads and writes the store, and nothing it
serves imports a markdown tree.
"""
