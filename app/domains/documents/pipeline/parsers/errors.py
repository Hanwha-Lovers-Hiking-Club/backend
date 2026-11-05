class ParseError(ValueError):
    """Machine-readable ingestion failure; HTTP/SSE mapping belongs to documents.service."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
