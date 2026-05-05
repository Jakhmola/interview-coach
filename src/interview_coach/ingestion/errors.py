class IngestionError(Exception):
    pass


class UnsupportedFormat(IngestionError):
    pass


class ExtractionFailed(IngestionError):
    pass
