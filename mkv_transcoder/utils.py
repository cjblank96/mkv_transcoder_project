"""General utility functions for the MKV Transcoder project."""

def quote_path(path):
    """Unconditionally wraps a file path in double quotes to handle spaces."""
    return f'"{path}"'
