from app.providers.component import ComponentProvider


class CatalogComponentProvider(ComponentProvider):
    """In-memory fixture for unrelated pipeline behavior, never used by the app."""
    name = "local-component-catalog"
    def __init__(self, profiles):
        self.profiles = {p.component_model: p for p in profiles}
    def lookup(self, model):
        return self.profiles.get(model)
