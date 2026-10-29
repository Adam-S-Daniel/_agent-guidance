from src.app import greet


def test_greet():
    assert greet("Ada").startswith("Hello, Ada")
