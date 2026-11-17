class WarehouseError(Exception):
    pass


class SchemaError(WarehouseError):
    pass


class PayloadError(WarehouseError):
    pass


class Conflict(WarehouseError):
    pass


class NotFound(WarehouseError):
    pass


class ResolutionError(WarehouseError):
    def __init__(self, code, path, message):
        self.code, self.path, self.message = code, path, message
        super().__init__(f"{code} at {path}: {message}")
