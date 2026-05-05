class IngestionError(Exception):
    pass


class UnsupportedFormat(IngestionError):
    pass


class ExtractionFailed(IngestionError):
    pass


class FetchFailed(IngestionError):
    pass


class KeyMissing(IngestionError):
    pass
