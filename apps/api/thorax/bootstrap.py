from thorax.application.access import AccessPolicy
from thorax.application.admin import AdminService
from thorax.application.ai_jobs import AIJobService
from thorax.application.auth import AuthService
from thorax.application.context import ContextBuilder
from thorax.application.ports import Clock
from thorax.application.reading import ReadingService
from thorax.domain.errors import DomainError
from thorax.infrastructure.db import Database
from thorax.infrastructure.image_store import ImageStore
from thorax.infrastructure.inference_http import HTTPInferenceProvider
from thorax.worker.main import Worker


class Container:
    def __init__(self, settings, *, database=None, provider=None, clock=None):
        settings.validate()
        self.settings = settings
        self.database = database or Database(settings.database_url)
        self.clock, self.access = clock or Clock(), AccessPolicy()
        self.provider = provider or HTTPInferenceProvider(settings)
        self.images = ImageStore(settings.image_root, settings.policy["limits"])
        self.auth = AuthService(self.database, settings, self.clock)
        try:
            capabilities = self.provider.capabilities()
        except DomainError:
            capabilities = {"provider_mode": settings.provider_mode, "ready": False, "can_detect": False, "can_ask_roi": False, "model_artifacts": []}
        self.reading = ReadingService(self.database, settings, self.clock, self.access, self.images, capabilities)
        self.context = ContextBuilder(settings, self.reading)
        self.jobs = AIJobService(self.database, settings, self.clock, self.access, self.context, self.provider)
        self.admin = AdminService(self.database, settings, self.clock, self.access, self.auth, self.images)
        self.worker = Worker(self.database, settings, self.clock, self.access, self.provider, self.images)

    def close(self):
        self.database.close()
