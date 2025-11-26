from django.apps import AppConfig


class JobtrackerAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'jobtracker_app'

    def ready(self):
        from . import signals  # noqa: F401
